from abc import ABC, abstractmethod

from haxdex_action.action_handler import ActionResult
from haxdex_core.db import IndexDatabase
from haxdex_core.job_types import BaseIndexer
from haxdex_core.types import FileHash
from PyQt6.QtWidgets import QWidget


class WidgetBuilder(ABC):
    indexer: BaseIndexer

    def __init__(self, indexer: BaseIndexer) -> None:
        super().__init__()
        self.indexer = indexer

    @property
    def asset_name(self) -> str:
        return self.indexer.asset_name

    @abstractmethod
    def build(
        self, db: IndexDatabase, hash: FileHash, action: ActionResult | None = None
    ) -> QWidget:
        raise NotImplementedError()
