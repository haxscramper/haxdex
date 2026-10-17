from abc import ABC, abstractmethod

from PyQt6.QtWidgets import QWidget

from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_types import BaseIndexer
from haxdex.services.core.types import FileHash
from haxdex_view_tree.src.haxdex_view_tree.actions.action_handler import ActionResult


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
