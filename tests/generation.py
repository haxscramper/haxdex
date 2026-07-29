from __future__ import annotations

import datetime
import decimal
import json
import math
import re
import shutil
from enum import Enum
from types import NoneType, UnionType

from beartype import beartype
from beartype.typing import Any, Literal, Sequence, Union, get_args, get_origin, Iterable
from hypothesis import assume, strategies as st
from hypothesis.strategies import SearchStrategy

from haxdex.services.core.job_types import BaseIndexer, META_SUFFIX
from haxdex.services.pydantic_utils import model_to_json_data

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from tests.generation_full_file import CorpusManifest, initialize_persistent_corpus, _MIME_SUFFIXES, CorpusFileEntry


@dataclass(frozen=True)
class GeneratedIndexerFile:
    relative_path: Path
    results: dict[str, object]
    corpus_source_path: Path | None = None


@dataclass(frozen=True)
class GeneratedIndexerDirectory:
    relative_path: Path


GeneratedIndexerEntry = GeneratedIndexerFile | GeneratedIndexerDirectory


def _sorted_rel(paths: Iterable[Path]) -> list[Path]:
    return sorted(set(paths), key=lambda p: (len(p.parts), str(p)))


@dataclass
class GeneratedDirectory:
    files: list[GeneratedIndexerFile]

    def _as_relative_path(self, path: Path | str = Path(".")) -> Path:
        rel_path = Path(path)
        return rel_path if rel_path != Path("") else Path(".")

    def _sorted_paths(self, paths: Iterable[Path]) -> list[Path]:
        return sorted(set(paths), key=lambda path: (len(path.parts), str(path)))

    def _all_directory_paths(self) -> set[Path]:
        result: set[Path] = set()
        for file in self.files:
            current = file.relative_path.parent
            while current != Path("."):
                result.add(current)
                current = current.parent
        return result

    def collect_directories_direct(
            self,
            query: Path = Path("."),
    ) -> list[GeneratedIndexerDirectory]:
        query = self._as_relative_path(query)
        direct_dirs: set[Path] = set()
        for file in self.collect_files_recursive(query):
            rel = file.relative_path if query == Path(
                ".") else file.relative_path.relative_to(query)
            if len(rel.parts) <= 1:
                continue
            direct = Path(rel.parts[0]) if query == Path(".") else query / rel.parts[0]
            direct_dirs.add(direct)
        return [
            GeneratedIndexerDirectory(relative_path=path)
            for path in _sorted_rel(direct_dirs)
        ]

    def collect_directories_recursive(
            self,
            query: Path = Path("."),
    ) -> list[GeneratedIndexerDirectory]:
        query = self._as_relative_path(query)
        dirs: set[Path] = set()
        for file in self.collect_files_recursive(query):
            current = file.relative_path.parent
            while current != query and current != Path("."):
                dirs.add(current)
                current = current.parent
        return [
            GeneratedIndexerDirectory(relative_path=path) for path in _sorted_rel(dirs)
        ]

    def collect_files_direct(
            self,
            query: Path | str = Path("."),
    ) -> list[GeneratedIndexerFile]:
        query_path = self._as_relative_path(query)
        return [file for file in self.files if file.relative_path.parent == query_path]

    def collect_files_recursive(
            self,
            query: Path | str = Path("."),
    ) -> list[GeneratedIndexerFile]:
        query_path = self._as_relative_path(query)
        if query_path == Path("."):
            return list(self.files)
        return [
            file for file in self.files if file.relative_path.is_relative_to(query_path)
        ]

    def collect_entries_direct(
            self,
            query: Path = Path("."),
    ) -> list[GeneratedIndexerEntry]:
        return [
            *self.collect_files_direct(query),
            *self.collect_directories_direct(query),
        ]

    def collect_entries_recursive(
            self,
            query: Path = Path("."),
    ) -> list[GeneratedIndexerEntry]:
        return [
            *self.collect_files_recursive(query),
            *self.collect_directories_recursive(query),
        ]

    def get_file_by_relative_name(self, name: Path | str) -> GeneratedIndexerFile:
        rel_name = self._as_relative_path(name)
        for file in self.files:
            if file.relative_path == rel_name:
                return file
        raise KeyError(f"File entry is missing for relative path '{rel_name}'")

    def get_directory_by_relative_name(self,
                                       name: Path | str) -> GeneratedIndexerDirectory:
        rel_name = self._as_relative_path(name)
        if rel_name == Path("."):
            raise KeyError("Root path '.' is not a generated directory entry")
        dirs = self._all_directory_paths()
        if rel_name in dirs:
            return GeneratedIndexerDirectory(relative_path=rel_name)
        raise KeyError(f"Directory entry is missing for relative path '{rel_name}'")

    def get_entry_by_relative_name(self, name: Path | str) -> GeneratedIndexerEntry:
        rel_name = self._as_relative_path(name)
        for file in self.files:
            if file.relative_path == rel_name:
                return file
        if rel_name in self._all_directory_paths():
            return GeneratedIndexerDirectory(relative_path=rel_name)
        raise KeyError(
            f"File or directory entry is missing for relative path '{rel_name}'")


