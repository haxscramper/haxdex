import argparse
import json
from loguru import logger
import sys
import traceback
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from beartype import beartype
from PyQt6.QtWidgets import QApplication
from sqlalchemy import event, create_engine, URL

from haxdex.cli.cli_config import _INDEXER_TYPES, _RESOURCE_TYPES, AppConfig, LoggingConfig
from haxdex.gui.collection_views.builder import WidgetBuilder
from haxdex.gui.collection_views.comfy_input_builder import (
    ComfyInputWidgetBuilder,)
from haxdex.gui.collection_views.exif_preview_builder import (
    ExifPreviewrWidgetBuilder,)
from haxdex.gui.collection_views.wd_tagger_builder import WdTaggerWidgetBuilder
from haxdex.gui.file_tree.actions.action_execute import ActionExecutor
from haxdex.gui.file_tree.actions.action_list_model import load_actions
from haxdex.gui.file_tree.qt_tree_window import FileTreeQueryWindow
from haxdex.gui.flat_query_preview.window import FlatQueryViewWindow
from haxdex.services.core.db import IndexDatabase, get_hash_cache_connection
from haxdex.services.core.hash_cache import HashCache
from haxdex.services.core.indexing_flow import run_indexing_per_root_plan
from haxdex.services.core.job_runtime import IndexRuntime
from haxdex.services.core.job_types import BaseIndexer, RunContext
import commentjson

from haxdex.services.indexers.comfy_input_indexer import ComfyInputIndexer
from haxdex.services.indexers.exif_metadata import ExifMetadataIndexer
from haxdex.services.indexers.wd_indexer import WdTagIndexer
from haxdex.services.log_config import JsonlFormatter, keep_last_files
from haxdex.services.pydantic_utils import model_to_json_data

from haxdex.services.utils import (
    dump_with_type,
    get_custom_traceback_handler,
    get_xdg_cache_dir,
    stfu_logs,
)
from haxdex.visual.trash_action_visual import visualize_trash_actions
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s %(filename)s:%(lineno)d: %(message)s",
)


def showwarning(message, category, filename, lineno, file=None, line=None):
    print(f"{filename}:{lineno}: {category.__name__}: {message}")
    traceback.print_stack()


warnings.showwarning = showwarning


@beartype
class IndexService():

    @staticmethod
    def reset_for_config(cfg: AppConfig):
        IndexDatabase.reset_database(
            host=cfg.db.host,
            db_name=cfg.db.db_name,
            username=cfg.db.username,
            password=cfg.db.password,
        )

    def get_indexer(self, s: str) -> BaseIndexer:
        for i in self.indexer_instances:
            if i.asset_name == s:
                return i

        assert False, f"No indexer named {s}"

    def __init__(self, cfg: AppConfig, only_short_curcuit_checks: bool) -> None:
        self.cfg = cfg
        self.indexer_instances = list()
        self.resource_instances = list()

        self.resource_instances = []
        for t in _RESOURCE_TYPES:
            key = t.resource_key
            if key not in self.cfg.resources:
                continue

            for dep in t.required_resources:
                if dep not in self.cfg.resources:
                    raise ValueError(
                        f"Resource '{key}' requires resource '{dep}' to be enabled")

            resource_cfg = self.cfg.resources[key]
            self.resource_instances.append(t(config=resource_cfg))

        self.indexer_connection = get_hash_cache_connection(self.cfg.index_cache)
        self.indexer_instances: list[BaseIndexer] = []
        for t in _INDEXER_TYPES:
            key = t.asset_name
            if key not in self.cfg.indexers:
                continue

            for dep in t.required_resources:
                if dep not in self.cfg.resources:
                    raise ValueError(
                        f"Indexer '{key}' requires resource '{dep}' to be enabled")

            for dep in t.required_assets:
                if dep not in self.cfg.indexers:
                    raise ValueError(
                        f"Indexer '{key}' requires indexer '{dep}' to be enabled")

            cfg = self.cfg.indexers[key]
            instance = t(
                database=self.indexer_connection,
                config=cfg,
            )

            self.indexer_instances.append(instance)

        self.db = IndexDatabase(
            host=self.cfg.db.host,
            db_name=self.cfg.db.db_name,
            username=self.cfg.db.username,
            password=self.cfg.db.password,
            hash_cache=HashCache(Path(self.cfg.hash_cache).expanduser().absolute()),
            only_short_curcuit_checks=only_short_curcuit_checks,
        )

        self.ctx = RunContext(self.db)

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
            self.cfg.indexers.keys())

        with self.ctx.trace_scope("create runner"):
            runner = IndexRuntime(
                ctx=self.ctx,
                db=self.db,
                indexer_types=self.indexer_instances,
                resource_types=self.resource_instances,
            )

        for idx in self.indexer_instances:
            self.db.enable_index(idx)

        logger.info(f"Enabled indexers {[t.asset_name for t in self.indexer_instances]}")

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

    def run_tree_view(self) -> None:
        qt_app = QApplication(sys.argv)

        assert self.cfg.file_tree_view
        win = FileTreeQueryWindow(
            ctx=self.ctx,
            db=self.db,
            indexer_instances=self.indexer_instances,
            builders=self.get_builders(),
            cfg=self.cfg,
        )

        win.show()
        sys.exit(qt_app.exec())


def main_impl(command: str, cfg: AppConfig):
    if command == "index" and cfg.index and cfg.index.reset:
        IndexService.reset_for_config(cfg)

    service = IndexService(
        cfg,
        only_short_curcuit_checks=command != "index",
    )

    _, _, perf_dir = service.setup_runtime_logging(service.cfg.logging)
    logger.debug(json.dumps(model_to_json_data(cfg), indent=2))
    service.ctx.start_trace()

    def register_db_actions() -> ActionExecutor:
        assert cfg.act
        executor = ActionExecutor(cfg.act.execution)
        actions = load_actions(cfg.action_file, list(executor.handlers.values()))
        executor.init_db()
        executor.register_actions(actions)
        return executor

    try:
        match command:
            case "index":
                service.run_index()

            case "flat_query_view":
                service.run_flat_query_view()

            case "file_tree_view":
                if cfg.act:
                    executor = ActionExecutor(cfg.act.execution)
                    if cfg.action_file.exists():
                        actions = load_actions(cfg.action_file,
                                               list(executor.handlers.values()))
                        executor.init_db()
                        executor.register_actions(actions)

                service.run_tree_view()

            case "visual":
                assert service.cfg.visual
                assert service.cfg.visual.trash
                visualize_trash_actions(service.cfg.visual.trash, cfg.action_file)

            case "do_act":
                executor = register_db_actions()
                executor.execute_pending()

            case "undo_act":
                assert cfg.act
                executor = ActionExecutor(cfg.act.execution)
                actions = load_actions(cfg.action_file, list(executor.handlers.values()))
                executor.init_db()
                executor.register_actions(actions)
                executor.revert_done()

            case _:
                raise ValueError(f"Unexpected command {command}")

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
            json.dumps(AppConfig.model_json_schema(), indent=2))
        return

    else:
        main_impl(args.command, cfg)


if __name__ == "__main__":
    main()
