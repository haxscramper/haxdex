from haxdex.services.core.db_impl.base import DatabaseBase
from haxdex.services.core.db_impl.contracts import (
    BaseIndexProtocol,
    FullTextSearchAccessParams,
    IndexerResultRecord,
)
from haxdex.services.core.db_impl.files import FileReferenceMixin
from haxdex.services.core.db_impl.graphviz import GraphvizMixin
from haxdex.services.core.db_impl.schema import SchemaMixin
from haxdex.services.core.db_impl.search import SearchMixin
from haxdex.services.core.db_impl.storage import StorageMixin


class IndexDatabase(
        GraphvizMixin,
        SearchMixin,
        StorageMixin,
        SchemaMixin,
        FileReferenceMixin,
        DatabaseBase,
):
    """
    Public database API.

    Implementation details are split across ``db_impl`` modules while this
    class preserves the original import path and public method surface.
    """

    FullTextSearchAccessParams = FullTextSearchAccessParams


__all__ = [
    "BaseIndexProtocol",
    "FullTextSearchAccessParams",
    "IndexDatabase",
    "IndexerResultRecord",
]
