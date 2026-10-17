from functools import cached_property
from pathlib import Path
from typing import ClassVar

import plumbum
from haxdex_core.database import IndexerStore
from haxdex_core.job_types import BaseIndexer, BaseResource, RunContext
from haxdex_core.types import IndexerRequest
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

import haxdex_read_media.indexers.full_document.full_document_types as doc_types
from haxdex_read_media.indexers.full_document.from_pandoc import (
    pandoc_to_document,
)
from haxdex_read_media.indexers.full_document.full_document_tables import (
    FULL_DOCUMENT_SCHEMA,
    DocumentFragment,
    DocumentNesting,
    FullDocumentFile,
)

type PendingNesting = tuple[
    DocumentFragment,
    DocumentFragment | None,
    int,
]


def _fragment_plaintext(block: doc_types.DocumentBlock) -> str:
    content = getattr(block, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, doc_types.InlineContent) and content.plaintext:
        return content.plaintext

    return "\n".join(
        text for nested in block.nested if (text := _fragment_plaintext(nested))
    )


def _flatten(
    block: doc_types.DocumentBlock,
    *,
    file_id: int,
    parent: DocumentFragment | None,
    order: int,
    fragments: list[DocumentFragment],
    nesting: list[PendingNesting],
) -> None:
    structured_element = block.model_dump(
        mode="json",
        exclude={"nested"},
        serialize_as_any=True,
    )

    fragment = DocumentFragment(
        file_id=file_id,
        type=structured_element["type"],
        plaintext=_fragment_plaintext(block),
        structured_element=structured_element,
    )
    fragments.append(fragment)
    nesting.append((fragment, parent, order))

    for nested_order, nested in enumerate(block.nested):
        _flatten(
            nested,
            file_id=file_id,
            parent=fragment,
            order=nested_order,
            fragments=fragments,
            nesting=nesting,
        )


class DocumentBlockIndexer(BaseIndexer):
    asset_name = "document_block"
    result_schema = FULL_DOCUMENT_SCHEMA
    table = FullDocumentFile.__table__
    max_parallel = 8

    extension_to_reader: ClassVar[dict[str, str]] = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".mkd": "markdown",
        ".mdown": "markdown",
        ".rst": "rst",
        ".tex": "latex",
        ".html": "html",
        ".htm": "html",
        ".docx": "docx",
        ".odt": "odt",
        ".epub": "epub",
        ".org": "org",
        ".txt": "markdown",
    }

    @cached_property
    def supported_readers(self) -> set[str]:
        return set(
            plumbum.local["pandoc"].run(["--list-input-formats"])[1].splitlines()
        )

    def can_run(self, path: Path) -> bool:
        if not path.is_file():
            return False

        reader = self.extension_to_reader.get(path.suffix.lower())
        return reader is not None and reader in self.supported_readers

    def run(
        self,
        ctx: RunContext,
        request: IndexerRequest,
        resources: dict[str, BaseResource],
        assets: dict[str, IndexerStore],
    ) -> None:
        file_hash = request.get_hash_str()
        path = ctx.get_path(request.file_ref)

        with (
            self.database.transaction() as connection,
            Session(bind=connection) as session,
        ):
            existing = session.scalar(
                select(FullDocumentFile).where(FullDocumentFile.hash == file_hash)
            )
            if existing is not None:
                return

            logger.info(f"Converting {path} to full document")

            root = pandoc_to_document(
                path,
                reader=self.extension_to_reader[path.suffix.lower()],
            )

            document_file = FullDocumentFile(hash=file_hash)
            session.add(document_file)
            session.flush()

            fragments: list[DocumentFragment] = []
            nesting: list[PendingNesting] = []

            for order, block in enumerate(root.nested):
                _flatten(
                    block,
                    file_id=document_file.id,
                    parent=None,
                    order=order,
                    fragments=fragments,
                    nesting=nesting,
                )

            session.add_all(fragments)
            session.flush()

            session.add_all(
                DocumentNesting(
                    nested_id=fragment.id,
                    parent_id=parent.id if parent is not None else None,
                    order=order,
                )
                for fragment, parent, order in nesting
            )
            session.flush()