@dataclass(frozen=True)
class MaterializedDirectory:
    root: Path
    files: list[Path]


@beartype
def _constraint_value(metadata: Sequence[Any], name: str) -> Any | None:
    for item in metadata:
        value = getattr(item, name, None)
        if value is not None:
            return value

    return None


@beartype
def _integer_strategy(metadata: Sequence[Any]) -> SearchStrategy[int]:
    lower: int | None = _constraint_value(metadata, "ge")
    upper: int | None = _constraint_value(metadata, "le")
    exclusive_lower: int | None = _constraint_value(metadata, "gt")
    exclusive_upper: int | None = _constraint_value(metadata, "lt")
    multiple_of: int | None = _constraint_value(metadata, "multiple_of")

    if exclusive_lower is not None:
        candidate = exclusive_lower + 1
        if lower is None or lower < candidate:
            lower = candidate

    if exclusive_upper is not None:
        candidate = exclusive_upper - 1
        if upper is None or candidate < upper:
            upper = candidate

    if lower is None:
        lower = -10_000

    if upper is None:
        upper = 10_000

    if upper < lower:
        raise ValueError(
            f"Integer constraints cannot be satisfied: lower bound is {lower}, "
            f"upper bound is {upper}")

    if multiple_of is None:
        return st.integers(min_value=lower, max_value=upper)

    if multiple_of == 0:
        raise ValueError("Integer multiple_of constraint cannot be zero")

    magnitude = abs(multiple_of)
    first = math.ceil(lower / magnitude)
    last = math.floor(upper / magnitude)

    if last < first:
        raise ValueError(
            f"Integer constraints cannot produce a multiple of {multiple_of} "
            f"between {lower} and {upper}")

    return st.integers(min_value=first,
                       max_value=last).map(lambda value: value * magnitude)


@beartype
def _float_strategy(metadata: Sequence[Any]) -> SearchStrategy[float]:
    lower: float | None = _constraint_value(metadata, "ge")
    upper: float | None = _constraint_value(metadata, "le")
    exclusive_lower: float | None = _constraint_value(metadata, "gt")
    exclusive_upper: float | None = _constraint_value(metadata, "lt")

    if exclusive_lower is not None:
        candidate = math.nextafter(exclusive_lower, math.inf)
        if lower is None or lower < candidate:
            lower = candidate

    if exclusive_upper is not None:
        candidate = math.nextafter(exclusive_upper, -math.inf)
        if upper is None or candidate < upper:
            upper = candidate

    if lower is None:
        lower = -10_000.0

    if upper is None:
        upper = 10_000.0

    if upper < lower:
        raise ValueError(
            f"Float constraints cannot be satisfied: lower bound is {lower}, "
            f"upper bound is {upper}")

    multiple_of: float | None = _constraint_value(metadata, "multiple_of")
    strategy = st.floats(
        min_value=lower,
        max_value=upper,
        allow_nan=False,
        allow_infinity=False,
    )

    if multiple_of is not None:
        strategy = strategy.filter(lambda value: math.isclose(
            value % multiple_of,
            0.0,
            abs_tol=1e-9,
        ))

    return strategy


