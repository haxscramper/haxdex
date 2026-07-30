from __future__ import annotations

import threading
import time
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine

from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_runtime import IndexRuntime
from haxdex.services.core.job_types import BaseIndexer, BaseResource, RunContext, BaseResourceConfig, BaseIndexerConfig
from haxdex.services.core.types import FileRef, IndexDocument, IndexerOutput


def _make_refs(db: IndexDatabase, stable_test_dir: Path, n: int) -> list[FileRef]:
    root = db.add_root("root", stable_test_dir)
    refs: list[FileRef] = []
    for i in range(n):
        p = stable_test_dir / f"f_{i}.txt"
        p.write_text(f"payload-{i}")
        refs.append(db.as_ref(root, p))
    return refs


class SimpleResult(BaseModel):
    value: str


class NoopIndexer(BaseIndexer):
    asset_name = "noop"
    result_model = SimpleResult

    def run(self, ctx: RunContext, request, resources, assets) -> IndexerOutput:
        return IndexerOutput(indexer_id=self.asset_name, result=SimpleResult(value="ok"))


def test_plan_structure_and_topological_batches(
    db: IndexDatabase,
    stable_test_dir: Path,
    index_cache_database: Engine,
) -> None:

    class A(NoopIndexer):
        asset_name = "a"

    class B(NoopIndexer):
        asset_name = "b"
        required_assets = ("a",)

    refs = _make_refs(db, stable_test_dir, 3)
    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[
            A(config=A.config_model(), database=index_cache_database),
            B(config=B.config_model(), database=index_cache_database),
        ],
        resource_types=[],
    )

    plan = rt.create_plan(refs, ["b"])
    assert [b.indexer_name for b in plan.batches] == ["a", "b"]
    assert len(plan.batches[0].file_refs) == 3
    assert len(plan.batches[1].file_refs) == 3


def test_plan_sub_batching_respects_max_parallel(db: IndexDatabase, stable_test_dir: Path,
                                                 index_cache_database: Engine) -> None:

    class MP(NoopIndexer):
        asset_name = "mp"
        max_parallel = 4

    refs = _make_refs(db, stable_test_dir, 20)
    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[MP(config=MP.config_model(), database=index_cache_database)],
        resource_types=[],
    )

    plan = rt.create_plan(refs, rt.get_indexers(["mp"]))
    assert len(plan.batches) == 1
    assert len(plan.batches[0].sub_batches) == 5
    assert all(len(chunk) <= 4 for chunk in plan.batches[0].sub_batches)


def test_exclusive_direct_conflict_splits_windows(db: IndexDatabase,
                                                  stable_test_dir: Path,
                                                  index_cache_database: Engine) -> None:

    class Llama(BaseResource):
        resource_key = "llama"
        exclusive = True

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return request

    class Gemma(BaseResource):
        resource_key = "gemma"
        required_resources = ("llama",)

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return resources["llama"].handle(ctx, request, resources)

    class A(NoopIndexer):
        asset_name = "a"
        required_resources = ("llama",)

    class B(NoopIndexer):
        asset_name = "b"
        required_resources = ("gemma",)

    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[
            A(database=index_cache_database, config=A.config_model()),
            B(database=index_cache_database, config=B.config_model()),
        ],
        resource_types=[
            Llama(config=Llama.config_model()),
            Gemma(config=Gemma.config_model()),
        ],
    )

    windows = rt.build_windows(["a", "b"])
    assert len(windows) == 2


def test_exclusive_transitive_same_direct_consumer_same_window(
        db: IndexDatabase, stable_test_dir: Path, index_cache_database: Engine) -> None:

    class Llama(BaseResource):
        resource_key = "llama"
        exclusive = True

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return request

    class Gemma(BaseResource):
        resource_key = "gemma"
        required_resources = ("llama",)

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return resources["llama"].handle(ctx, request, resources)

    class Summary(NoopIndexer):
        asset_name = "summary"
        required_resources = ("gemma",)

    class Elaborate(NoopIndexer):
        asset_name = "elaborate"
        required_resources = ("gemma",)

    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[
            Summary(database=index_cache_database, config=Summary.config_model()),
            Elaborate(database=index_cache_database, config=Elaborate.config_model()),
        ],
        resource_types=[
            Llama(config=Llama.config_model()),
            Gemma(config=Gemma.config_model()),
        ],
    )

    windows = rt.build_windows(["summary", "elaborate"])
    assert len(windows) == 1
    assert set(windows[0]) == {"summary", "elaborate"}


