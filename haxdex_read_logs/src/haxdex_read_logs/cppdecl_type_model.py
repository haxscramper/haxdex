from __future__ import annotations

from enum import STRICT, IntEnum, IntFlag, StrEnum

from beartype import beartype
from beartype.typing import Annotated, Literal
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
        strict=True,
        allow_inf_nan=False,
    )


type JsonArray[T] = Annotated[tuple[T, ...], Field(strict=False)]


@beartype
def require_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"Expected an integer enum value, got {value!r} "
            f"of type {type(value).__name__}; coercion is not allowed"
        )
    return int(value)


type IntegerEnum[T: IntEnum | IntFlag] = Annotated[
    T,
    Field(strict=False),
    BeforeValidator(require_integer),
]


class CvQualifiers(IntFlag, boundary=STRICT):
    NONE = 0
    CONST = 1 << 0
    VOLATILE = 1 << 1
    RESTRICT = 1 << 2
    MSVC_PTR32 = 1 << 3
    MSVC_PTR64 = 1 << 4
    MSVC_UNALIGNED = 1 << 5


class RefQualifier(IntEnum):
    NONE = 0
    LVALUE = 1
    RVALUE = 2


class QualifiedNameFlags(IntFlag, boundary=STRICT):
    NONE = 0
    REDUNDANT_INT = 1 << 0


class SimpleTypeFlags(IntFlag, boundary=STRICT):
    NONE = 0
    UNSIGNED = 1 << 0
    EXPLICITLY_SIGNED = 1 << 1
    IMPLIED_INT = 1 << 2
    C_COMPLEX = 1 << 3
    C_IMAGINARY = 1 << 4
    C_IMPLIED_DOUBLE = 1 << 5


class SimpleTypePrefix(IntEnum):
    NONE = 0
    STRUCT = 1
    CLASS = 2
    UNION = 3
    ENUM = 4
    TYPENAME = 5


class IntegerBase(IntEnum):
    DECIMAL = 0
    BINARY = 1
    OCTAL = 2
    HEX = 3


class IntegerSignedSuffix(IntEnum):
    NONE = 0
    L = 1
    LL = 2
    Z = 3


class FloatingBase(IntEnum):
    DECIMAL = 0
    HEX = 1


class FloatingSuffix(IntEnum):
    F = 0
    L = 1
    F16 = 2
    F32 = 3
    F64 = 4
    F128 = 5
    BF16 = 6


class LiteralKind(IntEnum):
    CHARACTER = 0
    STRING = 1
    RAW_STRING = 2


class LiteralEncoding(IntEnum):
    NORMAL = 0
    WIDE = 1
    U8 = 2
    U16 = 3
    U32 = 4


class ExprListKind(IntEnum):
    PARENTHESES = 0
    CURLY = 1
    SQUARE = 2


class NewDeleteKind(IntEnum):
    NEW = 0
    NEW_ARRAY = 1
    DELETE = 2
    DELETE_ARRAY = 3


class AttributeStyle(IntEnum):
    CPP = 0
    GNU = 1


type CvQualifiersValue = Annotated[
    IntegerEnum[CvQualifiers],
    Field(ge=0, le=63),
    WithJsonSchema(
        {
            "title": "CvQualifiers",
            "type": "integer",
            "minimum": 0,
            "maximum": 63,
        }
    ),
]

type QualifiedNameFlagsValue = Annotated[
    IntegerEnum[QualifiedNameFlags],
    Field(ge=0, le=1),
    WithJsonSchema(
        {
            "title": "QualifiedNameFlags",
            "type": "integer",
            "minimum": 0,
            "maximum": 1,
        }
    ),
]

type SimpleTypeFlagsValue = Annotated[
    IntegerEnum[SimpleTypeFlags],
    Field(ge=0, le=63),
    WithJsonSchema(
        {
            "title": "SimpleTypeFlags",
            "type": "integer",
            "minimum": 0,
            "maximum": 63,
        }
    ),
]


class NumericKind(StrEnum):
    INTEGER = "integer"
    FLOATING = "floating"