@beartype
def _decimal_strategy(metadata: Sequence[Any]) -> SearchStrategy[decimal.Decimal]:
    lower: decimal.Decimal | None = _constraint_value(metadata, "ge")
    upper: decimal.Decimal | None = _constraint_value(metadata, "le")
    exclusive_lower: decimal.Decimal | None = _constraint_value(metadata, "gt")
    exclusive_upper: decimal.Decimal | None = _constraint_value(metadata, "lt")

    if exclusive_lower is not None:
        candidate = exclusive_lower.next_plus()
        if lower is None or lower < candidate:
            lower = candidate

    if exclusive_upper is not None:
        candidate = exclusive_upper.next_minus()
        if upper is None or candidate < upper:
            upper = candidate

    if lower is None:
        lower = decimal.Decimal("-10000")

    if upper is None:
        upper = decimal.Decimal("10000")

    if upper < lower:
        raise ValueError(
            f"Decimal constraints cannot be satisfied: lower bound is {lower}, "
            f"upper bound is {upper}")

    decimal_places: int | None = _constraint_value(metadata, "decimal_places")
    strategy = st.decimals(
        min_value=lower,
        max_value=upper,
        allow_nan=False,
        allow_infinity=False,
        places=decimal_places,
    )

    multiple_of: decimal.Decimal | None = _constraint_value(metadata, "multiple_of")
    if multiple_of is not None:
        strategy = strategy.filter(lambda value: value % multiple_of == 0)

    return strategy


@beartype
def _string_strategy(metadata: Sequence[Any]) -> SearchStrategy[str]:
    min_length: int = _constraint_value(metadata, "min_length") or 0
    max_length: int | None = _constraint_value(metadata, "max_length")
    pattern: str | re.Pattern[str] | None = _constraint_value(metadata, "pattern")

    if max_length is None:
        max_length = 64

    if max_length < min_length:
        raise ValueError(
            f"String constraints cannot be satisfied: min_length is {min_length}, "
            f"max_length is {max_length}")

    if pattern is None:
        return st.text(min_size=min_length, max_size=max_length)

    else:
        return st.from_regex(pattern, fullmatch=True).filter(
            lambda value: min_length <= len(value) and len(value) <= max_length)


@beartype
def _collection_bounds(metadata: Sequence[Any]) -> tuple[int, int]:
    min_length: int = _constraint_value(metadata, "min_length") or 0
    max_length: int | None = _constraint_value(metadata, "max_length")

    if max_length is None:
        max_length = 8

    if max_length < min_length:
        raise ValueError(f"Collection constraints cannot be satisfied: min_length is "
                         f"{min_length}, max_length is {max_length}")

    return min_length, max_length


@beartype
def _model_field_name(name: str, field: Any) -> str:
    alias = field.validation_alias

    if isinstance(alias, str):
        return alias

    if isinstance(field.alias, str):
        return field.alias

    return name


@beartype
def pydantic_model_strategy(model_type: type[BaseModel]) -> SearchStrategy[BaseModel]:
    fields: dict[str, SearchStrategy[Any]] = {}

    for name, field in model_type.model_fields.items():
        field_name = _model_field_name(name, field)
        fields[field_name] = pydantic_value_strategy(
            field.annotation,
            field.metadata,
        )

    return st.builds(model_type, **fields)


