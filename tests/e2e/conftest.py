from beartype.typing import Any
from haxdex_core.utils import stfu_logs


def pytest_configure(config: Any) -> None:
    stfu_logs()
    # propagate_logger_level("haxdex", logging.DEBUG)