class SuffixKind(StrEnum):
    UDL = "udl"
    BUILTIN = "builtin"


class NameKind(StrEnum):
    NAME = "name"
    OVERLOADED_OPERATOR = "overloaded_operator"
    CONVERSION_OPERATOR = "conversion_operator"
    USER_DEFINED_LITERAL = "user_defined_literal"
    DESTRUCTOR = "destructor"
    NEW_DELETE_OPERATOR = "new_delete_operator"
    UNSPELLABLE_NAME = "unspellable_name"


class ModifierKind(StrEnum):
    POINTER = "pointer"
    REFERENCE = "reference"
    MEMBER_POINTER = "member_pointer"
    ARRAY = "array"
    FUNCTION = "function"


class TemplateArgumentKind(StrEnum):
    TYPE = "type"
    EXPR = "expr"


class PunctuationToken(FrozenModel):
    value: str


class UdlSuffix(FrozenModel):
    kind: Literal[SuffixKind.UDL]
    value: str


class BuiltinIntegerSuffix(FrozenModel):
    kind: Literal[SuffixKind.BUILTIN]
    is_unsigned: bool
    signed_part: IntegerEnum[IntegerSignedSuffix]


class BuiltinFloatingSuffix(FrozenModel):
    kind: Literal[SuffixKind.BUILTIN]
    value: IntegerEnum[FloatingSuffix]


type IntegerSuffix = Annotated[
    UdlSuffix | BuiltinIntegerSuffix,
    Field(discriminator="kind"),
]

type FloatingLiteralSuffix = Annotated[
    UdlSuffix | BuiltinFloatingSuffix,
    Field(discriminator="kind"),
]


class IntegerLiteral(FrozenModel):
    kind: Literal[NumericKind.INTEGER]
    base: IntegerEnum[IntegerBase]
    value: str
    suffix: IntegerSuffix


class FloatingPointLiteral(FrozenModel):
    kind: Literal[NumericKind.FLOATING]
    base: IntegerEnum[FloatingBase]
    value_int: str
    value_frac: str | None
    value_exp: str
    suffix: FloatingLiteralSuffix


type NumericLiteral = Annotated[
    IntegerLiteral | FloatingPointLiteral,
    Field(discriminator="kind"),
]


class StringOrCharLiteral(FrozenModel):
    type: IntegerEnum[LiteralEncoding]
    kind: IntegerEnum[LiteralKind]
    value: str
    literal_suffix: str
    raw_string_delim: str


class PlainName(FrozenModel):
    kind: Literal[NameKind.NAME]
    value: str


class OverloadedOperator(FrozenModel):
    kind: Literal[NameKind.OVERLOADED_OPERATOR]
    token: str


class ConversionOperator(FrozenModel):
    kind: Literal[NameKind.CONVERSION_OPERATOR]
    target_type: Type


class UserDefinedLiteral(FrozenModel):
    kind: Literal[NameKind.USER_DEFINED_LITERAL]
    suffix: str
    space_before_suffix: bool


class DestructorName(FrozenModel):
    kind: Literal[NameKind.DESTRUCTOR]
    simple_type: SimpleType


class NewDeleteOperator(FrozenModel):
    kind: Literal[NameKind.NEW_DELETE_OPERATOR]
    op_kind: IntegerEnum[NewDeleteKind]


class UnspellableName(FrozenModel):
    kind: Literal[NameKind.UNSPELLABLE_NAME]
    name: str


type UnqualifiedNameVariant = Annotated[
    PlainName
    | OverloadedOperator
    | ConversionOperator
    | UserDefinedLiteral
    | DestructorName
    | NewDeleteOperator
    | UnspellableName,
    Field(discriminator="kind"),
]


class UnqualifiedName(FrozenModel):
    var: UnqualifiedNameVariant
    template_args: TemplateArgumentList | None


class QualifiedName(FrozenModel):
    force_global_scope: bool
    flags: QualifiedNameFlagsValue
    parts: JsonArray[UnqualifiedName]


class Attribute(FrozenModel):
    style: IntegerEnum[AttributeStyle]
    expr: PseudoExpr


