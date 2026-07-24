import logging

from beartype.typing import Any

from haxdex.services.utils import stfu_logs, propagate_logger_level


def pytest_configure(config: Any) -> None:
    stfu_logs()
    propagate_logger_level("haxdex", logging.WARNING)
