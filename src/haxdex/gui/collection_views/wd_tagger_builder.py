from PyQt6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem, QWidget
from beartype.typing import Optional, cast

from haxdex.gui.collection_views.builder import WidgetBuilder
from haxdex.gui.collection_views.json_preview_widget import JsonPreviewWidget
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.job_types import BaseIndexer
from haxdex.services.indexers.wd_indexer import WdTagIndexer, WdTagIndexerResult
from haxdex.services.resources.wd_tagger import WdTaggerResult
from haxdex.services.core.types import FileHash


class WdTaggerWidgetBuilder(WidgetBuilder):

    def build(self, db: IndexDatabase, hash: FileHash) -> QWidget:

        if db.has_indexer_result(hash, self.indexer):
            result = cast(WdTagIndexerResult, db.get_indexer_result(hash, self.indexer))

            table = QTableWidget(len(result.tags), 3)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setHorizontalHeaderLabels(["category", "name", "probability"])
            for row, tag in enumerate(result.tags):
                table.setItem(row, 0, QTableWidgetItem(tag.category))
                table.setItem(row, 1, QTableWidgetItem(tag.name))
                table.setItem(row, 2, QTableWidgetItem(f"{tag.probability:.4f}"))
            table.horizontalHeader().setStretchLastSection(True)
            return table

        else:
            widget = JsonPreviewWidget()
            widget.set_doc(None)
            return widget
