import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from loguru import logger
from sqlalchemy import ARRAY, BigInteger, ForeignKey, MetaData, String, insert, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SAFETENSOR_SCHEMA = MetaData()


class SAFETENSOR_BASE(DeclarativeBase):
    metadata = SAFETENSOR_SCHEMA


class Safetensor(SAFETENSOR_BASE):
    __tablename__ = "safetensor"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    header_size: Mapped[int] = mapped_column(BigInteger)
    num_tensors: Mapped[int] = mapped_column(BigInteger)
    total_parameters: Mapped[int] = mapped_column(BigInteger)
    total_tensor_bytes: Mapped[int] = mapped_column(BigInteger)


class SafetensorMetadata(SAFETENSOR_BASE):
    __tablename__ = "safetensor_metadata"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("safetensor.hash"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


class SafetensorTensor(SAFETENSOR_BASE):
    __tablename__ = "safetensor_tensor"

    file_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("safetensor.hash"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String, primary_key=True)
    dtype: Mapped[str] = mapped_column(String)
    shape: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    num_elements: Mapped[int] = mapped_column(BigInteger)
    byte_size: Mapped[int] = mapped_column(BigInteger)


class _Document(TypedDict):
    size_bytes: int
    header_size: int
    num_tensors: int
    total_parameters: int
    total_tensor_bytes: int


class _Tensor(TypedDict):
    name: str
    dtype: str
    shape: list[int]
    num_elements: int
    byte_size: int


@dataclass
class _ParsedSafetensor:
    document: _Document
    metadata: dict[str, str]
    tensors: list[_Tensor]


def _read_header(path: Path) -> tuple[int, dict[str, Any]]:
    with path.open("rb") as file:
        (header_size,) = struct.unpack("<Q", file.read(8))
        header = json.loads(file.read(header_size))

    return header_size, header


def _parse(path: Path) -> _ParsedSafetensor:
    size_bytes = path.stat().st_size
    header_size, header = _read_header(path)
    metadata = header.pop("__metadata__", {})

    tensors: list[_Tensor] = []
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
    result_schema = SAFETENSOR_SCHEMA

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
                select(Safetensor.hash).where(Safetensor.hash == file_hash)
            ).scalar_one_or_none()

            if existing is not None:
                return

            connection.execute(
                insert(Safetensor).values(
                    hash=file_hash,
                    **parsed.document,
                )
            )

            if parsed.metadata:
                connection.execute(
                    insert(SafetensorMetadata),
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
                    insert(SafetensorTensor),
                    [
                        {
                            "file_hash": file_hash,
                            **tensor,
                        }
                        for tensor in parsed.tensors
                    ],
                )

        logger.info("{} OK", path)