class AttributeList(FrozenModel):
    attrs: JsonArray[Attribute]


class SimpleType(FrozenModel):
    attrs: AttributeList
    prefix: IntegerEnum[SimpleTypePrefix]
    flags: SimpleTypeFlagsValue
    quals: CvQualifiersValue
    name: QualifiedName


class Pointer(FrozenModel):
    kind: Literal[ModifierKind.POINTER]
    quals: CvQualifiersValue


class Reference(FrozenModel):
    kind: Literal[ModifierKind.REFERENCE]
    ref_kind: IntegerEnum[RefQualifier]
    quals: CvQualifiersValue


class MemberPointer(FrozenModel):
    kind: Literal[ModifierKind.MEMBER_POINTER]
    base: QualifiedName
    quals: CvQualifiersValue


class Array(FrozenModel):
    kind: Literal[ModifierKind.ARRAY]
    size: PseudoExpr


class Function(FrozenModel):
    kind: Literal[ModifierKind.FUNCTION]
    params: JsonArray[Decl]
    c_style_variadic: bool
    c_style_variadic_without_comma: bool
    c_style_void_params: bool
    cv_quals: CvQualifiersValue
    ref_qual: IntegerEnum[RefQualifier]
    noexcept: bool
    uses_trailing_return_type: bool


type TypeModifier = Annotated[
    Pointer | Reference | MemberPointer | Array | Function,
    Field(discriminator="kind"),
]


class Type(FrozenModel):
    simple_type: SimpleType
    modifiers: JsonArray[TypeModifier]


class PseudoExprList(FrozenModel):
    kind: IntegerEnum[ExprListKind]
    elems: JsonArray[PseudoExpr]
    has_trailing_comma: bool


class PseudoExprToken(FrozenModel):
    value: PseudoExprTokenValue


class PseudoExpr(FrozenModel):
    tokens: JsonArray[PseudoExprToken]


class TypeTemplateArgument(FrozenModel):
    kind: Literal[TemplateArgumentKind.TYPE]
    value: Type


class ExprTemplateArgument(FrozenModel):
    kind: Literal[TemplateArgumentKind.EXPR]
    value: PseudoExpr


type TemplateArgument = Annotated[
    TypeTemplateArgument | ExprTemplateArgument,
    Field(discriminator="kind"),
]


class TemplateArgumentList(FrozenModel):
    args: JsonArray[TemplateArgument]


type PseudoExprTokenValue = (
    SimpleType
    | PunctuationToken
    | NumericLiteral
    | StringOrCharLiteral
    | PseudoExprList
    | TemplateArgumentList
)


class Decl(FrozenModel):
    type: Type
    name: QualifiedName


class MaybeAmbiguous[T: FrozenModel](FrozenModel):
    value: T
    ambiguous_alternatives: JsonArray[T]


class NormalizedQualifierTag(StrEnum):
    CONST = "const"
    VOLATILE = "volatile"
    RESTRICT = "restrict"
    PTR32 = "ptr32"
    PTR64 = "ptr64"
    UNALIGNED = "unaligned"
    LV = "lv"
    RV = "rv"
    STRUCT = "struct"
    CLASS = "class"
    UNION = "union"
    ENUM = "enum"
    TYPENAME = "typename"
    UNSIGNED = "unsigned"
    SIGNED = "signed"
    IMPLIED_INT = "implied_int"
    C_COMPLEX = "c_complex"
    C_IMAGINARY = "c_imaginary"
    C_IMPLIED_DOUBLE = "c_implied_double"
    REDUNDANT_INT = "redundant_int"
    NUMBER = "number"
    INTEGER = "integer"
    BASE10 = "base10"
    BASE2 = "base2"
    BASE8 = "base8"
    BASE16 = "base16"
    FLOATING = "floating"
    LITERAL = "literal"
    CHAR = "char"
    STRING = "string"
    RAW_STRING = "raw_string"
    NORMAL = "normal"
    WIDE = "wide"
    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    LITERAL_SUFFIX = "literal_suffix"
    PUNCT = "punct"
    TEMPLATE_ARG = "template_arg"
    TEMPLATE_ARGS = "template_args"
    LIST = "list"
    PAREN = "paren"
    CURLY = "curly"
    SQUARE = "square"
    TRAILING_COMMA = "trailing_comma"
    EXPR = "expr"
    SIMPLE_TYPE = "simple_type"
    OVERLOADED_OPERATOR = "overloaded_operator"
    OPERATOR_TOKEN = "operator_token"
    CONVERSION_OPERATOR = "conversion_operator"
    UDL = "udl"
    UDL_SUFFIX = "udl_suffix"
    DESTRUCTOR = "destructor"
    NEW_DELETE_OPERATOR = "new_delete_operator"
    NEW = "new"
    NEW_ARRAY = "new_array"
    DELETE = "delete"
    DELETE_ARRAY = "delete_array"
    UNSPELLABLE = "unspellable"
    GLOBAL = "global"
    PTR = "ptr"
    MEMBER_PTR = "member_ptr"
    ARRAY = "array"
    ARRAY_SIZE = "array_size"
    FN = "fn"
    NOEXCEPT = "noexcept"
    TRAILING_RETURN_TYPE = "trailing_return_type"
    C_VOID_PARAMS = "c_void_params"
    C_VARIADIC = "c_variadic"
    C_VARIADIC_NO_COMMA = "c_variadic_no_comma"


