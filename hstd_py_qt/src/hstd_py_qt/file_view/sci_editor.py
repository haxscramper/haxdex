from beartype import beartype
from loguru import logger
from PyQt6.Qsci import QsciLexer, QsciScintilla
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QKeyEvent
from PyQt6.QtWidgets import QWidget


@beartype
class SciEditor(QsciScintilla):
    """Language-agnostic editor configuration and editing behavior."""

    delimiter_pairs = {
        "(": ")",
        "[": "]",
        "{": "}",
        "'": "'",
        '"': '"',
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor_font = QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont,
        )
        self.editor_font.setFixedPitch(True)
        self.configure_editor()

    def configure_editor(self) -> None:
        self.setFont(self.editor_font)
        self.setUtf8(True)

        self.setAutoIndent(True)
        self.setIndentationsUseTabs(False)
        self.setIndentationWidth(4)
        self.setTabWidth(4)
        self.setIndentationGuides(True)

        self.setBraceMatching(
            QsciScintilla.BraceMatch.SloppyBraceMatch,
        )
        self.setCaretLineVisible(True)

        self.setMarginType(
            0,
            QsciScintilla.MarginType.NumberMargin,
        )
        self.setMarginLineNumbers(0, True)
        self.setMarginWidth(0, "00000")

        self.setWrapMode(QsciScintilla.WrapMode.WrapNone)
        self.setAnnotationDisplay(
            QsciScintilla.AnnotationDisplay.AnnotationBoxed,
        )

        self.SendScintilla(QsciScintilla.SCI_AUTOCSETMAXHEIGHT, 12)

        # Multi-cursor editing support
        self.SendScintilla(QsciScintilla.SCI_SETMULTIPLESELECTION, True)
        self.SendScintilla(QsciScintilla.SCI_SETADDITIONALSELECTIONTYPING, True)
        self.SendScintilla(
            QsciScintilla.SCI_SETMULTIPASTE,
            QsciScintilla.SC_MULTIPASTE_EACH,
        )

        current_width = self.SendScintilla(QsciScintilla.SCI_AUTOCGETMAXWIDTH)
        base_width = current_width if 0 < current_width else 40
        self.SendScintilla(
            QsciScintilla.SCI_AUTOCSETMAXWIDTH,
            int(base_width * 1.5),
        )

    def set_editor_lexer(self, lexer: QsciLexer) -> None:
        lexer.setDefaultFont(self.editor_font)

        for style in range(1 << lexer.styleBitsNeeded()):
            lexer.setFont(self.editor_font, style)

        self.setLexer(lexer)

    def select_next_occurrence(self) -> None:
        """Select the next exact substring, with its caret at the start."""
        main = self.SendScintilla(QsciScintilla.SCI_GETMAINSELECTION)
        start = self.SendScintilla(
            QsciScintilla.SCI_GETSELECTIONNSTART,
            main,
        )
        end = self.SendScintilla(
            QsciScintilla.SCI_GETSELECTIONNEND,
            main,
        )

        if start == end:
            start = self.SendScintilla(
                QsciScintilla.SCI_WORDSTARTPOSITION,
                start,
                True,
            )
            end = self.SendScintilla(
                QsciScintilla.SCI_WORDENDPOSITION,
                end,
                True,
            )
            self.SendScintilla(QsciScintilla.SCI_SETSEL, end, start)
            return

        source = self.text().encode("utf-8")
        selected = source[start:end]
        count = self.SendScintilla(QsciScintilla.SCI_GETSELECTIONS)
        selections = [
            (
                self.SendScintilla(
                    QsciScintilla.SCI_GETSELECTIONNSTART,
                    index,
                ),
                self.SendScintilla(
                    QsciScintilla.SCI_GETSELECTIONNEND,
                    index,
                ),
            )
            for index in range(count)
        ]

        self.SendScintilla(
            QsciScintilla.SCI_SETSELECTIONNANCHOR,
            main,
            end,
        )
        self.SendScintilla(
            QsciScintilla.SCI_SETSELECTIONNCARET,
            main,
            start,
        )

        search_flags = self.SendScintilla(QsciScintilla.SCI_GETSEARCHFLAGS)
        try:
            self.SendScintilla(
                QsciScintilla.SCI_SETSEARCHFLAGS,
                QsciScintilla.SCFIND_MATCHCASE,
            )

            for offset, limit in ((end, len(source)), (0, start)):
                while offset < limit:
                    self.SendScintilla(
                        QsciScintilla.SCI_SETTARGETSTART,
                        offset,
                    )
                    self.SendScintilla(
                        QsciScintilla.SCI_SETTARGETEND,
                        limit,
                    )
                    match_start = self.SendScintilla(
                        QsciScintilla.SCI_SEARCHINTARGET,
                        len(selected),
                        selected,
                    )
                    if match_start == -1:
                        break

                    match_end = match_start + len(selected)
                    overlaps = any(
                        match_start < selection_end and selection_start < match_end
                        for selection_start, selection_end in selections
                    )
                    if not overlaps:
                        self.SendScintilla(
                            QsciScintilla.SCI_ADDSELECTION,
                            match_start,
                            match_end,
                        )
                        self.SendScintilla(
                            QsciScintilla.SCI_SETMAINSELECTION,
                            count,
                        )
                        self.SendScintilla(QsciScintilla.SCI_SCROLLCARET)
                        return

                    offset = match_end
        finally:
            self.SendScintilla(
                QsciScintilla.SCI_SETSEARCHFLAGS,
                search_flags,
            )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        modifiers = event.modifiers()
        key = event.key()

        match (key, modifiers):
            case (Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier):
                logger.info("Registered CTRL D event")
                self.select_next_occurrence()
                event.accept()
                return

            case (Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier):
                if not self.hasSelectedText() and self.delete_blank_line_indentation():
                    event.accept()
                    return

            case (Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier):
                self.SendScintilla(QsciScintilla.SCI_LINEDELETE)
                event.accept()
                return

            case (Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier):
                self.SendScintilla(QsciScintilla.SCI_MOVESELECTEDLINESUP)
                event.accept()
                return

            case (Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier):
                self.SendScintilla(QsciScintilla.SCI_MOVESELECTEDLINESDOWN)
                event.accept()
                return

            case (
                Qt.Key.Key_Z,
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
            ):
                self.SendScintilla(QsciScintilla.SCI_UNDO)
                event.accept()
                return

        opening = event.text()
        if (
            self.hasSelectedText()
            and opening in self.delimiter_pairs
            and not modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        ):
            selected = self.selectedText()
            closing = self.delimiter_pairs[opening]
            self.replaceSelectedText(f"{opening}{selected}{closing}")
            event.accept()
            return

        super().keyPressEvent(event)

    def delete_blank_line_indentation(self) -> bool:
        line, column = self.getCursorPosition()
        content = self.text(line).rstrip("\r\n")

        if column == 0 or content.strip():
            return False

        width = self.indentationWidth()
        delete_count = column % width or width
        start_column = max(0, column - delete_count)

        self.setSelection(line, start_column, line, column)
        self.replaceSelectedText("")
        return True
