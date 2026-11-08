import itertools
from io import StringIO

from beartype import beartype
from haxdex_read_logs.clang_segment_format import (
    format_clang_segment_group,
)
from haxdex_read_logs.clang_type_model import (
    ClangSegment,
)
from haxdex_read_logs.top_types import CompileLog, TextSegment
from rich.console import Console
from rich.text import Text


@beartype
def format_compile_log(log: CompileLog, *, color: bool = False) -> str:
    """Deduplicate diagnostics across the log; preserve text segments verbatim."""
    output = StringIO()

    for type_key, segment_list in itertools.groupby(
        log.segments, key=lambda it: type(it)
    ):
        match type_key:
            case _ if type_key == TextSegment:
                for segment in segment_list:
                    output.write(segment.text)

            case _ if type_key == ClangSegment:
                format_clang_segment_group(
                    segment_list=list(segment_list),
                    output=output,
                    color=color,
                )

    return output.getvalue()


from rich.terminal_theme import MONOKAI, TerminalTheme


@beartype
def format_compile_log_html(
    log: CompileLog,
    *,
    theme: TerminalTheme = MONOKAI,
) -> str:
    console = Console(
        file=StringIO(),
        record=True,
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        markup=False,
        highlight=False,
        soft_wrap=True,
    )

    console.print(
        Text.from_ansi(
            format_compile_log(log, color=True),
            no_wrap=True,
            overflow="ignore",
        ),
        end="",
    )

    return console.export_html(
        theme=theme,
        inline_styles=True,
        clear=False,
    )
