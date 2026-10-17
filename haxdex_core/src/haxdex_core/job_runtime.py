from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Sequence

from loguru import logger

from haxdex_core.db import IndexDatabase
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import FileRef, IndexerRequest


@dataclass(frozen=True)
class PreparedFile:
    ref: FileRef
    path: Path
    eligible_indexers: frozenset[str]


@dataclass(frozen=True)
class PlannedIndexerBatch:
    indexer_name: str
    file_refs: list[FileRef]
    sub_batches: list[list[FileRef]]


@dataclass(frozen=True)
class ExecutionPlan:
    batches: list[PlannedIndexerBatch]
    dependencies: dict[str, tuple[str, ...]]

    def total_runs(self) -> int:
        return sum(len(batch.file_refs) for batch in self.batches)

    def get_indexer_names(self) -> list[str]:
        return [batch.indexer_name for batch in self.batches]

    def to_text(self) -> str:
        lines = ["ExecutionPlan", f"  batches: {len(self.batches)}"]

        for index, batch in enumerate(self.batches, start=1):
            lines.append(
                f"    - batch {index}/{len(self.batches)}: "
                f"indexer={batch.indexer_name}, "
                f"files={len(batch.file_refs)}, "
                f"sub_batches={len(batch.sub_batches)}"
            )

            for sub_index, chunk in enumerate(batch.sub_batches, start=1):
                lines.append(
                    f"      - sub_batch "
                    f"{sub_index}/{len(batch.sub_batches)}: size={len(chunk)}"
                )

        return "\n".join(lines)


