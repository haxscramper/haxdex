from abc import ABC, abstractmethod

from beartype import beartype
from PyQt6.QtWidgets import QWidget


@beartype
class FileContentViewBuilder(ABC):
    @abstractmethod
    def can_build(self, mime: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def build(self, absolute_path: str) -> QWidget:
        raise NotImplementedError
