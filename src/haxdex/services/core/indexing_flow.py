from __future__ import annotations

from loguru import logger
from pathlib import Path
from beartype.typing import Sequence, Any
from beartype import beartype

from haxdex.cli.cli_config import IndexPathConfig, IndexConfig
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_runtime import IndexRuntime
from haxdex.services.core.job_types import BaseIndexer, RunContext, META_SUFFIX
from haxdex.services.core.types import FileRef, RootRef
from haxdex.services.file_iteration import RootFilter, prepare_root_filters, DirConfig
from haxdex.services.utils import format_duration
from time import perf_counter
import os


@beartype
def _is_file_selected_by_filters(file: Path, filters: list[RootFilter]) -> bool:
    file_str = str(file)

    for root_filter in filters:
        # Fast reject when file is not under this configured directory.
        if not file_str.startswith(root_filter.root_str):
            continue

        try:
            relative = file.relative_to(root_filter.root_path)
        except ValueError:
            continue

        if root_filter.ignore_spec is None:
            return True

        if not root_filter.ignore_spec.match_file(relative.as_posix()):
            return True

    return False


@beartype
def _assert_dir_paths_under_root(path_cfg: IndexPathConfig) -> None:
    root = path_cfg.root_path.resolve()
    for dir_cfg in path_cfg.paths:
        candidate = dir_cfg.path.resolve()
        assert candidate.is_relative_to(root), (
            f"configured path '{dir_cfg.path}' must be inside root '{path_cfg.root_path}'"
        )


@beartype
def collect_files_for_path(
    dir_configs: list[DirConfig],
    filters: list[RootFilter],
    limit_per_path: int | None,
) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()

    for dir_cfg in dir_configs:
        source = dir_cfg.path

        assert source.exists(), str(source)

        if source.is_file():
            candidates = [source]

        else:
            candidates = (p for p in source.rglob("*") if p.is_file())

        if not candidates:
            logger.warning(f"no candidates from {source}")

        for file in candidates:
            if file in seen:
                continue

            if str(file).endswith(META_SUFFIX):
                continue

            if not _is_file_selected_by_filters(file, filters):
                # logger.debug(f"ignoring {file}")
                continue

            seen.add(file)
            files.append(file)

            if limit_per_path is not None and len(files) >= limit_per_path:
                return files

    return files


def build_refs_for_root(
    db: IndexDatabase,
    root: RootRef,
    files: list[Path],
) -> list[FileRef]:
    return [db.as_ref(root, file) for file in files]


@beartype
def run_indexing_batch(
    db: IndexDatabase,
    runner: IndexRuntime,
    ctx: RunContext,
    indexers: Sequence[BaseIndexer],
    root: Any,
    path_name: str,
    batch_idx: int,
    total_batches: int,
    batch_files: Sequence[Any],
) -> tuple[int, float]:
    t0 = perf_counter()

    with ctx.trace_scope(
            "build refs for root",
            path=path_name,
            batch=batch_idx,
            batch_files=len(batch_files),
    ):
        refs = build_refs_for_root(db, root, batch_files)

    with ctx.trace_scope(
            "prepare files",
            root=root.name,
            batch=batch_idx,
            total_batches=total_batches,
            files=len(refs),
            indexers=len(indexers),
    ):
        prepared = runner.prepare_files(refs, indexers)

    with ctx.trace_scope(
            "root plan construction",
            root=root.name,
            batch=batch_idx,
            total_batches=total_batches,
            files=len(refs),
            indexers=len(indexers),
    ):
        plan = runner.create_plan(prepared, indexers)

    with ctx.trace_scope(
            "root plan execution",
            root=root.name,
            batch=batch_idx,
            total_batches=total_batches,
            batches=len(plan.batches),
            total_runs=plan.total_runs(),
    ):
        runner.execute_plan(plan)

    elapsed = perf_counter() - t0
    return len(refs), elapsed


@beartype
def run_indexing_per_root_plan(
    db: IndexDatabase,
    runner: IndexRuntime,
    ctx: RunContext,
    cfg: IndexConfig,
    indexers: Sequence[BaseIndexer],
) -> int:
    indexed_total = 0
    plan_exec_times: list[float] = []
    logger.info("constructing index jobs plan")

    for path_idx, path_cfg in enumerate(cfg.paths):
        if cfg.limit_total is not None and cfg.limit_total <= indexed_total:
            logger.info(f"limit total {cfg.limit_total} <= indexed total {indexed_total}")
            return indexed_total

        assert path_cfg.root_path.is_absolute(), str(path_cfg.root_path)
        assert path_cfg.root_path.exists(), str(path_cfg.root_path)

        root = db.add_root(path_cfg.name, path_cfg.root_path)

        with ctx.trace_scope("index path", path=path_cfg.name):
            with ctx.trace_scope("validate configured paths", path=path_cfg.name):
                _assert_dir_paths_under_root(path_cfg)

            with ctx.trace_scope("prepare root filters", path=path_cfg.name):
                root_filters = prepare_root_filters(path_cfg.paths)

            with ctx.trace_scope("collect files for path", path=path_cfg.name):
                logger.info(
                    f"collect files for path '{path_cfg.name}' {path_idx}/{len(cfg.paths)}"
                )
                files = collect_files_for_path(
                    path_cfg.paths,
                    root_filters,
                    cfg.limit_per_path,
                )

            if cfg.limit_total is not None:
                remaining = max(0, cfg.limit_total - indexed_total)
                files = files[:remaining]

            if not files:
                logger.info(f"no more files, total indexed {indexed_total}")
                continue

            plan_run_size = cfg.max_plan_run_size or len(files)
            assert plan_run_size > 0, "max_plan_run_size must be > 0"

            logger.info(f"collected {len(files)} files for indexing")
            total_batches = (len(files) + plan_run_size - 1) // plan_run_size

            for batch_idx, start in enumerate(range(0, len(files), plan_run_size),
                                              start=1):
                batch_files = files[start:start + plan_run_size]

                if plan_exec_times:
                    last_exec = plan_exec_times[-20:]
                    avg_plan = sum(last_exec) / len(last_exec)
                    remaining_batches = total_batches - batch_idx + 1
                    duration_fmt = f"{format_duration(avg_plan)}/plan, ETA {format_duration(avg_plan * remaining_batches)}"
                else:
                    duration_fmt = "n/a/plan, ETA n/a"

                logger.info(
                    f"run plan for [{start}:{start + plan_run_size}]/{len(files)} "
                    f"({(float(start) /float(len(files)) * 100.0):.2f}%) {duration_fmt}")

                indexed_count, elapsed = run_indexing_batch(
                    db=db,
                    runner=runner,
                    ctx=ctx,
                    indexers=indexers,
                    root=root,
                    path_name=path_cfg.name,
                    batch_idx=batch_idx,
                    total_batches=total_batches,
                    batch_files=batch_files,
                )
                indexed_total += indexed_count
                plan_exec_times.append(elapsed)

    return indexed_total
