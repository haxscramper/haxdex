import itertools
from io import StringIO

from beartype import beartype
from beartype.typing import cast
from haxdex_read_logs.clang_segment_format import (
    format_clang_segment_group,
)
from haxdex_read_logs.clang_type_model import ClangSegment
from haxdex_read_logs.top_types import (
    CompileLog,
    TextSegment,
)
from rich.console import Console
from rich.terminal_theme import MONOKAI, TerminalTheme
from rich.text import Text


@beartype
def format_compile_log(
    log: CompileLog,
    *,
    color: bool = False,
) -> str:
    """Format adjacent groups, including nested Clang diagnostic groups."""
    from haxdex_read_logs.top_processor import (
        format_segment_group,
    )

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=color,
        color_system="truecolor" if color else None,
        no_color=not color,
        markup=False,
        highlight=False,
        soft_wrap=True,
    )

    for type_key, segment_list in itertools.groupby(
        log.segments,
        key=type,
    ):
        segments = list(segment_list)

        match type_key:
            case _ if type_key == TextSegment:
                for segment in cast(list[TextSegment], segments):
                    output.write(segment.text)

            case _ if type_key == ClangSegment:
                format_clang_segment_group(
                    segment_list=cast(list[ClangSegment], segments),
                    output=output,
                    color=color,
                )

            case _:
                console.print(
                    format_segment_group(segments),
                    end="",
                )

    return output.getvalue()


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
