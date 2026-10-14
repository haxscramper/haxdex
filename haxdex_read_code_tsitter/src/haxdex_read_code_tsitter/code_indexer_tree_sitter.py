#!/usr/bin/env python

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from tree_sitter import Parser

from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.code_model import (
    Base,
    FunctionArgument,
    IndexEntry,
    ParsedEntry,
)
from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.extract_cpp import (
    CPP_LANGUAGE,
    CPP_SUFFIXES,
    index_cpp_file,
)
from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.extract_python import (
    PYTHON_LANGUAGE,
    PYTHON_SUFFIXES,
    index_python_file,
)
from haxdex_read_code_tsitter.src.haxdex_read_code_tsitter.utils import make_parser


def index_file(
    path: Path, rel_path: str, cpp_parser: Parser, python_parser: Parser
) -> ParsedEntry:
    suffix = path.suffix.lower()
    if suffix in CPP_SUFFIXES:
        return index_cpp_file(path, rel_path, cpp_parser)
    else:
        return index_python_file(path, rel_path, python_parser)


def gather_git_files(root: Path) -> list[Path]:
    output = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
        text=True,
    )
    result: list[Path] = []
    for line in output.splitlines():
        rel = line.strip()
        if not rel:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in CPP_SUFFIXES or suffix in PYTHON_SUFFIXES:
            result.append(Path(rel))
    result.sort()
    return result


def persist_entry(session: Session, parent_id: int | None, entry: ParsedEntry) -> int:
    row = IndexEntry(
        parent_id=parent_id,
        kind=entry.kind,
        language=entry.language,
        name=entry.name,
        path=entry.path,
        qualified_name=entry.qualified_name,
        start_line=entry.start_line,
        end_line=entry.end_line,
        type_text=entry.type_text,
        signature_text=entry.signature_text,
        signature_source=entry.signature_source,
        doc_brief=entry.doc.brief if entry.doc else None,
        doc_full=entry.doc.full if entry.doc else None,
    )
    session.add(row)
    session.flush()

    for idx, arg in enumerate(entry.arguments):
        session.add(
            FunctionArgument(
                entry_id=row.id,
                position=idx,
                name=arg.name,
                type_text=arg.type_text,
                default_value=arg.default_value,
                kind=arg.kind,
            )
        )

    for child in entry.children:
        persist_entry(session, row.id, child)

    return row.id


def create_flat_view(session: Session) -> None:
    session.execute(text("DROP VIEW IF EXISTS entry_flat_view"))
    session.execute(
        text("""
            CREATE VIEW entry_flat_view AS
            SELECT
                e.id AS entry_id,
                e.kind AS kind,
                e.language AS language,
                e.path AS path,
                e.qualified_name AS qualified_name,
                CASE
                    WHEN e.kind IN ('function', 'method', 'signal') THEN
                        COALESCE(e.qualified_name, e.name, '') || COALESCE(e.signature_text, '')
                    WHEN e.kind = 'field' THEN
                        COALESCE(e.qualified_name, e.name, '')
                    WHEN e.kind = 'class' THEN
                        COALESCE(e.qualified_name, e.name, '')
                    WHEN e.kind = 'namespace' THEN
                        COALESCE(e.qualified_name, e.name, '')
                    WHEN e.kind = 'file' THEN
                        COALESCE(e.path, e.name, '')
                    WHEN e.kind = 'directory' THEN
                        COALESCE(e.path, e.name, '')
                    ELSE
                        COALESCE(e.qualified_name, e.path, e.name, '')
                END AS flat_representation,
                e.type_text AS type_text,
                e.signature_text AS signature_text,
                e.signature_source AS signature_source,
                e.doc_brief AS doc_brief,
                e.doc_full AS doc_full,
                e.start_line AS start_line
            FROM entries e
            """)
    )


def main() -> None:
    argp = argparse.ArgumentParser()
    argp.add_argument("root", type=Path)
    argp.add_argument("--output", type=Path, required=False)
    args = argp.parse_args()

    root = args.root.resolve()
    output = (
        args.output.resolve()
        if args.output is not None
        else root / ".haxscramper-code-index.sqlite"
    )
    output.unlink(missing_ok=True)

    files = gather_git_files(root)
    total = len(files)

    cpp_parser = make_parser(CPP_LANGUAGE)
    python_parser = make_parser(PYTHON_LANGUAGE)

    engine = create_engine(f"sqlite+pysqlite:///{output}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        root_dir = ParsedEntry(
            kind="directory",
            name=root.name,
            path=".",
            qualified_name=".",
            start_line=None,
            end_line=None,
        )
        root_id = persist_entry(session, None, root_dir)

        dir_ids: dict[Path, int] = {Path("."): root_id}
        dirs_to_create: set[Path] = set()

        for rel in files:
            parent = rel.parent
            while str(parent) != ".":
                dirs_to_create.add(parent)
                parent = parent.parent

        for d in sorted(dirs_to_create, key=lambda p: (len(p.parts), str(p))):
            parent = d.parent if str(d.parent) != "" else Path(".")
            parent_id = dir_ids[parent]
            dir_entry = ParsedEntry(
                kind="directory",
                name=d.name,
                path=str(d),
                qualified_name=str(d),
                start_line=None,
                end_line=None,
            )
            dir_ids[d] = persist_entry(session, parent_id, dir_entry)

        start = time.monotonic()
        processed = 0

        for rel in files:
            abs_path = root / rel
            parsed_file = index_file(abs_path, str(rel), cpp_parser, python_parser)
            parent_id = dir_ids.get(rel.parent, root_id)
            persist_entry(session, parent_id, parsed_file)

            processed += 1
            remaining = total - processed
            elapsed = time.monotonic() - start
            avg = elapsed / processed
            eta = avg * remaining
            # logger.info("[{}/{}] ETA {} {}".format(processed, total,
            #                                        format_eta(eta), rel))

        create_flat_view(session)
        session.commit()


if __name__ == "__main__":
    main()
