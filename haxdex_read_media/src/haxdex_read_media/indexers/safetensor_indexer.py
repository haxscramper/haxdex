import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from loguru import logger
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    ForeignKey,
    MetaData,
    String,
    Table,
    select,
)

result_schema = MetaData()

safetensor = Table(
    "safetensor",
    result_schema,
    Column("hash", String(64), primary_key=True),
    Column("size_bytes", BigInteger, nullable=False),
    Column("header_size", BigInteger, nullable=False),
    Column("num_tensors", BigInteger, nullable=False),
    Column("total_parameters", BigInteger, nullable=False),
    Column("total_tensor_bytes", BigInteger, nullable=False),
)

safetensor_metadata = Table(
    "safetensor_metadata",
    result_schema,
    Column(
        "file_hash",
        String(64),
        ForeignKey("safetensor.hash"),
        primary_key=True,
    ),
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
)

safetensor_tensor = Table(
    "safetensor_tensor",
    result_schema,
    Column(
        "file_hash",
        String(64),
        ForeignKey("safetensor.hash"),
        primary_key=True,
    ),
    Column("name", String, primary_key=True),
    Column("dtype", String, nullable=False),
    Column("shape", ARRAY(BigInteger), nullable=False),
    Column("num_elements", BigInteger, nullable=False),
    Column("byte_size", BigInteger, nullable=False),
)


@dataclass
class _ParsedSafetensor:
    document: dict[str, int]
    metadata: dict[str, str]
    tensors: list[dict[str, Any]]


def _read_header(path: Path) -> tuple[int, dict[str, Any]]:
    with path.open("rb") as file:
        (header_size,) = struct.unpack("<Q", file.read(8))
        header = json.loads(file.read(header_size))

    return header_size, header


def _parse(path: Path) -> _ParsedSafetensor:
    size_bytes = path.stat().st_size
    header_size, header = _read_header(path)
    metadata = header.pop("__metadata__", {})

    tensors: list[dict[str, Any]] = []
    total_parameters = 0
    total_tensor_bytes = 0

    for name, info in header.items():
        shape = list(info["shape"])
        num_elements = 1
        for dimension in shape:
            num_elements *= dimension

        start, end = info["data_offsets"]
        byte_size = end - start

        tensors.append(
            {
                "name": name,
                "dtype": info["dtype"],
                "shape": shape,
                "num_elements": num_elements,
                "byte_size": byte_size,
            }
        )

        total_parameters += num_elements
        total_tensor_bytes += byte_size

    return _ParsedSafetensor(
        document={
            "size_bytes": size_bytes,
            "header_size": header_size,
            "num_tensors": len(tensors),
            "total_parameters": total_parameters,
            "total_tensor_bytes": total_tensor_bytes,
        },
        metadata=metadata,
        tensors=tensors,
    )


class SafetensorIndexer(BaseIndexer):
    asset_name = "safetensor"
    result_schema = result_schema

    def can_run(self, path: Path) -> bool:
        return path.suffix.lower() == ".safetensors"

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()

        if self.database.has_file(file_hash):
            return

        path = ctx.get_path(request.file_ref)
        parsed = _parse(path)

        with self.database.transaction() as connection:
            # Another worker may have indexed identical content while parsing.
            existing = connection.execute(
                select(safetensor.c.hash).where(safetensor.c.hash == file_hash)
            ).first()

            if existing is not None:
                return

            connection.execute(
                safetensor.insert(),
                {
                    "hash": file_hash,
                    **parsed.document,
                },
            )

            if parsed.metadata:
                connection.execute(
                    safetensor_metadata.insert(),
                    [
                        {
                            "file_hash": file_hash,
                            "key": key,
                            "value": value,
                        }
                        for key, value in parsed.metadata.items()
                    ],
                )

            if parsed.tensors:
                connection.execute(
                    safetensor_tensor.insert(),
                    [
                        {
                            "file_hash": file_hash,
                            **tensor,
                        }
                        for tensor in parsed.tensors
                    ],
                )

        logger.info("{} OK", path)
