from beartype import beartype
from beartype.typing import Callable
from pydantic import BaseModel, ValidationError
from PyQt6.Qsci import QsciLexerJSON
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hstd_py_qt.file_view.sci_editor import SciEditor


@beartype
class ConfigDialog[ConfigT: BaseModel](QDialog):
    def __init__(
        self,
        config: ConfigT,
        commit: Callable[[ConfigT], None],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)

        self.config_type: type[ConfigT] = type(config)
        self.commit = commit
        self.candidate: ConfigT | None = None

        self.setWindowTitle(f"Edit {self.config_type.__name__}")
        self.setModal(False)
        self.resize(800, 600)

        self.editor = SciEditor(self)
        self.lexer = QsciLexerJSON(self.editor)
        self.editor.set_editor_lexer(self.lexer)

        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(160)

        self.apply_button = QPushButton("Apply", self)
        self.close_button = QPushButton("Close", self)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self.editor.textChanged.connect(self.validate_config)
        self.apply_button.clicked.connect(self.apply_config)
        self.close_button.clicked.connect(self.close_dialog)

        self.load_config(config)

    def load_config(self, config: ConfigT) -> None:
        self.editor.setText(config.model_dump_json(indent=2))
        self.validate_config()

    def validate_config(self) -> None:
        try:
            self.candidate = self.config_type.model_validate_json(self.editor.text())
        except ValidationError as error:
            self.candidate = None
            self.apply_button.setEnabled(False)
            self.status.setText(str(error))
        else:
            self.apply_button.setEnabled(True)
            self.status.setText("Configuration is valid; changes are not applied.")

    def apply_config(self, checked: bool) -> None:
        self.validate_config()
        if self.candidate is not None:
            self.commit(self.candidate)
            self.status.setText("Configuration applied.")

    def close_dialog(self, checked: bool) -> None:
        self.close()


@beartype
class Configurable[ConfigT: BaseModel]:
    """Adds configuration handling without introducing a Qt superclass."""

    def initialize_config(
        self,
        config: ConfigT,
        *,
        parent: QWidget,
        apply: Callable[[ConfigT], None],
    ) -> None:
        self.config_type: type[ConfigT] = type(config)
        self.config_parent = parent
        self.config_apply = apply
        self.config_dialog: ConfigDialog[ConfigT] | None = None
        self.set_config(config)

    def get_config(self) -> ConfigT:
        return self.config.model_copy(deep=True)

    def set_config(self, config: ConfigT) -> None:
        validated = self.config_type.model_validate(config.model_dump(mode="python"))

        self.config_apply(validated.model_copy(deep=True))
        self.config = validated

    def show_config_dialog(self) -> None:
        if self.config_dialog is None:
            self.config_dialog = ConfigDialog(
                config=self.get_config(),
                commit=self.set_config,
                parent=self.config_parent,
            )
        elif not self.config_dialog.isVisible():
            self.config_dialog.load_config(self.get_config())

        self.config_dialog.show()
        self.config_dialog.raise_()
        self.config_dialog.activateWindow()
