import json

from arango import DocumentGetError
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.types import FileHash
from haxdex_action.action_handler import ActionResult
from haxdex_view_results.builder import WidgetBuilder
from haxdex_view_results.json_preview_widget import JsonPreviewWidget
from PyQt6.QtWidgets import QWidget


class JsonWidgetBuilder(WidgetBuilder):
    def __init__(self, collection_name: str) -> None:
        self._collection_name = collection_name

    def build(
        self,
        db: IndexDatabase,
        hash: FileHash,
        action: ActionResult | None = None,
    ) -> QWidget:
        try:
            doc = db._db.collection(self._collection_name).get(hash.hash)

        except DocumentGetError as err:
            doc = str(json.dumps(dict(err=str(err))))

        widget = JsonPreviewWidget()
        widget.set_doc(doc)
        return widget