@beartype
def pydantic_value_strategy(
        annotation: Any,
        metadata: Sequence[Any] = (),
) -> SearchStrategy[Any]:
    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is Union or origin is UnionType:
        return st.one_of(
            *[pydantic_value_strategy(argument, metadata) for argument in arguments])

    if origin is Literal:
        return st.sampled_from(arguments)

    if annotation is NoneType:
        return st.none()

    if annotation is bool:
        return st.booleans()

    if annotation is int:
        return _integer_strategy(metadata)

    if annotation is float:
        return _float_strategy(metadata)

    if annotation is decimal.Decimal:
        return _decimal_strategy(metadata)

    if annotation is str:
        return _string_strategy(metadata)

    if annotation is bytes:
        min_length, max_length = _collection_bounds(metadata)
        return st.binary(min_size=min_length, max_size=max_length)

    if annotation is Path:
        return _string_strategy(metadata).map(Path)

    if annotation is datetime.datetime:
        return st.datetimes(timezones=st.none())

    if annotation is datetime.date:
        return st.dates()

    if annotation is datetime.time:
        return st.times()

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return st.sampled_from(list(annotation))

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return pydantic_model_strategy(annotation)

    min_length, max_length = _collection_bounds(metadata)

    match origin:
        case list():
            return st.lists(
                pydantic_value_strategy(arguments[0]),
                min_size=min_length,
                max_size=max_length,
            )
        case set():
            return st.sets(
                pydantic_value_strategy(arguments[0]),
                min_size=min_length,
                max_size=max_length,
            )
        case frozenset():
            return st.frozensets(
                pydantic_value_strategy(arguments[0]),
                min_size=min_length,
                max_size=max_length,
            )
        case tuple():
            if len(arguments) == 2 and arguments[1] is Ellipsis:
                return st.lists(
                    pydantic_value_strategy(arguments[0]),
                    min_size=min_length,
                    max_size=max_length,
                ).map(tuple)

            return st.tuples(
                *[pydantic_value_strategy(argument) for argument in arguments])
        case dict():
            return st.dictionaries(
                pydantic_value_strategy(arguments[0]),
                pydantic_value_strategy(arguments[1]),
                min_size=min_length,
                max_size=max_length,
            )

    return st.from_type(annotation)


@beartype
def _normalize_mime_requirements(
    mime_types: Sequence[str | tuple[str, int]],) -> list[tuple[str, int]]:
    requirements: list[tuple[str, int]] = []
    seen: set[str] = set()

    for item in mime_types:
        if isinstance(item, str):
            mime_type = item
            min_count = 1
        else:
            if len(item) != 2:
                raise ValueError(
                    f"MIME requirement tuple must have exactly 2 items, got {item}")
            mime_type, min_count = item
            if not isinstance(mime_type, str):
                raise ValueError(f"MIME type must be a string, got {mime_type!r}")
            if not isinstance(min_count, int) or isinstance(min_count, bool):
                raise ValueError(
                    f"MIME minimum count must be an integer, got {min_count!r}")
            if min_count < 0:
                raise ValueError(
                    f"MIME minimum count must be non-negative, got {min_count} for {mime_type!r}"
                )

        if mime_type in seen:
            raise ValueError(f"Duplicate MIME type requirement for {mime_type!r}")
        seen.add(mime_type)
        requirements.append((mime_type, min_count))

    return requirements


@beartype
def _validate_directory_structure_inputs(
    min_files: int,
    max_files: int,
    min_nesting: int,
    max_nesting: int,
    min_duplicates: int,
    max_duplicates: int,
) -> None:
    if max_files < min_files:
        raise ValueError(
            f"max_files must be at least min_files, got min_files={min_files} and max_files={max_files}"
        )

    if max_nesting < min_nesting:
        raise ValueError(
            f"max_nesting must be at least min_nesting, got min_nesting={min_nesting} and max_nesting={max_nesting}"
        )

    if min_duplicates < 0:
        raise ValueError(
            f"min_duplicates must be non-negative, got min_duplicates={min_duplicates}")

    if max_duplicates < min_duplicates:
        raise ValueError(
            f"max_duplicates must be at least min_duplicates, got min_duplicates={min_duplicates} and max_duplicates={max_duplicates}"
        )

    if max_files < (min_duplicates + 1):
        raise ValueError(
            f"Cannot satisfy min_duplicates={min_duplicates} with max_files={max_files}. Need at least min_duplicates + 1 files."
        )

    if max_duplicates > (max_files - 1):
        raise ValueError(
            f"max_duplicates must be at most max_files - 1, got max_duplicates={max_duplicates} and max_files={max_files}"
        )


