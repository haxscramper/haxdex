from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import PIL.TiffImagePlugin
import pandas as pd
import pytest
from beartype import beartype
from beartype.typing import Any, Annotated, ClassVar, Literal, Optional, Union
from pydantic import BaseModel, Field
from pydantic_core import PydanticSerializationError

from haxdex.services.pydantic_utils import _DUMPERS, from_json_safe, model_to_json_data, to_json_safe


class IndexDocument(BaseModel, extra="forbid"):
    kind: Literal["processed"] = "processed"
    hash: str
    vector_index: ClassVar[Optional[Any]] = None
    full_text_index: ClassVar[Optional[Any]] = None


class ModelA(IndexDocument):
    value: str


class ModelB(IndexDocument):
    score: int


class SourceSpan(BaseModel, extra="forbid"):
    source_hash: str
    source_start: int
    source_end: int
    chunk_start: int
    chunk_end: int
    kind: Literal["core", "start_overlap", "end_overlap"] = "core"


class ChunkFile(IndexDocument):
    file_hash: str
    type: Literal["chunkFile"] = "chunkFile"


class ChunkDocument(IndexDocument):
    file_hash: str
    type: Literal["chunk"] = "chunk"
    index: int
    text: str
    char_count: int
    token_count: int
    unit: str
    spans: list[SourceSpan]


class FullTextChunk(ChunkDocument):
    pass


FullTextResultType = Annotated[
    Union[ChunkFile, FullTextChunk],
    Field(discriminator="type"),
]


@beartype
def compare_values(lhs: Any, rhs: Any, path: str, failures: list[str]) -> None:
    match (lhs, rhs):
        case (BaseModel(), BaseModel()):
            if type(lhs) is not type(rhs):
                failures.append(
                    f"{path}: model type mismatch lhs={type(lhs).__name__} rhs={type(rhs).__name__}"
                )
            compare_values(lhs.model_dump(), rhs.model_dump(), path, failures)

        case (pd.DataFrame(), pd.DataFrame()):
            if not lhs.equals(rhs):
                failures.append(
                    f"{path}: dataframe mismatch lhs={lhs.to_dict(orient='split')} rhs={rhs.to_dict(orient='split')}"
                )

        case (pd.Series(), pd.Series()):
            if not lhs.equals(rhs):
                failures.append(
                    f"{path}: series mismatch lhs={lhs.to_dict()} rhs={rhs.to_dict()}")

        case (pd.Timestamp(), pd.Timestamp()):
            if lhs != rhs:
                failures.append(f"{path}: timestamp mismatch lhs={lhs} rhs={rhs}")

        case (pd.Timedelta(), pd.Timedelta()):
            if lhs != rhs:
                failures.append(f"{path}: timedelta mismatch lhs={lhs} rhs={rhs}")

        case (dict(), dict()):
            lhs_keys = set(lhs.keys())
            rhs_keys = set(rhs.keys())
            for key in sorted(lhs_keys - rhs_keys, key=str):
                failures.append(f"{path}.{key}: key missing in rhs")
            for key in sorted(rhs_keys - lhs_keys, key=str):
                failures.append(f"{path}.{key}: key missing in lhs")
            for key in sorted(lhs_keys & rhs_keys, key=str):
                compare_values(lhs[key], rhs[key], f"{path}.{key}", failures)

        case (list(), list()) | (tuple(), tuple()):
            lhs_len = len(lhs)
            rhs_len = len(rhs)
            if lhs_len != rhs_len:
                failures.append(f"{path}: length mismatch lhs={lhs_len} rhs={rhs_len}")
            for idx, pair in enumerate(zip(lhs, rhs)):
                left_item, right_item = pair
                compare_values(left_item, right_item, f"{path}[{idx}]", failures)

        case (set(), set()) | (frozenset(), frozenset()):
            if lhs != rhs:
                failures.append(f"{path}: set mismatch lhs={lhs} rhs={rhs}")

        case (float(), float()):
            lhs_nan = math.isnan(lhs)
            rhs_nan = math.isnan(rhs)
            if lhs_nan and rhs_nan:
                return
            if lhs != rhs:
                failures.append(f"{path}: float mismatch lhs={lhs} rhs={rhs}")

        case _:
            if lhs is pd.NA and rhs is pd.NA:
                return
            if lhs != rhs:
                failures.append(f"{path}: value mismatch lhs={lhs!r} rhs={rhs!r}")