class NormalizedQualifier(FrozenModel):
    t: Annotated[NormalizedQualifierTag, Field(strict=False)]


class NormalizedIdentifier(FrozenModel):
    name: str
    cvr: JsonArray[NormalizedQualifier]


class NormalizedName(FrozenModel):
    name: NormalizedIdentifier
    params: JsonArray[NormalizedName]


class NormalizedRoot(FrozenModel):
    names: NormalizedName


class NormalizedAmbiguousRoot(NormalizedRoot):
    ambiguous_alternatives: JsonArray[NormalizedName]


for model in (
    ConversionOperator,
    DestructorName,
    UnqualifiedName,
    QualifiedName,
    Attribute,
    AttributeList,
    SimpleType,
    MemberPointer,
    Array,
    Function,
    Type,
    PseudoExprList,
    PseudoExprToken,
    PseudoExpr,
    TypeTemplateArgument,
    ExprTemplateArgument,
    TemplateArgumentList,
    Decl,
    NormalizedName,
    NormalizedRoot,
    NormalizedAmbiguousRoot,
):
    model.model_rebuild()


@beartype
class QualType(FrozenModel):
    name: str
    params: list[QualType] = Field(default_factory=list)
    scopes: list[QualType] = Field(default_factory=list)
    qualifiers: list[str] = Field(default_factory=list)

    def format(self) -> str:
        scopes = [it.format() for it in self.scopes]
        params = [it.format() for it in self.params]

        return "::".join(
            [
                *scopes,
                " ".join([self.name, *self.qualifiers]),
                *(["<{}>".format(", ".join(params))] if params else []),
            ]
        )


_JUNK_QUALIFIERS = [
    NormalizedQualifierTag.TEMPLATE_ARG,
]


@beartype
def to_qual_type(value: NormalizedRoot | NormalizedName) -> QualType:
    match value:
        case NormalizedRoot():
            return to_qual_type(value.names)

        case NormalizedName():
            qualifiers = [
                qualifier.t.value
                for qualifier in value.name.cvr
                if qualifier.t not in _JUNK_QUALIFIERS
            ]
            params = [to_qual_type(param) for param in value.params]

            if value.name.name == "<qualified>":
                if not params:
                    raise ValueError(
                        "Cannot simplify '<qualified>': params is empty; "
                        "expected scope components followed by a terminal name."
                    )

                terminal = params[-1]
                return terminal.model_copy(
                    update=dict(
                        scopes=[*params[:-1], *terminal.scopes],
                        qualifiers=[*qualifiers, *terminal.qualifiers],
                    )
                )

            elif "lambda at" in value.name.name:
                # logger.info(value.name.name)
                return QualType(name="(lambda)")

            return QualType(
                name=value.name.name,
                params=params,
                qualifiers=qualifiers,
            )