@beartype
def _validate_unique_indexer_asset_names(
    indexer_types: Sequence[type[BaseIndexer]],) -> None:
    asset_names = [indexer_type.asset_name for indexer_type in indexer_types]
    duplicate_names = {name for name in asset_names if 1 < asset_names.count(name)}
    if 0 < len(duplicate_names):
        raise ValueError(
            f"Indexer asset names must be unique, duplicate names are {sorted(duplicate_names)}"
        )


@beartype
def _build_entries_by_mime(
    corpus_manifest: CorpusManifest,
    mime_requirements: Sequence[tuple[str, int]],
) -> dict[str, list[CorpusFileEntry]]:
    entries_by_mime: dict[str, list[CorpusFileEntry]] = {
        mime: [] for mime, _ in mime_requirements
    }
    for entry in corpus_manifest.entries:
        mime_type = entry.file_spec.mime_type
        if mime_type in entries_by_mime:
            entries_by_mime[mime_type].append(entry)
    return entries_by_mime


@beartype
def _validate_mime_requirements(
    mime_requirements: Sequence[tuple[str, int]],
    entries_by_mime: dict[str, list[CorpusFileEntry]],
    max_files: int,
) -> tuple[int, int]:
    unsupported_mime_types = [
        mime_type for mime_type, _ in mime_requirements if mime_type not in _MIME_SUFFIXES
    ]
    if 0 < len(unsupported_mime_types):
        raise ValueError(
            f"Unsupported MIME types: {unsupported_mime_types}. Supported MIME types are {sorted(_MIME_SUFFIXES)}"
        )

    missing_required = [
        mime_type for mime_type, min_count in mime_requirements
        if 0 < min_count and len(entries_by_mime[mime_type]) == 0
    ]
    if 0 < len(missing_required):
        raise ValueError(
            f"Corpus does not contain files for required MIME types {missing_required}")

    required_total = sum(min_count for _, min_count in mime_requirements)
    if max_files < required_total:
        raise ValueError(
            f"Cannot satisfy per-MIME minimum counts: required total is {required_total}, but max_files is {max_files}"
        )

    positive_mime_count = sum(1 for _, min_count in mime_requirements if 0 < min_count)
    return required_total, positive_mime_count


@beartype
def _eligible_entries(
    mime_requirements: Sequence[tuple[str, int]],
    entries_by_mime: dict[str, list[CorpusFileEntry]],
) -> list[CorpusFileEntry]:
    eligible = [
        entry for mime_type, _ in mime_requirements
        for entry in entries_by_mime[mime_type]
    ]
    if len(eligible) == 0:
        raise ValueError("No corpus entries available for selected MIME types")
    return eligible


@beartype
def _build_indexer_strategies(
    indexer_types: Sequence[type[BaseIndexer]],) -> dict[str, SearchStrategy[Any]]:
    return {
        indexer_type.asset_name: pydantic_model_strategy(indexer_type.result_model)
        for indexer_type in indexer_types
    }


@beartype
def _draw_parent_path(
    draw: Any,
    min_nesting: int,
    max_nesting: int,
    different_from: Path | None = None,
) -> Path:
    depth = draw(st.integers(min_value=min_nesting, max_value=max_nesting))
    parts = draw(
        st.lists(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
                min_size=8,
                max_size=8,
            ),
            min_size=depth,
            max_size=depth,
        ))
    parent = Path(*parts)
    if different_from is not None:
        assume(parent != different_from)
    return parent


