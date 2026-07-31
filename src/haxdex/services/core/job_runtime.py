from __future__ import annotations

import json
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from graphlib import TopologicalSorter
from pathlib import Path
from time import monotonic, perf_counter

from beartype import beartype
from beartype.typing import Optional, Sequence
from graphviz import Digraph

from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_cache import (
    has_cached_result,
    load_cached_output,
    parse_indexer_output,
    store_cached_output,
)
from haxdex.services.core.job_types import (
    META_SUFFIX,
    BaseIndexer,
    BaseResource,
    RunContext,
)
from haxdex.services.core.types import (
    FileHash,
    FileRef,
    IndexerOutput,
    IndexerRequest,
    is_processed_result,
)
from haxdex.services.utils import ExceptionContextNote

log = logging.getLogger(__name__)


class ActionKind(str, Enum):
    skip = "skip"  # already present in Arango
    copy_cache = "copy_cache"  # in SQLite cache, not in Arango
    run_indexer = "run_indexer"  # missing in both


@beartype
@dataclass(frozen=True)
class PreparedFile:
    ref: FileRef
    path: Path
    arango_indexers: frozenset[str]
    cached_indexers: frozenset[str]
    missing_indexers: frozenset[str]


@beartype
@dataclass(frozen=True)
class IndexerAction:
    file_ref: FileRef
    indexer_name: str
    kind: ActionKind


@beartype
@dataclass(frozen=True)
class PlannedIndexerBatch:
    indexer_name: str
    kind: ActionKind
    file_refs: list[FileRef]
    sub_batches: list[list[FileRef]]
    window_id: int


