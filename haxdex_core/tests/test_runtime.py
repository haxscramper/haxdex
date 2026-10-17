from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from haxdex_core.db import IndexDatabase
from haxdex_core.hash_cache import HashCache
from haxdex_core.job_runtime import IndexRuntime
from haxdex_core.job_types import BaseIndexer, RunContext
from sqlalchemy import Column, MetaData, String, Table, select


@pytest.fixture
def db(tmp_path: Path):
    with IndexDatabase(
        directory=tmp_path / "databases",
        hash_cache=HashCache(tmp_path / "hashes.sqlite"),
    ) as database:
        yield database


def make_indexer(
    db: IndexDatabase,
    name: str,
    required_assets: tuple[str, ...] = (),
    max_parallel: int = 1,
):
    metadata = MetaData()
    table = Table(
        name,
        metadata,
        Column("hash", String(64), primary_key=True),
        Column("value", String, nullable=False),
    )

    class TestIndexer(BaseIndexer):
        asset_name = name
        result_schema = metadata

        def run(self, ctx, request, resources, assets) -> None:
            file_hash = request.get_hash_str()

            with self.database.transaction() as connection:
                existing = connection.execute(
                    select(table.c.hash).where(table.c.hash == file_hash)
                ).first()

                if existing is None:
                    connection.execute(
                        table.insert().values(
                            hash=file_hash,
                            value=self.asset_name,
                        )
                    )

    TestIndexer.required_assets = required_assets
    TestIndexer.max_parallel = max_parallel

    return TestIndexer(
        database=db.open_indexer(name, metadata),
        config=TestIndexer.config_model(),
    )


def make_refs(
    db: IndexDatabase,
    directory: Path,
    count: int,
):
    root = db.add_root("root", directory)
    refs = []

    for index in range(count):
        path = directory / f"{index}.txt"
        path.write_text(f"payload-{index}")
        refs.append(db.as_ref(root, path))

    return refs


def test_dependencies_and_separate_databases(
    db: IndexDatabase,
    tmp_path: Path,
) -> None:
    calls = []
    parent = make_indexer(db, "parent")
    child = make_indexer(db, "child", required_assets=("parent",))

    parent_run = parent.run
    child_run = child.run

    def run_parent(ctx, request, resources, assets):
        calls.append("parent")
        parent_run(ctx, request, resources, assets)

    def run_child(ctx, request, resources, assets):
        calls.append("child")
        assert assets["parent"] is parent.database
        assert assets["parent"].has_file(request.file_ref)
        child_run(ctx, request, resources, assets)

    parent.run = run_parent
    child.run = run_child

    runtime = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[child, parent],
    )
    refs = make_refs(db, tmp_path, 1)
    plan = runtime.prepare_and_create_plan(
        refs,
        runtime.get_indexers(["child"]),
    )

    assert plan.get_indexer_names() == ["parent", "child"]

    runtime.execute_plan(plan)

    assert calls == ["parent", "child"]
    assert parent.database.path != child.database.path
    assert db.has_indexer_result(refs[0], "parent")
    assert db.has_indexer_result(refs[0], "child")


def test_skip_decision_belongs_to_indexer(
    db: IndexDatabase,
    tmp_path: Path,
) -> None:
    indexer = make_indexer(db, "repeat")
    calls = []
    original_run = indexer.run

    def run(ctx, request, resources, assets):
        calls.append(request.get_hash_str())
        original_run(ctx, request, resources, assets)

    indexer.run = run
    runtime = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[indexer],
    )
    refs = make_refs(db, tmp_path, 1)

    runtime.run_indexers(refs, [indexer])
    runtime.run_indexers(refs, [indexer])

    assert len(calls) == 2

    with indexer.database.connection() as connection:
        rows = connection.execute(select(indexer.database.main_table)).all()

    assert len(rows) == 1


def test_parallel_execution_and_sequential_indexer_batches(
    db: IndexDatabase,
    tmp_path: Path,
) -> None:
    first = make_indexer(db, "first", max_parallel=4)
    second = make_indexer(db, "second", max_parallel=4)

    lock = threading.Lock()
    barrier = threading.Barrier(4)
    active = 0
    peak = 0
    first_completed = 0

    def run_first(ctx, request, resources, assets):
        nonlocal active, peak, first_completed

        with lock:
            active += 1
            peak = max(peak, active)

        try:
            barrier.wait(timeout=10)
            time.sleep(0.01)
        finally:
            with lock:
                active -= 1
                first_completed += 1

    def run_second(ctx, request, resources, assets):
        with lock:
            assert active == 0
            assert first_completed == 8

    first.run = run_first
    second.run = run_second

    runtime = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[first, second],
    )
    refs = make_refs(db, tmp_path, 8)
    plan = runtime.prepare_and_create_plan(refs, [first, second])

    assert [len(chunk) for chunk in plan.batches[0].sub_batches] == [4, 4]

    runtime.execute_plan(plan)

    assert peak == 4


def test_transaction_rolls_back(db: IndexDatabase) -> None:
    indexer = make_indexer(db, "rollback")
    table = indexer.database.main_table

    with pytest.raises(RuntimeError, match="abort"):
        with indexer.database.transaction() as connection:
            connection.execute(
                table.insert().values(
                    hash="a" * 64,
                    value="uncommitted",
                )
            )
            raise RuntimeError("abort")

    assert not indexer.database.has_file("a" * 64)


def test_reordered_dependency_plan_is_rejected(
    db: IndexDatabase,
    tmp_path: Path,
) -> None:
    parent = make_indexer(db, "parent")
    child = make_indexer(db, "child", required_assets=("parent",))

    runtime = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[parent, child],
    )
    refs = make_refs(db, tmp_path, 1)
    plan = runtime.prepare_and_create_plan(refs, [child])
    plan.batches.reverse()

    with pytest.raises(ValueError, match="precedes its dependencies"):
        runtime.execute_plan(plan)

    assert not parent.database.has_file(refs[0])


def test_persistence(tmp_path: Path) -> None:
    directory = tmp_path / "databases"
    hash_path = tmp_path / "hashes.sqlite"

    with IndexDatabase(directory, HashCache(hash_path)) as database:
        indexer = make_indexer(database, "persistent")
        refs = make_refs(database, tmp_path, 1)
        ref = refs[0]

        runtime = IndexRuntime(
            ctx=RunContext(database),
            db=database,
            indexer_types=[indexer],
        )
        runtime.run_indexers(refs, [indexer])

    with IndexDatabase(directory, HashCache(hash_path)) as database:
        indexer = make_indexer(database, "persistent")

        assert indexer.database.has_file(ref)
        assert database.get_path(ref) == tmp_path / "0.txt"
        assert database.get_all_refs(ref.hash) == [ref]