def test_exclusive_transitive_different_direct_consumers_split(
    db: IndexDatabase,
    stable_test_dir: Path,
    index_cache_database: Engine,
) -> None:

    class Llama(BaseResource):
        resource_key = "llama"
        exclusive = True

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return request

    class Gemma(BaseResource):
        resource_key = "gemma"
        required_resources = ("llama",)

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return resources["llama"].handle(ctx, request, resources)

    class Mistral(BaseResource):
        resource_key = "mistral"
        required_resources = ("llama",)

        def handle(self, ctx: RunContext, request: BaseModel, resources):
            return resources["llama"].handle(ctx, request, resources)

    class Summary(NoopIndexer):
        asset_name = "summary"
        required_resources = ("gemma",)

    class Extract(NoopIndexer):
        asset_name = "extract"
        required_resources = ("mistral",)

    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[
            Summary(config=Summary.config_model(), database=index_cache_database),
            Extract(config=Extract.config_model(), database=index_cache_database),
        ],
        resource_types=[
            Llama(config=Llama.config_model()),
            Gemma(config=Gemma.config_model()),
            Mistral(config=Mistral.config_model())
        ],
    )

    windows = rt.build_windows(["summary", "extract"])
    assert len(windows) == 2


def test_stateful_resource_model_load_not_churned(
    db: IndexDatabase,
    stable_test_dir: Path,
    index_cache_database: Engine,
) -> None:

    class LlamaRequest(BaseModel):
        model: str
        prompt: str

    class LlamaResponse(IndexDocument):
        text: str

    class GemmaRequest(BaseModel):
        prompt: str

    class Llama(BaseResource):
        resource_key = "llama"
        exclusive = True

        def __init__(self, config: BaseResourceConfig):
            super().__init__(config=config)
            self.current_model: str | None = None
            self.load_count = 0
            self.lock = threading.Lock()

        def handle(self, ctx: RunContext, request: LlamaRequest, resources):
            with self.lock:
                if self.current_model != request.model:
                    self.current_model = request.model
                    self.load_count += 1
            return LlamaResponse(text=f"{request.model}:{request.prompt}",
                                 hash="1234" * 16)

    class Gemma(BaseResource):
        resource_key = "gemma"
        required_resources = ("llama",)

        def handle(self, ctx: RunContext, request: GemmaRequest, resources):
            llama = resources["llama"]
            return llama.handle(
                ctx,
                LlamaRequest(model="gemma-26b", prompt=request.prompt),
                resources,
            )

    class SummaryResult(IndexDocument):
        summary: str

    class SummaryIndexer(BaseIndexer):
        asset_name = "summary"
        result_model = SummaryResult
        required_resources = ("gemma",)
        max_parallel = 4

        def run(self, ctx: RunContext, request, resources, assets) -> IndexerOutput:
            gemma = resources["gemma"]
            out = gemma.handle(ctx, GemmaRequest(prompt="x"), resources)
            return IndexerOutput(
                indexer_id=self.asset_name,
                result=SummaryResult(summary=out.text, hash="99999"),
            )

    llama = Llama(config=Llama.config_model())
    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[
            SummaryIndexer(config=SummaryIndexer.config_model(),
                           database=index_cache_database)
        ],
        resource_types=[llama, Gemma(config=Gemma.config_model())],
    )

    refs = _make_refs(db, stable_test_dir, 12)
    rt.run_indexers(refs, rt.get_indexers(["summary"]))
    assert llama.load_count == 1


def test_max_parallel_execution_bound(db: IndexDatabase, stable_test_dir: Path,
                                      index_cache_database: Engine) -> None:

    class PResult(IndexDocument):
        value: str

    class ParallelIndexer(BaseIndexer):
        asset_name = "parallel"
        result_model = PResult
        max_parallel = 4

        def __init__(self, config: BaseIndexerConfig, database: Engine):
            super().__init__(config=config, database=database)
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def run(self, ctx: RunContext, request, resources, assets) -> IndexerOutput:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            return IndexerOutput(indexer_id=self.asset_name,
                                 result=PResult(value="ok", hash="ABCD" * 16))

    idx = ParallelIndexer(
        config=ParallelIndexer.config_model(),
        database=index_cache_database,
    )
    rt = IndexRuntime(
        ctx=RunContext(db),
        db=db,
        indexer_types=[idx],
        resource_types=[],
    )

    refs = _make_refs(db, stable_test_dir, 12)
    rt.run_indexers(refs, rt.get_indexers(["parallel"]))
    assert idx.peak == 4