@beartype
def _draw_stem(draw: Any, different_from: str | None = None) -> str:
    stem = draw(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
            min_size=8,
            max_size=8,
        ))
    if different_from is not None:
        assume(stem != different_from)
    return stem


@beartype
def _minimal_required_duplicates(
    file_count: int,
    unique_count: int,
    required_total: int,
    positive_mime_count: int,
    additional_cover_capacity: int,
) -> int:
    if unique_count < positive_mime_count:
        return file_count + 1

    base_cover = positive_mime_count
    extra_slots = unique_count - positive_mime_count
    covered = base_cover + min(extra_slots, additional_cover_capacity)
    return required_total - covered


@beartype
def _draw_file_and_duplicate_counts(
    draw: Any,
    min_files: int,
    max_files: int,
    min_duplicates: int,
    max_duplicates: int,
    required_total: int,
    positive_mime_count: int,
    eligible_entries_count: int,
    mime_requirements: Sequence[tuple[str, int]],
    entries_by_mime: dict[str, list[CorpusFileEntry]],
) -> tuple[int, int]:
    min_file_count = max(min_files, required_total, min_duplicates + 1)
    file_count = draw(st.integers(min_value=min_file_count, max_value=max_files))

    additional_cover_capacity = 0
    for mime_type, min_count in mime_requirements:
        if min_count == 0:
            continue
        cover_cap = min(min_count, len(entries_by_mime[mime_type]))
        additional_cover_capacity += max(0, cover_cap - 1)

    unique_lower = max(1, positive_mime_count, file_count - max_duplicates)
    unique_upper = min(eligible_entries_count, file_count - min_duplicates)

    feasible_unique_counts = [
        unique_count for unique_count in range(unique_lower, unique_upper + 1)
        if _minimal_required_duplicates(
            file_count=file_count,
            unique_count=unique_count,
            required_total=required_total,
            positive_mime_count=positive_mime_count,
            additional_cover_capacity=additional_cover_capacity,
        ) <= (file_count - unique_count)
    ]
    assume(0 < len(feasible_unique_counts))

    unique_count = draw(st.sampled_from(feasible_unique_counts))
    duplicate_count = file_count - unique_count
    return unique_count, duplicate_count


@beartype
def _allocate_unique_targets(
    unique_count: int,
    mime_requirements: Sequence[tuple[str, int]],
    entries_by_mime: dict[str, list[CorpusFileEntry]],
) -> dict[str, int]:
    unique_targets: dict[str, int] = {mime_type: 0 for mime_type, _ in mime_requirements}

    positive_mime_count = sum(1 for _, min_count in mime_requirements if 0 < min_count)
    for mime_type, min_count in mime_requirements:
        if 0 < min_count:
            unique_targets[mime_type] = 1

    remaining_unique = unique_count - positive_mime_count

    for mime_type, min_count in mime_requirements:
        cap = len(entries_by_mime[mime_type])
        target = min(min_count, cap)
        while 0 < remaining_unique and unique_targets[mime_type] < target:
            unique_targets[mime_type] += 1
            remaining_unique -= 1

    if 0 < remaining_unique:
        for mime_type, _ in mime_requirements:
            cap = len(entries_by_mime[mime_type])
            while 0 < remaining_unique and unique_targets[mime_type] < cap:
                unique_targets[mime_type] += 1
                remaining_unique -= 1
            if remaining_unique == 0:
                break

    assume(remaining_unique == 0)
    return unique_targets