@beartype
def use_any_target_for_roundtrip(target_type: Any) -> bool:
    match target_type:
        case type() as tp:
            if issubclass(tp, BaseModel):
                return False

            else:
                return any(
                    issubclass(tp, registered_type) for registered_type in _DUMPERS)
        case _:
            return False


@beartype
def verify_roundtrip(value: Any, target_type: Any, expected: Any | None = None) -> None:
    serialized = model_to_json_data(value)
    deserialize_target = Any if use_any_target_for_roundtrip(target_type) else target_type
    restored = from_json_safe(serialized, deserialize_target)

    match target_type:
        case type() as tp:
            if deserialize_target is Any and not isinstance(restored, tp):
                pytest.fail(
                    f"restored value has unexpected runtime type, expected {tp.__name__}, got {type(restored).__name__}"
                )

    expected_value = value if expected is None else expected
    failures: list[str] = []
    compare_values(expected_value, restored, "$", failures)
    if 0 < len(failures):
        pytest.fail("roundtrip mismatches:\n" + "\n".join(failures))


def test_roundtrip_scalars_and_collections() -> None:
    verify_roundtrip(None, type(None))
    verify_roundtrip(True, bool)
    verify_roundtrip(17, int)
    verify_roundtrip("alpha", str)
    verify_roundtrip(3.5, float)
    verify_roundtrip(b"\x00\x7f\xff", bytes)
    verify_roundtrip([1, 2, 3], list[int])
    verify_roundtrip((1, "x", 3), tuple[int, str, int])
    verify_roundtrip({1, 2, 3}, set[int])
    verify_roundtrip({"k1": 1, "k2": [1, 2]}, dict[str, Any])


def test_roundtrip_standard_registered_types() -> None:
    verify_roundtrip(Path("/tmp/a/b/c.txt"), Path)
    verify_roundtrip(datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc), datetime)

    rational = PIL.TiffImagePlugin.IFDRational(7, 11)
    verify_roundtrip(rational, PIL.TiffImagePlugin.IFDRational)


def test_roundtrip_pandas_types() -> None:
    frame = pd.DataFrame(
        {
            "name": ["a", "b"],
            "value": [10, 20]
        },
        index=pd.Index(["row1", "row2"], name="rid"),
    )
    verify_roundtrip(frame, pd.DataFrame)

    series = pd.Series([100, 200], index=["a", "b"], name="score")
    verify_roundtrip(series, pd.Series)

    verify_roundtrip(pd.Timestamp("2025-01-01T10:11:12Z"), pd.Timestamp)
    verify_roundtrip(pd.Timedelta("2 days 03:04:05"), pd.Timedelta)
    verify_roundtrip(pd.NA, type(pd.NA))


def test_float_nan_and_inf_projection_to_none() -> None:
    payload = {
        "nan": float("nan"),
        "inf": float("inf"),
        "ninf": float("-inf"),
        "ok": 2.25,
    }
    serialized = to_json_safe(payload)
    assert serialized == {"nan": None, "inf": None, "ninf": None, "ok": 2.25}


def test_roundtrip_concrete_models_and_map_of_basemodel() -> None:
    model_a = ModelA(
        hash="A" * 64,
        value="payload-a",
    )
    model_b = ModelB(
        hash="B" * 64,
        score=5,
    )

    verify_roundtrip(model_a, ModelA)

    model_map: dict[str, BaseModel] = {
        "a": model_a,
        "b": model_b,
    }
    verify_roundtrip(model_map, dict[str, BaseModel])


def test_roundtrip_discriminated_union_ignores_runtime_model_hint() -> None:
    value = FullTextChunk(
        hash="C" * 64,
        file_hash="D" * 64,
        index=0,
        text="alpha\nbeta\ngamma",
        char_count=16,
        token_count=5,
        unit="chars",
        spans=[
            SourceSpan(
                source_hash="E" * 64,
                source_start=0,
                source_end=16,
                chunk_start=0,
                chunk_end=16,
                kind="core",
            )
        ],
    )
    serialized = model_to_json_data(value)
    serialized["__pydantic_type__"] = (
        f"{ChunkDocument.__module__}:{ChunkDocument.__qualname__}")
    restored = from_json_safe(serialized, FullTextResultType)

    failures: list[str] = []
    compare_values(value, restored, "$", failures)
    if 0 < len(failures):
        pytest.fail("roundtrip mismatches:\n" + "\n".join(failures))


def test_unregistered_type_raises_serialization_error() -> None:

    class Unsupported:

        def __init__(self, value: int) -> None:
            self.value = value

    with pytest.raises(
            PydanticSerializationError,
            match="no serializer registered for type",
    ):
        to_json_safe(Unsupported(1))
