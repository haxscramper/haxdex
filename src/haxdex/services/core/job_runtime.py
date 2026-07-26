from __future__ import annotations

from collections import deque
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from graphlib import TopologicalSorter
from pathlib import Path

from beartype import beartype
from beartype.typing import Sequence, Optional
from graphviz import Digraph
from time import monotonic

from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_cache import JobCache
from haxdex.services.core.job_types import BaseIndexer, BaseResource, META_SUFFIX, RunContext
from haxdex.services.core.types import (
    CannotProcess,
    FileHash,
    FileRef,
    IndexerOutput,
    IndexerOutputError,
    IndexerRequest,
    IndexerResultKind,
    MissingAssets,
    RootRef,
)
from haxdex.services.utils import ExceptionContextNote

log = logging.getLogger(__name__)


@beartype
@dataclass(frozen=True)
class PlannedIndexerBatch:
    indexer_name: str
    file_refs: list[FileRef]
    sub_batches: list[list[FileRef]]
    window_id: int
    cached_outputs: list[tuple[FileRef, IndexerOutput]]


@beartype
@dataclass(frozen=True)
class ExecutionPlan:
    batches: list[PlannedIndexerBatch]
    windows: list[list[str]]
    dependencies: dict[str, tuple[str, ...]]

    def total_runs(self) -> int:
        return sum(
            len(batch.file_refs) for batch in self.batches if len(batch.sub_batches) != 0)

    def get_indexer_names(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for batch in self.batches:
            if batch.indexer_name not in seen:
                seen.add(batch.indexer_name)
                names.append(batch.indexer_name)
        return names

    def to_text(self) -> str:
        lines: list[str] = []
        lines.append("ExecutionPlan")
        lines.append("  windows: " + str(len(self.windows)))
        for window_id, names in enumerate(self.windows):
            lines.append("    - window " + str(window_id) + ": " + ", ".join(names))

        lines.append("  batches: " + str(len(self.batches)))
        for batch_idx, batch in enumerate(self.batches, start=1):
            lines.append("    - batch " + str(batch_idx) + "/" + str(len(self.batches)) +
                         ": indexer=" + batch.indexer_name + ", window=" +
                         str(batch.window_id) + ", files=" + str(len(batch.file_refs)) +
                         ", sub_batches=" + str(len(batch.sub_batches)) +
                         ", cached_outputs=" + str(len(batch.cached_outputs)))
            for sub_idx, sub in enumerate(batch.sub_batches, start=1):
                lines.append("      - sub_batch " + str(sub_idx) + "/" +
                             str(len(batch.sub_batches)) + ": size=" + str(len(sub)))
        return "\n".join(lines)

    def to_graphviz(self) -> Digraph:
        dot = Digraph("index_execution_plan")
        dot.attr(rankdir="LR")

        for window_id, names in enumerate(self.windows):
            cluster_name = "cluster_window_" + str(window_id)
            with dot.subgraph(name=cluster_name) as sub:
                sub.attr(label="window " + str(window_id))
                sub.attr(rank="same")
                for name in names:
                    sub.node(name, label=name)

        for name in self.dependencies:
            dot.node(name, label=name)

        for node, deps in self.dependencies.items():
            for dep in deps:
                dot.edge(dep, node)

        return dot


@beartype
class IndexRuntime:

    def __init__(
        self,
        ctx: RunContext,
        db: IndexDatabase,
        indexer_types: Sequence[BaseIndexer] = list(),
        resource_types: Sequence[BaseResource] = list(),
        cache: Optional[JobCache] = None,
    ) -> None:
        self.db = db
        self.ctx = ctx
        self.cache = cache or JobCache()
        self._resource_instances: dict[str, BaseResource] = {
            inst.resource_key: inst for inst in resource_types
        }
        self._indexer_instances: dict[str, BaseIndexer] = {
            inst.asset_name: inst for inst in indexer_types
        }

        self.db.ensure_collections(list(self._indexer_instances.values()))  # type: ignore
        self._indexer_order = self._compute_order()

    def _compute_order(self) -> list[str]:
        ts: TopologicalSorter[str] = TopologicalSorter()
        for name, idx in self._indexer_instances.items():
            ts.add(name, *idx.required_assets)
        return list(ts.static_order())

    def _compute_layers(self, requested: set[str]) -> list[list[str]]:
        ts: TopologicalSorter[str] = TopologicalSorter()
        for name, idx in self._indexer_instances.items():
            if name in requested:
                ts.add(name, *[dep for dep in idx.required_assets if dep in requested])

        layers: list[list[str]] = []
        ts.prepare()
        while ts.is_active():
            ready = list(ts.get_ready())
            if ready:
                layers.append(sorted(ready))
                ts.done(*ready)
        return layers

    def _expand_requested(self, names: list[str]) -> set[str]:
        needed: set[str] = set()
        stack = list(names)
        while stack:
            name = stack.pop()
            if name in needed:
                continue
            needed.add(name)
            stack.extend(self._indexer_instances[name].required_assets)
        return needed

    def _resource_closure(self, roots: tuple[str, ...]) -> set[str]:
        out: set[str] = set()
        stack = list(roots)
        while stack:
            name = stack.pop()
            if name in out:
                continue
            out.add(name)
            stack.extend(self._resource_instances[name].required_resources)
        return out

    def _resources_for_indexer(self, indexer: BaseIndexer) -> dict[str, BaseResource]:
        names = self._resource_closure(indexer.required_resources)
        return {name: self._resource_instances[name] for name in names}

    def can_share_batch(self, left: str, right: str) -> bool:
        left_sig = self._exclusive_signature(left)
        right_sig = self._exclusive_signature(right)

        for res_name in set(left_sig.keys()) & set(right_sig.keys()):
            if left_sig[res_name] != right_sig[res_name]:
                return False
        return True

    def _exclusive_signature(self, indexer_name: str) -> dict[str, str]:
        indexer = self._indexer_instances[indexer_name]
        consumers: dict[str, set[str]] = {}

        def walk(resource_name: str, consumer_name: str) -> None:
            res = self._resource_instances[resource_name]
            if res.exclusive:
                consumers.setdefault(resource_name, set()).add(consumer_name)
            for dep in res.required_resources:
                walk(dep, resource_name)

        for rname in indexer.required_resources:
            walk(rname, indexer_name)

        normalized: dict[str, str] = {}
        for exclusive_resource, direct_users in consumers.items():
            if len(direct_users) == 1:
                normalized[exclusive_resource] = next(iter(direct_users))
            else:
                normalized[exclusive_resource] = f"__multi__:{indexer_name}"
        return normalized

    def _group_layer_into_windows(self, layer: list[str]) -> list[list[str]]:
        windows: list[tuple[list[str], dict[str, str]]] = []

        for name in layer:
            signature = self._exclusive_signature(name)
            placed = False
            for names, locks in windows:
                compatible = True
                for res_name, consumer_key in signature.items():
                    if res_name in locks and locks[res_name] != consumer_key:
                        compatible = False
                        break
                if compatible:
                    names.append(name)
                    for res_name, consumer_key in signature.items():
                        locks.setdefault(res_name, consumer_key)
                    placed = True
                    break

            if not placed:
                windows.append(([name], dict(signature)))

        return [names for names, _ in windows]

    def build_windows(self, names: list[str]) -> list[list[str]]:
        requested = self._expand_requested(names)
        layers = self._compute_layers(requested)
        windows: list[list[str]] = []
        for layer in layers:
            windows.extend(self._group_layer_into_windows(layer))
        return windows

    def _is_success_output(self, output: IndexerOutput) -> bool:
        match output.result_kind:
            case IndexerResultKind.DOCUMENT:
                return True
            case IndexerResultKind.MISSING_ASSETS:
                return False
            case IndexerResultKind.CANNOT_PROCESS:
                return False
            case IndexerResultKind.ERROR:
                return False
        raise ValueError(
            f"unsupported result kind for {output.indexer_id}: {output.result_kind}")

    def load_meta_cache_for_root(self, root: RootRef, meta_files: list[Path]) -> None:
        for meta_path in meta_files:
            if not str(meta_path).endswith(META_SUFFIX):
                raise ValueError(
                    f"unexpected non-meta file passed for cache preload: {meta_path}")
            source_path = Path(str(meta_path)[:len(str(meta_path)) - len(META_SUFFIX)])
            if not source_path.exists():
                raise ValueError(
                    f"meta cache file '{meta_path}' points to missing source file '{source_path}'"
                )
            ref = self.db.as_ref(root, source_path)
            payload = json.loads(meta_path.read_text())
            if "indexers" not in payload:
                raise ValueError(
                    f"meta file '{meta_path}' does not contain required 'indexers'")
            indexers = payload["indexers"]
            if not isinstance(indexers, dict):
                raise TypeError(
                    f"meta file '{meta_path}' has invalid 'indexers' type {type(indexers)}"
                )
            self.cache.register_meta_results(ref, indexers)

    def create_plan(self, files: list[FileRef], names: list[str]) -> ExecutionPlan:
        windows = self.build_windows(names)
        requested = {name for window in windows for name in window}
        dependencies = {
            name:
                tuple(dep
                      for dep in self._indexer_instances[name].required_assets
                      if dep in requested) for name in sorted(requested)
        }

        planned: list[PlannedIndexerBatch] = []

        for window_id, window in enumerate(windows):
            for name in window:
                indexer = self._indexer_instances[name]
                stage_files: list[FileRef] = []
                hydrate_outputs: list[tuple[FileRef, IndexerOutput]] = []

                for ref in files:
                    file_path = self.db.get_path(ref)

                    if not indexer.can_run(file_path):
                        cannot_process = IndexerOutput(
                            indexer_id=name,
                            result_kind=IndexerResultKind.CANNOT_PROCESS,
                            result=CannotProcess(
                                reason=
                                f"indexer '{name}' cannot process file '{file_path}'"),
                        )
                        self.cache.store_output(ref, cannot_process)
                        continue

                    has_db_result = self.db.has_indexer_result(
                        ref,
                        name,
                        short_circuit_this_check=True,
                    )
                    if has_db_result:
                        continue

                    cached_result = self.cache.get_output(ref, name)
                    if cached_result is None:
                        stage_files.append(ref)
                        continue

                    if self._is_success_output(cached_result):
                        hydrate_outputs.append((ref, cached_result))

                if hydrate_outputs:
                    planned.append(
                        PlannedIndexerBatch(
                            indexer_name=name,
                            file_refs=[ref for ref, _ in hydrate_outputs],
                            sub_batches=[],
                            window_id=window_id,
                            cached_outputs=hydrate_outputs,
                        ))

                if stage_files:
                    chunk_size = max(1, indexer.max_parallel)
                    sub_batches = [
                        stage_files[i:i + chunk_size]
                        for i in range(0, len(stage_files), chunk_size)
                    ]
                    planned.append(
                        PlannedIndexerBatch(
                            indexer_name=name,
                            file_refs=stage_files,
                            sub_batches=sub_batches,
                            window_id=window_id,
                            cached_outputs=[],
                        ))

        return ExecutionPlan(batches=planned, windows=windows, dependencies=dependencies)

    def execute_plan(self, plan: ExecutionPlan) -> None:
        total_batches = len(plan.batches)
        plan_started_at = monotonic()

        for batch_idx, batch in enumerate(plan.batches, start=1):
            completed_batches = batch_idx - 1
            elapsed = monotonic() - plan_started_at

            if 0 < completed_batches and 0 < elapsed:
                batches_per_min = (completed_batches / elapsed) * 60.0
                eta_plan_sec = ((total_batches - completed_batches) /
                                (batches_per_min / 60.0)
                                if 0 < batches_per_min else float("inf"))
                batches_per_min_str = f"{batches_per_min:.2f}"
                eta_plan_str = f"{eta_plan_sec:.1f}s"
            else:
                batches_per_min_str = "n/a"
                eta_plan_str = "unknown"

            log.debug(
                "batch {}/{}: indexer={} window={} files={} sub_batches={} cached_outputs={} batches_per_min={} eta_plan={}"
                .format(
                    batch_idx,
                    total_batches,
                    batch.indexer_name,
                    batch.window_id,
                    len(batch.file_refs),
                    len(batch.sub_batches),
                    len(batch.cached_outputs),
                    batches_per_min_str,
                    eta_plan_str,
                ))

            with self.ctx.trace_scope(
                    "execute batch",
                    batch=batch_idx,
                    total_batches=total_batches,
                    indexer=batch.indexer_name,
                    window=batch.window_id,
                    files=len(batch.file_refs),
                    sub_batches=len(batch.sub_batches),
                    cached_outputs=len(batch.cached_outputs),
            ):
                self._run_indexer_batch(batch)

    def truncate_all(self) -> None:
        self.db.truncate_all(list(self._indexer_instances.keys()))

    def run_indexers(self, files: list[FileRef], names: list[str]) -> None:
        with self.ctx.trace_scope("plan construction",
                                  files=len(files),
                                  indexers=len(names)):
            plan = self.create_plan(files, names)

        with self.ctx.trace_scope(
                "plan execution",
                batches=len(plan.batches),
                total_runs=plan.total_runs(),
        ):
            self.execute_plan(plan)

    def get_indexer(self, name: str) -> BaseIndexer:
        return self._indexer_instances[name]

    def get_indexer_result(self, hash: FileHash | FileRef, name: str) -> IndexerOutput:
        assert name in self._indexer_instances
        assert self.get_indexer(name).result_model
        return IndexerOutput(
            indexer_id=name,
            result=self.db.get_indexer_result(
                hash if isinstance(hash, FileHash) else hash.hash,
                self.get_indexer(name),
            ),
        )

    def run_indexer(self, file: FileRef, names: list[str]):
        self.run_indexers([file], names)

    def _run_indexer_batch(self, batch: PlannedIndexerBatch) -> None:
        indexer = self._indexer_instances[batch.indexer_name]

        if batch.cached_outputs:
            with self.ctx.trace_scope("hydrate cached indexer outputs",
                                      indexer=indexer.asset_name):
                for ref, output in batch.cached_outputs:
                    if self._is_success_output(output):
                        self.db.store_indexer_output(ref, output)
            return

        resources = self._resources_for_indexer(indexer)

        def work(ref: FileRef) -> Optional[tuple[FileRef, IndexerOutput]]:
            assets: dict[str, IndexerOutput | None] = {}
            for name in indexer.required_assets:
                has_db_result = self.db.has_indexer_result(
                    ref,
                    name,
                    short_circuit_this_check=True,
                )
                if has_db_result:
                    assets[name] = self.get_indexer_result(ref.hash, name)
                else:
                    assets[name] = self.cache.get_output(ref, name)

            missing_asset_names = [
                name for name, value in assets.items() if value is None
            ]
            if missing_asset_names:
                missing_output = IndexerOutput(
                    indexer_id=indexer.asset_name,
                    result_kind=IndexerResultKind.MISSING_ASSETS,
                    result=MissingAssets(
                        assets=missing_asset_names,
                        reason=
                        f"missing required assets for indexer '{indexer.asset_name}'",
                    ),
                )
                return ref, missing_output

            request = IndexerRequest(file_ref=ref, dependency_results=assets)

            with (
                    ExceptionContextNote(
                        f"running indexer '{indexer.asset_name}' for '{self.db.get_path(ref)} {ref.hash}'"
                    ),
                    ExceptionContextNote(f"request {request}"),
                    self.ctx.trace_scope("index", file=str(self.db.get_path(ref))),
            ):
                out = indexer.run(
                    ctx=self.ctx,
                    request=request,
                    resources=resources,  # type: ignore
                    assets=assets,  # type: ignore
                )

            if out.indexer_id != indexer.asset_name:
                raise ValueError(
                    f"indexer '{indexer.asset_name}' returned mismatched output indexer_id '{out.indexer_id}'"
                )

            return ref, out

        total_sub_batches = len(batch.sub_batches)
        recent_sub_batch_times: deque[float] = deque(maxlen=30)

        for sub_idx, chunk in enumerate(batch.sub_batches, start=1):
            remaining_sub_batches = total_sub_batches - sub_idx + 1

            if recent_sub_batch_times:
                average_sub_batch_sec = sum(recent_sub_batch_times) / len(
                    recent_sub_batch_times)
                sub_batches_per_sec = 1 / average_sub_batch_sec
                eta_batch_sec = remaining_sub_batches * average_sub_batch_sec
                sub_batches_per_sec_str = f"{sub_batches_per_sec:.2f}"
                eta_batch_str = f"{eta_batch_sec:.1f}s"
            else:
                sub_batches_per_sec_str = "n/a"
                eta_batch_str = "unknown"

            log.debug(
                "sub-batch {}/{}: indexer={} size={} sub_batches_per_sec={} eta_batch={}".
                format(
                    sub_idx,
                    total_sub_batches,
                    batch.indexer_name,
                    len(chunk),
                    sub_batches_per_sec_str,
                    eta_batch_str,
                ))

            sub_batch_started_at = monotonic()

            with self.ctx.trace_scope(
                    "execute sub-batch",
                    indexer=batch.indexer_name,
                    sub_batch=sub_idx,
                    total_sub_batches=total_sub_batches,
                    size=len(chunk),
            ):
                if 1 < len(chunk) and 1 < indexer.max_parallel:
                    with ThreadPoolExecutor(max_workers=indexer.max_parallel) as ex:
                        completed_1 = list(ex.map(work, chunk))
                else:
                    completed_1 = [work(ref) for ref in chunk]

                completed = [c for c in completed_1 if c is not None]

                for ref, out in completed:
                    with ExceptionContextNote(f"indexer asset: {indexer.asset_name}"):
                        self.cache.store_output(ref, out)
                        if self._is_success_output(out):
                            self.db.store_indexer_output(ref, out)

            recent_sub_batch_times.append(monotonic() - sub_batch_started_at)

        log.debug("finished indexer batch")
