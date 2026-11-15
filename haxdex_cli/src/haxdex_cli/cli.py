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
from haxdex_core.core_config import IndexServiceConfig
from haxdex_core.utils import (
    stfu_logs,
)
from hstd_py_lib.pydantic_utils import model_to_json_data
from hstd_py_lib.script_logging import get_custom_traceback_handler
from loguru import logger

from haxdex.services.log_config import keep_last_files

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s %(filename)s:%(lineno)d: %(message)s",
)


def showwarning(message, category, filename, lineno, file=None, line=None):
    print(f"{filename}:{lineno}: {category.__name__}: {message}")
    traceback.print_stack()


warnings.showwarning = showwarning


def main() -> None:
    handler = get_custom_traceback_handler(show_args=False)

    def impl(exc_type: Any, exc_value: Any, exc_traceback: Any):
        print(handler(exc_type, exc_value, exc_traceback))

    sys.excepthook = impl

    parser = argparse.ArgumentParser()
    parser.add_argument("command", help="Which action to execute")
    parser.add_argument("config", type=Path, help="Path to JSON config file")
    args = parser.parse_args()

    stfu_logs()

    cfg_path = Path(args.config).expanduser().resolve().absolute()
    payload = commentjson.loads(cfg_path.read_text())
    cfg = IndexServiceConfig.model_validate(payload)

    if args.command == "schema":
        cfg_path.with_stem(cfg_path.stem + "_schema").with_suffix(".json").write_text(
            json.dumps(IndexServiceConfig.model_json_schema(), indent=2)
        )
        return

    else:
        assert args.command == "index"
        if cfg.index and cfg.index.reset:
            IndexService.reset_for_config(cfg)

        service = IndexService(cfg)

        _, _, perf_dir = service.setup_runtime_logging(service.cfg.logging)
        logger.debug(json.dumps(model_to_json_data(cfg), indent=2))
        service.ctx.start_trace()

        try:
            service.run_index()

        finally:
            service.db.hash_cache.close()
            perf_file = perf_dir / f"{datetime.now().isoformat()}.json"
            service.ctx.writer.save(str(perf_file))
            keep_last_files(perf_dir, "*.json", 20)

            if service.cfg.perf_trace_file is not None:
                logger.info(f"Trace file {service.cfg.perf_trace_file}")
                service.ctx.writer.save(str(service.cfg.perf_trace_file))

        logger.info("Completed indexing")


if __name__ == "__main__":
    main()
