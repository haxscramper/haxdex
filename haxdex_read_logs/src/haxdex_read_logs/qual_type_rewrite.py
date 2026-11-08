from dataclasses import dataclass
from types import EllipsisType

from beartype import beartype
from haxdex_read_logs.cppdecl_type_model import QualType
from matchpy import (
    Arity,
    Expression,
    Operation,
    Pattern,
    ReplacementRule,
    Symbol,
    Wildcard,
)
from matchpy import (
    match as match_term,
)


@beartype
@dataclass(frozen=True)
class Binding:
    name: str
    sequence: bool = False


@beartype
@dataclass(frozen=True)
class Reference:
    name: str


@beartype
class T:
    @staticmethod
    def bind(
        name: str,
        sequence: EllipsisType | None = None,
    ) -> Binding:
        return Binding(name=name, sequence=sequence is Ellipsis)

    @staticmethod
    def get(name: str) -> Reference:
        return Reference(name=name)


TypeTerm = Operation.new("TypeTerm", Arity(4, True))
ParamsTerm = Operation.new("ParamsTerm", Arity.variadic)
ScopesTerm = Operation.new("ScopesTerm", Arity.variadic)
QualifiersTerm = Operation.new("QualifiersTerm", Arity.variadic)


@beartype
class QualTypeRewriter:
    def __init__(self) -> None:
        self.rules: list[ReplacementRule] = []

    def rule(self, source: object, target: object) -> None:
        bindings: dict[str, Binding] = {}

        def capture(binding: Binding) -> Wildcard:
            previous = bindings.get(binding.name)
            if previous is not None and previous != binding:
                raise ValueError(
                    f"Capture {binding.name!r} mixes single-type and "
                    f"sequence bindings in pattern {source!r}"
                )

            bindings[binding.name] = binding
            variable = f"capture_{binding.name}"

            if binding.sequence:
                return Wildcard.star(variable)

            return Wildcard.dot(variable)

        def match_sequence(spec: object) -> list[Expression]:
            match spec:
                case list():
                    items = spec
                case EllipsisType() | Binding():
                    items = [spec]
                case _:
                    raise ValueError(
                        f"Invalid sequence pattern {spec!r}: expected "
                        f"a list, ..., or T.bind(...)"
                    )

            result: list[Expression] = []

            for item in items:
                match item:
                    case EllipsisType():
                        result.append(Wildcard.star())
                    case Binding():
                        result.append(capture(item))
                    case _:
                        result.append(match_node(item))

            return result

        def match_name(spec: object) -> Expression:
            match spec:
                case Binding(sequence=False):
                    return capture(spec)

                case Binding(sequence=True):
                    raise ValueError(
                        f"Type name capture {spec.name!r} cannot capture "
                        f"a sequence: a type has exactly one name"
                    )

                case _:
                    return Symbol(self.pattern_name(spec))

        def match_node(spec: object, root: bool = False) -> Expression:
            match spec:
                case str():
                    return match_node(([], spec, []), root)

                case Binding(sequence=False):
                    return capture(spec)

                case (scopes, name, params):
                    qualifiers = (
                        Wildcard.dot("root_qualifiers") if root else Wildcard.dot()
                    )

                    return TypeTerm(
                        match_name(name),
                        ParamsTerm(*match_sequence(params)),
                        ScopesTerm(*match_sequence(scopes)),
                        qualifiers,
                    )

                case _:
                    raise ValueError(
                        f"Invalid type match pattern {spec!r}: expected "
                        f"a string, (scopes, name, params), or "
                        f"a single-type T.bind(...)"
                    )

        expression = match_node(source, root=True)

        def replacement(
            **captures: Expression | tuple[Expression, ...],
        ) -> Expression:
            def lookup(reference: Reference) -> Expression | tuple[Expression, ...]:
                if reference.name not in bindings:
                    raise ValueError(
                        f"Replacement references {reference.name!r}, "
                        f"but pattern {source!r} binds only "
                        f"{list(bindings)!r}"
                    )

                return captures[f"capture_{reference.name}"]

            def build_sequence(spec: object) -> list[Expression]:
                match spec:
                    case list():
                        items = spec
                    case Reference():
                        items = [spec]
                    case _:
                        raise ValueError(
                            f"Invalid replacement sequence {spec!r}: "
                            f"expected a list or T.get(...); "
                            f"... is only valid in match patterns"
                        )

                result: list[Expression] = []

                for item in items:
                    match item:
                        case Reference():
                            value = lookup(item)
                            match value:
                                case tuple():
                                    result.extend(value)
                                case Expression():
                                    result.append(value)
                        case _:
                            result.append(build_node(item))

                return result

            def build_name(spec: object) -> Symbol:
                match spec:
                    case Reference():
                        value = lookup(spec)

                        match value:
                            case Symbol():
                                return value

                            case _:
                                raise ValueError(
                                    f"Capture {spec.name!r} cannot be used "
                                    f"as a type name: expected a captured "
                                    f"name Symbol, got {value!r}"
                                )

                    case _:
                        return Symbol(self.pattern_name(spec))

            def build_node(spec: object, root: bool = False) -> Expression:
                match spec:
                    case str():
                        return build_node(([], spec, []), root)

                    case Reference():
                        value = lookup(spec)
                        match value:
                            case Expression():
                                return value
                            case tuple():
                                raise ValueError(
                                    f"Capture {spec.name!r} contains "
                                    f"{len(value)} types; use T.get(...) "
                                    f"in a scopes or params list, not "
                                    f"as a single type"
                                )

                    case (scopes, name, params):
                        qualifiers = (
                            captures["root_qualifiers"]
                            if root and "root_qualifiers" in captures
                            else QualifiersTerm()
                        )

                        return TypeTerm(
                            build_name(name),
                            ParamsTerm(*build_sequence(params)),
                            ScopesTerm(*build_sequence(scopes)),
                            qualifiers,
                        )

                    case _:
                        raise ValueError(
                            f"Invalid type replacement {spec!r}: expected "
                            f"a string, (scopes, name, params), or T.get(...)"
                        )

            return build_node(target, root=True)

        self.rules.append(ReplacementRule(Pattern(expression), replacement))

    def pattern_name(self, spec: object) -> str:
        match spec:
            case str():
                return spec
            case [str() as name]:
                return name
            case _:
                raise ValueError(
                    f"Invalid type name {spec!r}: expected a string "
                    f"or a singleton string list"
                )

    def encode(self, value: QualType) -> Expression:
        return TypeTerm(
            Symbol(value.name),
            ParamsTerm(*(self.encode(param) for param in value.params)),
            ScopesTerm(*(self.encode(scope) for scope in value.scopes)),
            QualifiersTerm(*(Symbol(qualifier) for qualifier in value.qualifiers)),
        )

    def symbol_name(self, expression: Expression) -> str:
        match expression:
            case Symbol(name=name):
                return name
            case _:
                raise ValueError(
                    f"Expected a Symbol for a type name or qualifier, "
                    f"got {expression!r}"
                )

    def decode(self, expression: Expression) -> QualType:
        match expression:
            case TypeTerm(
                operands=[
                    Symbol(name=name),
                    ParamsTerm(operands=params),
                    ScopesTerm(operands=scopes),
                    QualifiersTerm(operands=qualifiers),
                ]
            ):
                return QualType(
                    name=name,
                    params=[self.decode(param) for param in params],
                    scopes=[self.decode(scope) for scope in scopes],
                    qualifiers=[
                        self.symbol_name(qualifier) for qualifier in qualifiers
                    ],
                )
            case _:
                raise ValueError(
                    f"Cannot decode rewritten type {expression!r}: expected "
                    f"TypeTerm(Symbol, ParamsTerm, ScopesTerm, QualifiersTerm)"
                )

    def simplify(self, expression: Expression) -> Expression:
        match expression:
            case Operation():
                expression = type(expression)(
                    *(self.simplify(item) for item in expression.operands)
                )

        if not isinstance(expression, TypeTerm):
            return expression

        for rule in self.rules:
            for captures in match_term(expression, rule.pattern):
                replacement = rule.replacement(**captures)

                if replacement != expression:
                    return self.simplify(replacement)

        return expression

    def rewrite(self, value: QualType) -> QualType:
        return self.decode(self.simplify(self.encode(value)))
