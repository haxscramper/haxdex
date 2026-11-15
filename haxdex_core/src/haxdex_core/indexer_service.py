import sys
from datetime import datetime
from pathlib import Path

from beartype import beartype
from loguru import logger

from haxdex.services.log_config import keep_last_files
from haxdex_core.core_config import IndexServiceConfig, LoggingConfig
from haxdex_core.db import IndexDatabase
from haxdex_core.hash_cache import HashCache
from haxdex_core.indexing_flow import run_indexing_per_root_plan
from haxdex_core.job_runtime import IndexRuntime
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.utils import (
    get_xdg_cache_dir,
)


@beartype
class IndexService:
    @staticmethod
    def reset_for_config(cfg: IndexServiceConfig) -> None:
        IndexDatabase.reset_database(cfg.db.directory)

    def get_indexer(self, s: str) -> BaseIndexer:
        for i in self.indexer_instances:
            if i.asset_name == s:
                return i

        assert False, f"No indexer named {s}"

    def __init__(
        self,
        cfg: IndexServiceConfig,
        indexer_types: list[type[BaseIndexer]],
        resource_types: list[type[BaseResource]],
    ) -> None:
        self.cfg = cfg
        self.resource_instances = []
        self.indexer_instances: list[BaseIndexer] = []
        self.indexer_types = indexer_types
        self.resource_types = resource_types

        enabled_resources = set(cfg.resources)
        enabled_indexers = set(cfg.indexers)

        for resource_type in resource_types:
            key = resource_type.resource_key
            if key not in enabled_resources:
                continue

            missing = set(resource_type.required_resources) - enabled_resources
            if missing:
                raise ValueError(
                    f"Resource {key!r} requires resources: {sorted(missing)}"
                )

            self.resource_instances.append(resource_type(config=cfg.resources[key]))

        for indexer_type in indexer_types:
            key = indexer_type.asset_name
            if key not in enabled_indexers:
                continue

            missing_resources = set(indexer_type.required_resources) - enabled_resources
            if missing_resources:
                raise ValueError(
                    f"Indexer {key!r} requires resources: {sorted(missing_resources)}"
                )

            missing_assets = set(indexer_type.required_assets) - enabled_indexers
            if missing_assets:
                raise ValueError(
                    f"Indexer {key!r} requires indexers: {sorted(missing_assets)}"
                )

        self.db = IndexDatabase(
            directory=cfg.db.directory,
            hash_cache=HashCache(Path(cfg.hash_cache).expanduser().absolute()),
        )

        try:
            for indexer_type in indexer_types:
                key = indexer_type.asset_name
                if key not in enabled_indexers:
                    continue

                database = self.db.open_indexer(
                    asset_name=key,
                    result_schema=indexer_type.result_schema,
                    reset_db=cfg.indexers[key].reset_db,
                )

                self.indexer_instances.append(
                    indexer_type(
                        database=database,
                        config=cfg.indexers[key],
                    )
                )
        except BaseException:
            self.db.close()
            raise

        self.ctx = RunContext(self.db)

    def close(self) -> None:
        self.db.close()

    import sys
    from datetime import datetime
    from pathlib import Path

    from loguru import logger

    def setup_runtime_logging(self, log_cfg: LoggingConfig) -> tuple[Path, Path, Path]:
        run_text_dir = get_xdg_cache_dir(["logs", "run", "text"])
        run_json_dir = get_xdg_cache_dir(["logs", "run", "json"])
        perf_dir = get_xdg_cache_dir(["logs", "perf"])

        run_text_dir.mkdir(parents=True, exist_ok=True)
        run_json_dir.mkdir(parents=True, exist_ok=True)
        perf_dir.mkdir(parents=True, exist_ok=True)

        if not log_cfg.setup_handlers:
            logger.info("not setup runtime loggin")
            return run_text_dir, run_json_dir, perf_dir

        timestamp = datetime.now().isoformat()

        run_text_file = run_text_dir / f"{timestamp}.log"
        run_json_file = run_json_dir / f"{timestamp}.jsonl"

        logger.remove()

        text_format = "{time:YYYY-MM-DDTHH:mm:ss.SSSSSS} {level} {name} {file.name}:{line}: {message}"

        logger.add(
            sys.stderr,
            level="INFO",
            enqueue=False,
            backtrace=True,
            diagnose=False,
        )

        logger.add(
            run_text_file,
            level="DEBUG",
            format=text_format,
            mode="w",
            enqueue=False,
            backtrace=True,
            diagnose=False,
        )

        logger.add(
            run_json_file,
            level="DEBUG",
            serialize=True,
            mode="w",
            enqueue=False,
            backtrace=True,
            diagnose=False,
        )

        if log_cfg.logfile is not None:
            log_cfg.logfile.parent.mkdir(parents=True, exist_ok=True)
            logger.add(
                log_cfg.logfile,
                level="DEBUG",
                serialize=(log_cfg.logfile_format == "json"),
                format=text_format,
                mode="w",
                enqueue=False,
                backtrace=True,
                diagnose=False,
            )

        keep_last_files(run_text_dir, "*.log", 20)
        keep_last_files(run_json_dir, "*.jsonl", 20)
        keep_last_files(perf_dir, "*.json", 20)

        logger.info("finished logger configuration")

        return run_text_dir, run_json_dir, perf_dir

    def run_index(self) -> None:

        assert self.cfg.index is not None
        index_cfg = self.cfg.index
        self.ctx.start_trace()

        assert set(t.asset_name for t in self.indexer_instances) == set(
            self.cfg.indexers.keys()
        )

        with self.ctx.trace_scope("create runner"):
            runner = IndexRuntime(
                ctx=self.ctx,
                db=self.db,
                indexer_types=self.indexer_instances,
                resource_types=self.resource_instances,
            )

        logger.info(
            f"Enabled indexers {[t.asset_name for t in self.indexer_instances]}"
        )

        run_indexing_per_root_plan(
            db=self.db,
            runner=runner,
            ctx=self.ctx,
            indexers=self.indexer_instances,
            cfg=index_cfg,
        )

    # def get_builders(self) -> list[WidgetBuilder]:
    #     builders = list()
    #     for inst in self.indexer_instances:
    #         match inst:
    #             case ComfyInputIndexer():
    #                 builders.append(ComfyInputWidgetBuilder(inst))

    #             case WdTagIndexer():
    #                 builders.append(WdTaggerWidgetBuilder(inst))

    #             case ExifMetadataIndexer():
    #                 builders.append(ExifPreviewrWidgetBuilder(inst))

    #     return builders

    # def run_flat_query_view(self) -> None:
    #     qt_app = QApplication(sys.argv)

    #     win = FlatQueryViewWindow(
    #         self.db,
    #         collection_names=[t for t in self.cfg.indexers.keys()],
    #         builders=self.get_builders(),
    #     )
    #     win.show()
    #     sys.exit(qt_app.exec())
