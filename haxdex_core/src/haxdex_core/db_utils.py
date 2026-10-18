from sqlalchemy import (
    Integer,
    Sequence,
)
from sqlalchemy.orm import MappedColumn, mapped_column


def duck_id_autoincrement(cls) -> MappedColumn[int]:
    sequence = Sequence(f"{cls.__tablename__}_id_seq")
    return mapped_column(
        Integer,
        sequence,
        server_default=sequence.next_value(),
        primary_key=True,
    )
