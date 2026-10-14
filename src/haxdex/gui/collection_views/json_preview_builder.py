import json

from arango import DocumentGetError
from PyQt6.QtWidgets import QWidget

from haxdex.gui.collection_views.builder import WidgetBuilder
from haxdex.gui.collection_views.json_preview_widget import JsonPreviewWidget
from haxdex.gui.file_tree.actions.action_handler import ActionResult
from haxdex.services.core.db import IndexDatabase
from haxdex.services.core.types import FileHash


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