@st.composite
@beartype
def directory_structure(
    draw: Any,
    indexer_types: Sequence[type[BaseIndexer]],
    corpus_manifest: CorpusManifest,
    corpus_root: Path,
    min_files: int = 1,
    max_files: int = 16,
    min_nesting: int = 0,
    max_nesting: int = 3,
    mime_types: Sequence[str | tuple[str, int]] = tuple(_MIME_SUFFIXES),
    min_duplicates: int = 0,
    max_duplicates: int = 0,
) -> GeneratedDirectory:
    _validate_directory_structure_inputs(
        min_files=min_files,
        max_files=max_files,
        min_nesting=min_nesting,
        max_nesting=max_nesting,
        min_duplicates=min_duplicates,
        max_duplicates=max_duplicates,
    )
    _validate_unique_indexer_asset_names(indexer_types)

    mime_requirements = _normalize_mime_requirements(mime_types)
    entries_by_mime = _build_entries_by_mime(corpus_manifest, mime_requirements)
    required_total, positive_mime_count = _validate_mime_requirements(
        mime_requirements=mime_requirements,
        entries_by_mime=entries_by_mime,
        max_files=max_files,
    )
    eligible_entries = _eligible_entries(mime_requirements, entries_by_mime)
    indexer_strategies = _build_indexer_strategies(indexer_types)

    unique_count, duplicate_count = _draw_file_and_duplicate_counts(
        draw=draw,
        min_files=min_files,
        max_files=max_files,
        min_duplicates=min_duplicates,
        max_duplicates=max_duplicates,
        required_total=required_total,
        positive_mime_count=positive_mime_count,
        eligible_entries_count=len(eligible_entries),
        mime_requirements=mime_requirements,
        entries_by_mime=entries_by_mime,
    )

    unique_targets = _allocate_unique_targets(
        unique_count=unique_count,
        mime_requirements=mime_requirements,
        entries_by_mime=entries_by_mime,
    )

    relative_paths: set[Path] = set()
    generated_files: list[GeneratedIndexerFile] = []
    generated_by_mime: dict[str, list[GeneratedIndexerFile]] = {
        mime_type: [] for mime_type, _ in mime_requirements
    }

    for mime_type, _ in mime_requirements:
        take_count = unique_targets[mime_type]
        if take_count == 0:
            continue

        mime_entries = entries_by_mime[mime_type]
        selected_indices = draw(
            st.lists(
                st.integers(min_value=0, max_value=len(mime_entries) - 1),
                min_size=take_count,
                max_size=take_count,
                unique=True,
            ))

        for entry_index in selected_indices:
            selected_entry = mime_entries[entry_index]
            parent = _draw_parent_path(draw, min_nesting, max_nesting)
            stem = _draw_stem(draw)
            relative_path = parent / f"{stem}{_MIME_SUFFIXES[mime_type]}"
            assume(relative_path not in relative_paths)
            relative_paths.add(relative_path)

            results = {
                asset_name: draw(strategy)
                for asset_name, strategy in indexer_strategies.items()
            }

            generated = GeneratedIndexerFile(
                relative_path=relative_path,
                results=results,
                corpus_source_path=corpus_root / selected_entry.relative_path,
            )
            generated_files.append(generated)
            generated_by_mime[mime_type].append(generated)

    deficits: dict[str, int] = {
        mime_type: max(0, min_count - len(generated_by_mime[mime_type]))
        for mime_type, min_count in mime_requirements
    }

    for _ in range(duplicate_count):
        deficit_mimes = [
            mime_type for mime_type, deficit in deficits.items()
            if 0 < deficit and 0 < len(generated_by_mime[mime_type])
        ]
        if 0 < len(deficit_mimes):
            selected_mime = draw(st.sampled_from(deficit_mimes))
        else:
            available_mimes = [
                mime_type for mime_type, files in generated_by_mime.items()
                if 0 < len(files)
            ]
            selected_mime = draw(st.sampled_from(available_mimes))

        anchor = draw(st.sampled_from(generated_by_mime[selected_mime]))
        anchor_parent = anchor.relative_path.parent
        anchor_stem = anchor.relative_path.stem
        anchor_suffix = anchor.relative_path.suffix

        modes = ["different_name_same_dir"]
        if 0 < max_nesting:
            modes.extend(["same_name_different_dir", "different_name_different_dir"])
        mode = draw(st.sampled_from(tuple(modes)))

        if mode == "different_name_same_dir":
            parent = anchor_parent
            stem = _draw_stem(draw, different_from=anchor_stem)
        elif mode == "same_name_different_dir":
            parent = _draw_parent_path(
                draw,
                min_nesting,
                max_nesting,
                different_from=anchor_parent,
            )
            stem = anchor_stem
        else:
            parent = _draw_parent_path(
                draw,
                min_nesting,
                max_nesting,
                different_from=anchor_parent,
            )
            stem = _draw_stem(draw, different_from=anchor_stem)

        relative_path = parent / f"{stem}{anchor_suffix}"
        assume(relative_path not in relative_paths)
        relative_paths.add(relative_path)

        results = {
            asset_name: draw(strategy)
            for asset_name, strategy in indexer_strategies.items()
        }

        duplicated = GeneratedIndexerFile(
            relative_path=relative_path,
            results=results,
            corpus_source_path=anchor.corpus_source_path,
        )
        generated_files.append(duplicated)
        generated_by_mime[selected_mime].append(duplicated)

        if 0 < deficits[selected_mime]:
            deficits[selected_mime] -= 1

    assume(all(deficit == 0 for deficit in deficits.values()))
    return GeneratedDirectory(files=generated_files)


