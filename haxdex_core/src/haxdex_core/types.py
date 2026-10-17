from __future__ import annotations

from pydantic import BaseModel


class FileHash(BaseModel, extra="forbid", frozen=True):
    hash: str

    def __repr__(self) -> str:
        return self.hash


class RootRef(BaseModel, extra="forbid", frozen=True):
    name: str


class FileRef(BaseModel, extra="forbid", frozen=True):
    hash: FileHash
    relative: str
    root: RootRef


class IndexerRequest(BaseModel, extra="forbid", frozen=True):
    file_ref: FileRef

    def get_hash_str(self) -> str:
        return self.file_ref.hash.hash
