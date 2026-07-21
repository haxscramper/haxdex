from pathlib import Path

from sqlalchemy import event, create_engine, URL, Engine

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


def get_hash_cache_connection(database_path: Path) -> Engine:
    database_path.parent.mkdir(exist_ok=True, parents=True)
    engine = create_engine(
        URL.create("sqlite", database=str(database_path)),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()

    return engine


class IndexDatabase(
        GraphvizMixin,
        SearchMixin,
        StorageMixin,
        SchemaMixin,
        FileReferenceMixin,
        DatabaseBase,
):
    FullTextSearchAccessParams = FullTextSearchAccessParams


__all__ = [
    "BaseIndexProtocol",
    "FullTextSearchAccessParams",
    "IndexDatabase",
    "IndexerResultRecord",
]