@beartype
def write_generated_directory(
    root: Path,
    directory: GeneratedDirectory,
) -> MaterializedDirectory:
    if root.exists():
        shutil.rmtree(root)

    root.mkdir(parents=True, exist_ok=True)
    materialized_files: list[Path] = []

    for generated_file in directory.files:
        if generated_file.corpus_source_path is None:
            raise ValueError(
                f"Generated file '{generated_file.relative_path}' does not define corpus_source_path"
            )
        if not generated_file.corpus_source_path.exists():
            raise ValueError(
                f"Corpus source path '{generated_file.corpus_source_path}' for '{generated_file.relative_path}' does not exist"
            )

        file_path = root / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated_file.corpus_source_path, file_path)

        metadata_path = file_path.with_name(f"{file_path.name}{META_SUFFIX}")
        metadata = {
            "indexers": {
                asset_name: model_to_json_data(result)
                for asset_name, result in generated_file.results.items()
            }
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        materialized_files.append(file_path)

    return MaterializedDirectory(root=root, files=materialized_files)


@beartype
def create_default_persistent_corpus(corpus_root: Path) -> CorpusManifest:
    seeds = list(range(0, 240))
    return initialize_persistent_corpus(
        corpus_root=corpus_root,
        seeds=seeds,
        mime_types=tuple(_MIME_SUFFIXES),
    )


@beartype
def assert_generated_directory_entries_exact(
    root: Path,
    generated: GeneratedDirectory,
) -> None:
    assert root.exists() and root.is_dir(), f"Expected directory at {root}"

    expected_files: set[Path] = set()
    for gfile in generated.files:
        rel = gfile.relative_path
        expected_files.add(rel)
        expected_files.add(rel.with_name(f"{rel.name}{META_SUFFIX}"))

    expected_dirs: set[Path] = set()
    for rel_file in expected_files:
        parent = rel_file.parent
        while parent != Path("."):
            expected_dirs.add(parent)
            parent = parent.parent

    actual_files: set[Path] = set()
    actual_dirs: set[Path] = set()

    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file():
            actual_files.add(rel)
        elif path.is_dir():
            actual_dirs.add(rel)
        else:
            raise AssertionError(f"Unexpected non-file/non-dir entry: {path}")

    missing_files = expected_files - actual_files
    extra_files = actual_files - expected_files
    missing_dirs = expected_dirs - actual_dirs
    extra_dirs = actual_dirs - expected_dirs

    assert not missing_files and not extra_files and not missing_dirs and not extra_dirs, (
        "Directory entries mismatch:\n"
        f"  missing files: {sorted(map(str, missing_files))}\n"
        f"  extra files:   {sorted(map(str, extra_files))}\n"
        f"  missing dirs:  {sorted(map(str, missing_dirs))}\n"
        f"  extra dirs:    {sorted(map(str, extra_dirs))}")