class IndexRuntime:
    def __init__(
        self,
        ctx: RunContext,
        db: IndexDatabase,
        indexer_types: Sequence[BaseIndexer] = (),
        resource_types: Sequence[BaseResource] = (),
    ) -> None:
        self.ctx = ctx
        self.db = db

        self._indexer_instances: dict[str, BaseIndexer] = {}
        for indexer in indexer_types:
            if indexer.asset_name in self._indexer_instances:
                raise ValueError(f"Duplicate indexer name: {indexer.asset_name!r}")

            if db.get_indexer_database(indexer.asset_name) is not indexer.database:
                raise ValueError(
                    f"Indexer {indexer.asset_name!r} uses an unmanaged database"
                )

            self._indexer_instances[indexer.asset_name] = indexer

        self._resource_instances: dict[str, BaseResource] = {}
        for resource in resource_types:
            if resource.resource_key in self._resource_instances:
                raise ValueError(f"Duplicate resource key: {resource.resource_key!r}")
            self._resource_instances[resource.resource_key] = resource

        self._validate_dependencies()

        self._indexer_order = list(
            TopologicalSorter(
                {
                    name: indexer.required_assets
                    for name, indexer in self._indexer_instances.items()
                }
            ).static_order()
        )

        self._resource_order = list(
            TopologicalSorter(
                {
                    name: resource.required_resources
                    for name, resource in self._resource_instances.items()
                }
            ).static_order()
        )

    def _validate_dependencies(self) -> None:
        for name, resource in self._resource_instances.items():
            missing = set(resource.required_resources) - self._resource_instances.keys()
            if missing:
                raise ValueError(
                    f"Resource {name!r} requires missing resources: {sorted(missing)}"
                )

        for name, indexer in self._indexer_instances.items():
            missing_assets = (
                set(indexer.required_assets) - self._indexer_instances.keys()
            )
            if missing_assets:
                raise ValueError(
                    f"Indexer {name!r} requires missing indexers: "
                    f"{sorted(missing_assets)}"
                )

            missing_resources = (
                set(indexer.required_resources) - self._resource_instances.keys()
            )
            if missing_resources:
                raise ValueError(
                    f"Indexer {name!r} requires missing resources: "
                    f"{sorted(missing_resources)}"
                )

    def _expand_requested(self, names: Sequence[str]) -> set[str]:
        needed: set[str] = set()
        stack = list(names)

        while stack:
            name = stack.pop()
            if name in needed:
                continue

            indexer = self._indexer_instances[name]
            needed.add(name)
            stack.extend(indexer.required_assets)

        return needed

    def _requested_order(self, names: Sequence[str]) -> list[str]:
        requested = self._expand_requested(names)
        return [name for name in self._indexer_order if name in requested]

    def _resources_for_indexer(
        self,
        indexer: BaseIndexer,
    ) -> dict[str, BaseResource]:
        needed: set[str] = set()
        stack = list(indexer.required_resources)

        while stack:
            name = stack.pop()
            if name in needed:
                continue

            needed.add(name)
            stack.extend(self._resource_instances[name].required_resources)

        return {
            name: self._resource_instances[name]
            for name in self._resource_order
            if name in needed
        }

    def get_indexers(
        self,
        names: Sequence[str] | None = None,
    ) -> list[BaseIndexer]:
        selected = self._indexer_order if names is None else names
        return [self._indexer_instances[name] for name in selected]

    def get_indexer(self, name: str) -> BaseIndexer:
        return self._indexer_instances[name]

    def prepare_files(
        self,
        refs: list[FileRef],
        indexers: Sequence[BaseIndexer],
    ) -> list[PreparedFile]:
        names = self._requested_order([indexer.asset_name for indexer in indexers])
        prepared: list[PreparedFile] = []

        for ref in refs:
            path = self.db.get_path(ref)
            eligible = frozenset(
                name for name in names if self._indexer_instances[name].can_run(path)
            )

            prepared.append(
                PreparedFile(
                    ref=ref,
                    path=path,
                    eligible_indexers=eligible,
                )
            )

        return prepared

    def create_plan(
        self,
        prepared: list[PreparedFile],
        indexers: Sequence[BaseIndexer],
    ) -> ExecutionPlan:
        names = self._requested_order([indexer.asset_name for indexer in indexers])
        batches: list[PlannedIndexerBatch] = []

        for name in names:
            refs = [file.ref for file in prepared if name in file.eligible_indexers]
            if not refs:
                continue

            indexer = self._indexer_instances[name]
            chunk_size = indexer.max_parallel
            chunks = [
                refs[start : start + chunk_size]
                for start in range(0, len(refs), chunk_size)
            ]

            batches.append(
                PlannedIndexerBatch(
                    indexer_name=name,
                    file_refs=refs,
                    sub_batches=chunks,
                )
            )

        return ExecutionPlan(
            batches=batches,
            dependencies={
                name: self._indexer_instances[name].required_assets for name in names
            },
        )

    def prepare_and_create_plan(
        self,
        files: list[FileRef],
        indexers: Sequence[BaseIndexer],
    ) -> ExecutionPlan:
        return self.create_plan(
            self.prepare_files(files, indexers),
            indexers,
        )

    def _validate_plan(self, plan: ExecutionPlan) -> None:
        scheduled = {batch.indexer_name for batch in plan.batches}
        completed: set[str] = set()

        for batch in plan.batches:
            name = batch.indexer_name
            indexer = self._indexer_instances[name]

            if name in completed:
                raise ValueError(f"Plan contains multiple batches for indexer {name!r}")

            pending_dependencies = (
                set(indexer.required_assets) & scheduled
            ) - completed

            if pending_dependencies:
                raise ValueError(
                    f"Batch {name!r} precedes its dependencies: "
                    f"{sorted(pending_dependencies)}"
                )

            flattened = [ref for chunk in batch.sub_batches for ref in chunk]
            if flattened != batch.file_refs:
                raise ValueError(f"Sub-batches do not match files for indexer {name!r}")

            if any(
                not chunk or len(chunk) > indexer.max_parallel
                for chunk in batch.sub_batches
            ):
                raise ValueError(f"Invalid sub-batch size for indexer {name!r}")

            completed.add(name)

    def execute_plan(self, plan: ExecutionPlan) -> None:
        self._validate_plan(plan)

        for batch_index, batch in enumerate(plan.batches, start=1):
            logger.debug(
                "batch {}/{}: indexer={} files={} sub_batches={}",
                batch_index,
                len(plan.batches),
                batch.indexer_name,
                len(batch.file_refs),
                len(batch.sub_batches),
            )

            with self.ctx.trace_scope(
                "execute batch",
                batch=batch_index,
                total_batches=len(plan.batches),
                indexer=batch.indexer_name,
                files=len(batch.file_refs),
                sub_batches=len(batch.sub_batches),
            ):
                self._run_indexer_batch(batch)

    def _run_indexer_batch(self, batch: PlannedIndexerBatch) -> None:
        indexer = self._indexer_instances[batch.indexer_name]
        resources = self._resources_for_indexer(indexer)
        assets = {
            name: self.db.get_indexer_database(name) for name in indexer.required_assets
        }

        def work(ref: FileRef) -> None:
            request = IndexerRequest(file_ref=ref)
            path = self.ctx.get_path(ref)

            try:
                with self.ctx.trace_scope(
                    "index",
                    indexer=indexer.asset_name,
                    file=str(path),
                ):
                    indexer.run(
                        ctx=self.ctx,
                        request=request,
                        resources=dict(resources),
                        assets=dict(assets),
                    )
            except Exception as error:
                error.add_note(
                    f"Running indexer {indexer.asset_name!r} "
                    f"for {path} ({ref.hash.hash})"
                )
                raise

        if indexer.max_parallel == 1:
            for sub_index, chunk in enumerate(batch.sub_batches, start=1):
                with self.ctx.trace_scope(
                    "execute sub-batch",
                    indexer=indexer.asset_name,
                    sub_batch=sub_index,
                    total_sub_batches=len(batch.sub_batches),
                    size=len(chunk),
                ):
                    for ref in chunk:
                        work(ref)
            return

        with ThreadPoolExecutor(max_workers=indexer.max_parallel) as executor:
            for sub_index, chunk in enumerate(batch.sub_batches, start=1):
                with self.ctx.trace_scope(
                    "execute sub-batch",
                    indexer=indexer.asset_name,
                    sub_batch=sub_index,
                    total_sub_batches=len(batch.sub_batches),
                    size=len(chunk),
                ):
                    for _ in executor.map(work, chunk):
                        pass

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

    def run_indexer(
        self,
        file: FileRef,
        indexers: Sequence[BaseIndexer],
    ) -> None:
        self.run_indexers([file], indexers)
