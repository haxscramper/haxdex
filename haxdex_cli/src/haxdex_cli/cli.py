import argparse
import json
import logging
import sys
import traceback
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import commentjson
from beartype import beartype
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_runtime import IndexRuntime
from haxdex.services.core.job_types import BaseIndexer, RunContext
from hstd_py_lib.pydantic_utils import model_to_json_data
from loguru import logger

from haxdex.gui.flat_query_preview.window import FlatQueryViewWindow
from haxdex.services.log_config import keep_last_files
from haxdex.services.utils import (
    get_custom_traceback_handler,
    get_xdg_cache_dir,
    stfu_logs,
)
from haxdex_cli.src.haxdex_cli.cli_config import (
    _INDEXER_TYPES,
    _RESOURCE_TYPES,
    AppConfig,
    LoggingConfig,
)
from haxdex_core.src.haxdex_core.hash_cache import HashCache
from haxdex_core.src.haxdex_core.indexing_flow import run_indexing_per_root_plan
from haxdex_read_media.src.haxdex_read_media.indexers.comfy_input_indexer import (
    ComfyInputIndexer,
)
from haxdex_read_media.src.haxdex_read_media.indexers.exif_metadata import (
    ExifMetadataIndexer,
)
from haxdex_read_media.src.haxdex_read_media.indexers.wd_indexer import WdTagIndexer
from haxdex_view_results.src.haxdex_view_results.builder import WidgetBuilder
from haxdex_view_results.src.haxdex_view_results.comfy_input_builder import (
    ComfyInputWidgetBuilder,
)
from haxdex_view_results.src.haxdex_view_results.exif_preview_builder import (
    ExifPreviewrWidgetBuilder,
)
from haxdex_view_results.src.haxdex_view_results.wd_tagger_builder import (
    WdTaggerWidgetBuilder,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s %(filename)s:%(lineno)d: %(message)s",
)


def showwarning(message, category, filename, lineno, file=None, line=None):
    print(f"{filename}:{lineno}: {category.__name__}: {message}")
    traceback.print_stack()


warnings.showwarning = showwarning


@beartype
class IndexService:
    @staticmethod
    def reset_for_config(cfg: AppConfig) -> None:
        IndexDatabase.reset_database(cfg.db.directory)

    def get_indexer(self, s: str) -> BaseIndexer:
        for i in self.indexer_instances:
            if i.asset_name == s:
                return i

        assert False, f"No indexer named {s}"

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.resource_instances = []
        self.indexer_instances: list[BaseIndexer] = []

        enabled_resources = set(cfg.resources)
        enabled_indexers = set(cfg.indexers)

        for resource_type in _RESOURCE_TYPES:
            key = resource_type.resource_key
            if key not in enabled_resources:
                continue

            missing = set(resource_type.required_resources) - enabled_resources
            if missing:
                raise ValueError(
                    f"Resource {key!r} requires resources: {sorted(missing)}"
                )

            self.resource_instances.append(resource_type(config=cfg.resources[key]))

        for indexer_type in _INDEXER_TYPES:
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
            for indexer_type in _INDEXER_TYPES:
                key = indexer_type.asset_name
                if key not in enabled_indexers:
                    continue

                database = self.db.open_indexer(
                    asset_name=key,
                    result_schema=indexer_type.result_schema,
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

        handler = get_custom_traceback_handler(show_args=False)

        def impl(exc_type: Any, exc_value: Any, exc_traceback: Any):
            print(handler(exc_type, exc_value, exc_traceback))

        sys.excepthook = impl
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

    def get_builders(self) -> list[WidgetBuilder]:
        builders = list()
        for inst in self.indexer_instances:
            match inst:
                case ComfyInputIndexer():
                    builders.append(ComfyInputWidgetBuilder(inst))

                case WdTagIndexer():
                    builders.append(WdTaggerWidgetBuilder(inst))

                case ExifMetadataIndexer():
                    builders.append(ExifPreviewrWidgetBuilder(inst))

        return builders

    def run_flat_query_view(self) -> None:
        qt_app = QApplication(sys.argv)

        win = FlatQueryViewWindow(
            self.db,
            collection_names=[t for t in self.cfg.indexers.keys()],
            builders=self.get_builders(),
        )
        win.show()
        sys.exit(qt_app.exec())


def main_impl(command: str, cfg: AppConfig):
    if command == "index" and cfg.index and cfg.index.reset:
        IndexService.reset_for_config(cfg)

    service = IndexService(cfg)

    _, _, perf_dir = service.setup_runtime_logging(service.cfg.logging)
    logger.debug(json.dumps(model_to_json_data(cfg), indent=2))
    service.ctx.start_trace()

    try:
        service.run_index()

    except Exception as ex:
        logger.opt(exception=ex).critical("{}", ex)
        raise

    finally:
        service.db.hash_cache.close()
        perf_file = perf_dir / f"{datetime.now().isoformat()}.json"
        service.ctx.writer.save(str(perf_file))
        keep_last_files(perf_dir, "*.json", 20)

        if service.cfg.perf_trace_file is not None:
            logger.info(f"Trace file {service.cfg.perf_trace_file}")
            service.ctx.writer.save(str(service.cfg.perf_trace_file))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", help="Which action to execute")
    parser.add_argument("config", type=Path, help="Path to JSON config file")
    args = parser.parse_args()

    stfu_logs()

    cfg_path = Path(args.config).expanduser().resolve().absolute()
    payload = commentjson.loads(cfg_path.read_text())
    cfg = AppConfig.model_validate(payload)

    if args.command == "schema":
        cfg_path.with_stem(cfg_path.stem + "_schema").with_suffix(".json").write_text(
            json.dumps(AppConfig.model_json_schema(), indent=2)
        )
        return

    else:
        main_impl(args.command, cfg)


if __name__ == "__main__":
    main()
