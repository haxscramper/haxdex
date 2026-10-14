from hstd_py_lib.pydantic_utils import register_type

from __future__ import annotations

import base64
import importlib
import json
from pprint import pformat
import types

from beartype import beartype
from beartype.typing import Literal, Union, get_args, get_origin
import glom
import math
from pydantic import AfterValidator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Optional, TypeVar
import plumbum

import PIL.TiffImagePlugin
from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticSerializationError
from loguru import logger
import pandas as pd

from haxdex.services.utils import ExceptionContextNote

register_type(
    pd.DataFrame,
    "pd.DataFrame",
    dict[str, Any],
    dump=lambda it: {
        "columns": list(it.columns),
        "index": it.index.tolist(),
        "rows": it.to_dict(orient="records"),
    },
    load=lambda it: pd.DataFrame(it["rows"], columns=it["columns"]).set_index(
        pd.Index(it["index"])),
)

register_type(
    pd.Series,
    "pd.Series",
    dict[str, Any],
    dump=lambda it: {
        "name": it.name,
        "index": it.index.tolist(),
        "values": it.tolist(),
    },
    load=lambda it: pd.Series(it["values"], index=it["index"], name=it["name"]),
)

register_type(
    pd.Timestamp,
    "pd.Timestamp",
    str,
    dump=lambda it: it.isoformat(),
    load=lambda it: pd.Timestamp(it),
)

register_type(
    pd.Timedelta,
    "pd.Timedelta",
    str,
    dump=lambda it: it.isoformat(),
    load=lambda it: pd.Timedelta(it),
)

register_type(
    type(pd.NA),
    "pd.NA",
    type(None),
    dump=lambda it: None,
    load=lambda it: pd.NA,
)


class IFDRationalJson(BaseModel):
    value: int
    denominator: int


register_type(
    PIL.TiffImagePlugin.IFDRational,
    "PIL.TiffImagePlugin.IFDRational",
    IFDRationalJson,
    dump=lambda it: IFDRationalJson(
        value=it.numerator,
        denominator=it.denominator,
    ),
    load=lambda it: PIL.TiffImagePlugin.IFDRational(it.value, it.denominator),
)