@beartype
@dataclass(frozen=True)
class ExecutionPlan:
    batches: list[PlannedIndexerBatch]
    windows: list[list[str]]
    dependencies: dict[str, tuple[str, ...]]

    def total_runs(self) -> int:
        return sum(len(batch.file_refs) for batch in self.batches)

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
                         ": indexer=" + batch.indexer_name + ", kind=" +
                         batch.kind.value + ", window=" + str(batch.window_id) +
                         ", files=" + str(len(batch.file_refs)) + ", sub_batches=" +
                         str(len(batch.sub_batches)))
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
    ) -> None:
        self.db = db
        self.ctx = ctx
        self._resource_instances: dict[str, BaseResource] = {
            inst.resource_key: inst for inst in resource_types
        }
        self._indexer_instances: dict[str, BaseIndexer] = {
            inst.asset_name: inst for inst in indexer_types
        }

        self.db.ensure_collections(list(self._indexer_instances.values()))  # type: ignore

        self._indexer_order = self._compute_order()

    def get_indexers(self, names: Optional[list[str]] = None) -> list[BaseIndexer]:
        return [
            self._indexer_instances[i]
            for i in (names if names is not None else self._indexer_order)
        ]

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

    def prepare_files(
        self,
        refs: list[FileRef],
        indexers: Sequence[BaseIndexer],
    ) -> list[PreparedFile]:
        """Classify each (file, indexer) pair against Arango and the SQLite
        cache without running any indexer. `can_run` filtering happens here,
        during plan construction."""
        requested = self._expand_requested([i.asset_name for i in indexers])
        prepared: list[PreparedFile] = []

        for ref in refs:
            path = self.db.get_path(ref)
            arango: set[str] = set()
            cached: set[str] = set()
            missing: set[str] = set()

            for name in sorted(requested):
                indexer = self._indexer_instances[name]

                if not indexer.can_run(path):
                    continue

                if self.db.has_indexer_result(ref,
                                              indexer.asset_name,
                                              short_circuit_this_check=True):
                    arango.add(name)
                elif has_cached_result(indexer, ref.hash.hash):
                    cached.add(name)
                else:
                    missing.add(name)

            prepared.append(
                PreparedFile(
                    ref=ref,
                    path=path,
                    arango_indexers=frozenset(arango),
                    cached_indexers=frozenset(cached),
                    missing_indexers=frozenset(missing),
                ))

        return prepared

    def build_actions(
        self,
        prepared: list[PreparedFile],
        indexers: Sequence[BaseIndexer],
    ) -> list[IndexerAction]:
        """Materialize the NxM file x indexer action grid."""
        requested = self._expand_requested([i.asset_name for i in indexers])
        actions: list[IndexerAction] = []

        for pf in prepared:
            for name in sorted(requested):
                if name in pf.arango_indexers:
                    kind = ActionKind.skip
                elif name in pf.cached_indexers:
                    kind = ActionKind.copy_cache
                elif name in pf.missing_indexers:
                    kind = ActionKind.run_indexer
                else:
                    continue

                actions.append(
                    IndexerAction(file_ref=pf.ref, indexer_name=name, kind=kind))

        return actions

    def prepare_and_create_plan(self, files: list[FileRef],
                                indexers: Sequence[BaseIndexer]) -> ExecutionPlan:
        return self.create_plan(self.prepare_files(files, indexers), indexers)

    def create_plan(self, prepared: list[PreparedFile],
                    indexers: Sequence[BaseIndexer]) -> ExecutionPlan:
        names = [i.asset_name for i in indexers]
        windows = self.build_windows(names)
        requested = {name for window in windows for name in window}
        dependencies = {
            name:
                tuple(dep
                      for dep in self._indexer_instances[name].required_assets
                      if dep in requested) for name in sorted(requested)
        }

        actions = self.build_actions(prepared, indexers)
        actions_by_indexer: dict[str, dict[ActionKind, list[FileRef]]] = {}
        for action in actions:
            if action.kind == ActionKind.skip:
                continue
            by_kind = actions_by_indexer.setdefault(action.indexer_name, {
                ActionKind.copy_cache: [],
                ActionKind.run_indexer: [],
            })
            by_kind[action.kind].append(action.file_ref)

        planned: list[PlannedIndexerBatch] = []

        for window_id, window in enumerate(windows):
            for name in window:
                by_kind = actions_by_indexer.get(name)
                if not by_kind:
                    continue

                indexer = self._indexer_instances[name]

                copy_refs = by_kind[ActionKind.copy_cache]
                if copy_refs:
                    planned.append(
                        PlannedIndexerBatch(
                            indexer_name=name,
                            kind=ActionKind.copy_cache,
                            file_refs=copy_refs,
                            sub_batches=[copy_refs],
                            window_id=window_id,
                        ))

                run_refs = by_kind[ActionKind.run_indexer]
                if run_refs:
                    chunk_size = max(1, indexer.max_parallel)
                    sub_batches = [
                        run_refs[i:i + chunk_size]
                        for i in range(0, len(run_refs), chunk_size)
                    ]
                    planned.append(
                        PlannedIndexerBatch(
                            indexer_name=name,
                            kind=ActionKind.run_indexer,
                            file_refs=run_refs,
                            sub_batches=sub_batches,
                            window_id=window_id,
                        ))

        return ExecutionPlan(
            batches=planned,
            windows=windows,
            dependencies=dependencies,
        )

    def execute_plan(self, plan: ExecutionPlan) -> None:
        total_batches = len(plan.batches)
        plan_started_at = monotonic()

        for batch_idx, batch in enumerate(plan.batches, start=1):
            completed_batches = batch_idx - 1
            elapsed = monotonic() - plan_started_at

            if completed_batches > 0 and elapsed > 0:
                batches_per_min = (completed_batches / elapsed) * 60.0
                eta_plan_sec = ((total_batches - completed_batches) /
                                (batches_per_min / 60.0)
                                if batches_per_min > 0 else float("inf"))
                batches_per_min_str = f"{batches_per_min:.2f}"
                eta_plan_str = f"{eta_plan_sec:.1f}s"
            else:
                batches_per_min_str = "n/a"
                eta_plan_str = "unknown"

            log.debug(
                "batch {}/{}: indexer={} kind={} window={} files={} sub_batches={} batches_per_min={} eta_plan={}"
                .format(
                    batch_idx,
                    total_batches,
                    batch.indexer_name,
                    batch.kind.value,
                    batch.window_id,
                    len(batch.file_refs),
                    len(batch.sub_batches),
                    batches_per_min_str,
                    eta_plan_str,
                ),)

            with self.ctx.trace_scope(
                    "execute batch",
                    batch=batch_idx,
                    total_batches=total_batches,
                    indexer=batch.indexer_name,
                    kind=batch.kind.value,
                    window=batch.window_id,
                    files=len(batch.file_refs),
                    sub_batches=len(batch.sub_batches),
            ):
                if batch.kind == ActionKind.copy_cache:
                    self._run_copy_cache_batch(batch)
                else:
                    self._run_indexer_batch(batch)

    def truncate_all(self) -> None:
        self.db.truncate_all(list(self._indexer_instances.keys()))

    def run_indexers(
        self,
        files: list[FileRef],
        indexers: Sequence[BaseIndexer],
    ) -> None:
        with self.ctx.trace_scope(
                "file preparation",
                files=len(files),
                indexers=len(indexers),
        ):
            prepared = self.prepare_files(files, indexers)

        with self.ctx.trace_scope(
                "plan construction",
                files=len(files),
                indexers=len(indexers),
        ):
            plan = self.create_plan(prepared, indexers)

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
            result=self.db.get_indexer_result(  # type: ignore
                hash if isinstance(hash, FileHash) else hash.hash,
                self.get_indexer(name),
            ),
        )

    def run_indexer(self, file: FileRef, indexers: Sequence[BaseIndexer]):
        self.run_indexers([file], indexers)

    def _read_meta_output(self, indexer: BaseIndexer, path: Path) -> IndexerOutput | None:
        """Read this indexer's cached output from the sidecar meta file, if
        present. Missing entries do NOT produce an error output -- the
        indexer is run normally instead."""
        meta_path = Path(str(path) + META_SUFFIX)
        if not meta_path.exists():
            return None

        with self.ctx.trace_scope("read meta file", file=str(meta_path)):
            data = json.loads(meta_path.read_text())

        entry = data.get("indexers", {}).get(indexer.asset_name)
        if entry is None:
            return None

        return parse_indexer_output(indexer, entry)

    def _run_copy_cache_batch(self, batch: PlannedIndexerBatch) -> None:
        indexer = self._indexer_instances[batch.indexer_name]

        with self.ctx.trace_scope("copy cache to arango",
                                  indexer=indexer.asset_name,
                                  files=len(batch.file_refs)):
            for ref in batch.file_refs:
                out = load_cached_output(self.ctx, indexer, ref.hash.hash)
                if out is None:
                    continue

                if not is_processed_result(out.result):
                    continue

                with ExceptionContextNote(
                        f"copy cached indexer asset: {indexer.asset_name}"):
                    self.db.store_indexer_output(ref, out)

    def _run_indexer_batch(self, batch: PlannedIndexerBatch) -> None:
        indexer = self._indexer_instances[batch.indexer_name]
        resources = self._resources_for_indexer(indexer)

        def work(ref: FileRef) -> Optional[tuple[FileRef, IndexerOutput]]:
            path = self.db.get_path(ref)

            meta_output = self._read_meta_output(indexer, path)
            if meta_output is not None:
                return ref, meta_output

            assets: dict[str, IndexerOutput | None] = {}
            for name in indexer.required_assets:
                if self.db.has_indexer_result(ref, name):
                    assets[name] = self.get_indexer_result(ref.hash, name)
                else:
                    assets[name] = None

            request = IndexerRequest(file_ref=ref, dependency_results=assets)

            function_started_at = datetime.now(timezone.utc)
            execution_started = perf_counter()

            with (
                    ExceptionContextNote(
                        f"running indexer '{indexer.asset_name}' for '{path} {ref.hash}'"
                    ),
                    ExceptionContextNote(f"request {request}"),
                    self.ctx.trace_scope("index", file=str(path)),
            ):
                out = indexer.run(
                    ctx=self.ctx,
                    request=request,
                    resources=resources,  # type: ignore
                    assets=assets,  # type: ignore
                )

            store_cached_output(
                self.ctx,
                indexer,
                ref.hash.hash,
                out,
                function_started_at=function_started_at,
                function_duration_seconds=perf_counter() - execution_started,
            )

            return ref, out

        total_sub_batches = len(batch.sub_batches)
        recent_sub_batch_times: deque[float] = deque(maxlen=30)

        for sub_idx, chunk in enumerate(batch.sub_batches, start=1):
            remaining_sub_batches = total_sub_batches - sub_idx + 1

            if recent_sub_batch_times:
                average_sub_batch_sec = (sum(recent_sub_batch_times) /
                                         len(recent_sub_batch_times))
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
                ),)

            sub_batch_started_at = monotonic()

            with self.ctx.trace_scope(
                    "execute sub-batch",
                    indexer=batch.indexer_name,
                    sub_batch=sub_idx,
                    total_sub_batches=total_sub_batches,
                    size=len(chunk),
            ):
                if len(chunk) > 1 and indexer.max_parallel > 1:
                    with ThreadPoolExecutor(max_workers=indexer.max_parallel) as ex:
                        completed_1 = list(ex.map(work, chunk))
                else:
                    completed_1 = [work(ref) for ref in chunk]

                completed = [c for c in completed_1 if c is not None]

                with self.ctx.trace_scope("store all indexer results",
                                          indexer=indexer.asset_name):
                    for ref, out in completed:
                        if not is_processed_result(out.result):
                            continue

                        with ExceptionContextNote(f"indexer asset: {indexer.asset_name}"):
                            self.db.store_indexer_output(ref, out)

            recent_sub_batch_times.append(monotonic() - sub_batch_started_at)

        log.debug("finished indexer batch")
