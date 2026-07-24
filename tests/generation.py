from __future__ import annotations

import datetime
import decimal
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import NoneType, UnionType

from beartype import beartype
from beartype.typing import Any, ClassVar, Literal, Sequence, Union, get_args, get_origin
from hypothesis import assume, strategies as st
from hypothesis.strategies import SearchStrategy
from pydantic import BaseModel

from haxdex.services.core.job_types import BaseIndexer
from haxdex.services.pydantic_utils import model_to_json_data


@dataclass(frozen=True)
class GeneratedIndexerFile:
    relative_path: Path
    results: dict[str, BaseModel]


@dataclass(frozen=True)
class GeneratedDirectory:
    files: list[GeneratedIndexerFile]


@dataclass(frozen=True)
class MaterializedDirectory:
    root: Path
    files: list[Path]


_MIME_SUFFIXES: dict[str, str] = {
    "image/png": ".png",
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "text/plain": ".txt",
    "text/org": ".org",
    "text/markdown": ".md",
}


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


@st.composite
@beartype
def directory_structure(
        draw: Any,
        indexer_types: Sequence[type[BaseIndexer]],
        min_files: int = 1,
        max_files: int = 16,
        min_nesting: int = 0,
        max_nesting: int = 3,
        mime_types: Sequence[str] = tuple(_MIME_SUFFIXES),
) -> GeneratedDirectory:
    if max_files < min_files:
        raise ValueError(
            f"max_files must be at least min_files, got min_files={min_files} "
            f"and max_files={max_files}")

    if max_nesting < min_nesting:
        raise ValueError(f"max_nesting must be at least min_nesting, got "
                         f"min_nesting={min_nesting} and max_nesting={max_nesting}")

    asset_names = [indexer_type.asset_name for indexer_type in indexer_types]
    duplicate_names = {name for name in asset_names if 1 < asset_names.count(name)}

    if duplicate_names:
        raise ValueError(f"Indexer asset names must be unique, duplicate names are "
                         f"{sorted(duplicate_names)}")

    unsupported_mime_types = [
        mime_type for mime_type in mime_types if mime_type not in _MIME_SUFFIXES
    ]

    if unsupported_mime_types:
        raise ValueError(
            f"Unsupported MIME types: {unsupported_mime_types}. Supported MIME "
            f"types are {sorted(_MIME_SUFFIXES)}")

    indexer_strategies = {
        indexer_type.asset_name: pydantic_model_strategy(indexer_type.result_model)
        for indexer_type in indexer_types
    }
    file_count = draw(st.integers(min_value=min_files, max_value=max_files))
    generated_files: list[GeneratedIndexerFile] = []
    relative_paths: set[Path] = set()

    for _ in range(file_count):
        depth = draw(st.integers(min_value=min_nesting, max_value=max_nesting))
        nested_parts = draw(
            st.lists(
                st.text(
                    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
                    min_size=1,
                    max_size=12,
                ),
                min_size=depth,
                max_size=depth,
            ))
        stem = draw(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
                min_size=1,
                max_size=20,
            ))
        mime_type = draw(st.sampled_from(tuple(mime_types)))
        relative_path = Path(*nested_parts, f"{stem}{_MIME_SUFFIXES[mime_type]}")
        assume(relative_path not in relative_paths)
        relative_paths.add(relative_path)

        results = {
            asset_name: draw(strategy)
            for asset_name, strategy in indexer_strategies.items()
        }
        generated_files.append(
            GeneratedIndexerFile(
                relative_path=relative_path,
                results=results,
            ))

    return GeneratedDirectory(files=generated_files)


@beartype
def write_generated_directory(
    root: Path,
    directory: GeneratedDirectory,
) -> MaterializedDirectory:
    import shutil
    if root.exists():
        shutil.rmtree(root)

    root.mkdir(parents=True, exist_ok=True)
    materialized_files: list[Path] = []

    for generated_file in directory.files:
        file_path = root / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)

        metadata_path = file_path.with_name(f"{file_path.name}.haxdex-meta.json")
        metadata = {
            "indexers": {
                asset_name: model_to_json_data(result)
                for asset_name, result in generated_file.results.items()
            }
        }
        metadata_path.write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        materialized_files.append(file_path)

    return MaterializedDirectory(
        root=root,
        files=materialized_files,
    )
