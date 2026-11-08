import argparse
from pathlib import Path

from beartype import beartype
from haxdex_read_logs.format_log_general import (
    format_compile_log,
    format_compile_log_html,
)
from haxdex_read_logs.top_types import CompileLog
from loguru import logger


@beartype
def segment_compile_logs(text: str) -> CompileLog:
    """Compose supported log formats while preserving unmatched text."""
    from haxdex_read_logs.top_processor import TopProcessor

    return TopProcessor().parse_structured(text)


@beartype
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract grouped Clang diagnostics from a build log."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()

    with arguments.input.open(
        "r",
        newline="",
    ) as stream:
        result = segment_compile_logs(stream.read())

    Path("/tmp/parsed_log.json").write_text(result.model_dump_json(indent=2))

    if str(arguments.output).endswith(".html"):
        arguments.output.write_text(format_compile_log_html(result))
    else:
        arguments.output.write_text(format_compile_log(result))

    logger.info(f"Wrote result to {arguments.output}")


if __name__ == "__main__":
    main()
