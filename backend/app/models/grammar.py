"""Pheme's measure expression language.

A small, whitelisted grammar parsed into an AST. It is never evaluated and never passed
through as SQL: the query compiler (M5) walks the AST.

    measure    := agg | combinator | named
    agg        := count() | count_if(pred) | count_distinct(f) | sum(f) | avg(f)
                | min(f) | max(f) | percentile(f, p)
    combinator := ratio(measure, measure) | rate(measure, 'second' | 'minute')
    named      := apdex(f, t_ms) | flaky_rate()
    pred       := disj ;  disj := conj ('or' conj)* ;  conj := neg ('and' neg)*
    neg        := 'not' neg | '(' pred ')' | f op literal | f 'in' '[' literal (, literal)* ']'
    op         := == | != | < | <= | > | >=
    literal    := number | 'string' | "string" | true | false
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Scalar = str | int | float | bool
CmpOp = Literal["==", "!=", "<", "<=", ">", ">="]


class MeasureSyntaxError(ValueError):
    """A measure expression outside the grammar. The message is meant for users."""


# ---------------------------------------------------------------------------- AST


@dataclass(frozen=True, slots=True)
class Compare:
    field: str
    op: CmpOp
    value: Scalar


@dataclass(frozen=True, slots=True)
class InList:
    field: str
    values: tuple[Scalar, ...]


@dataclass(frozen=True, slots=True)
class And:
    items: tuple[Pred, ...]


@dataclass(frozen=True, slots=True)
class Or:
    items: tuple[Pred, ...]


@dataclass(frozen=True, slots=True)
class Not:
    item: Pred


Pred = Compare | InList | And | Or | Not

AggFunc = Literal["count", "count_if", "count_distinct", "sum", "avg", "min", "max", "percentile"]


@dataclass(frozen=True, slots=True)
class Agg:
    func: AggFunc
    field: str | None = None
    pred: Pred | None = None
    p: float | None = None


@dataclass(frozen=True, slots=True)
class Ratio:
    numerator: MeasureExpr
    denominator: MeasureExpr


@dataclass(frozen=True, slots=True)
class Rate:
    measure: MeasureExpr
    per: Literal["second", "minute"]


@dataclass(frozen=True, slots=True)
class Named:
    func: Literal["apdex", "flaky_rate"]
    field: str | None = None
    threshold: float | None = None


MeasureExpr = Agg | Ratio | Rate | Named

FIELD_FUNCS: frozenset[str] = frozenset({"count_distinct", "sum", "avg", "min", "max"})
ALL_FUNCS: tuple[str, ...] = (
    "count",
    "count_if",
    "count_distinct",
    "sum",
    "avg",
    "min",
    "max",
    "percentile",
    "ratio",
    "rate",
    "apdex",
    "flaky_rate",
)

# ---------------------------------------------------------------------------- lexer

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<op>==|!=|<=|>=|<|>)
  | (?P<punct>[(),\[\]])
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class _Tok:
    kind: str
    text: str
    pos: int


def _tokenize(expr: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if m is None and expr[pos] == "=":
            raise MeasureSyntaxError(f"Use == to compare values (single = at position {pos}).")
        if m is None:
            raise MeasureSyntaxError(f"Unexpected character {expr[pos]!r} at position {pos}.")
        kind = m.lastgroup or ""
        if kind != "ws":
            tokens.append(_Tok(kind, m.group(), pos))
        pos = m.end()
    tokens.append(_Tok("end", "", len(expr)))
    return tokens


# ---------------------------------------------------------------------------- parser


class _Parser:
    def __init__(self, expr: str) -> None:
        self.toks = _tokenize(expr)
        self.i = 0

    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def _next(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect(self, text: str) -> None:
        t = self._next()
        if t.text != text:
            found = "end of expression" if t.kind == "end" else repr(t.text)
            raise MeasureSyntaxError(f"Expected {text!r} at position {t.pos}, found {found}.")

    def _field(self) -> str:
        t = self._next()
        if t.kind != "ident" or t.text in {"and", "or", "not", "in", "true", "false"}:
            raise MeasureSyntaxError(f"Expected a field name at position {t.pos}.")
        return t.text

    def _number(self) -> float:
        t = self._next()
        if t.kind != "number":
            raise MeasureSyntaxError(f"Expected a number at position {t.pos}.")
        return float(t.text)

    def _literal(self) -> Scalar:
        t = self._next()
        if t.kind == "number":
            return int(t.text) if t.text.lstrip("-").isdigit() else float(t.text)
        if t.kind == "string":
            return re.sub(r"\\(.)", r"\1", t.text[1:-1])
        if t.kind == "ident" and t.text in {"true", "false"}:
            return t.text == "true"
        raise MeasureSyntaxError(
            f"Expected a value (number, quoted text, true or false) at position {t.pos}."
        )

    # measure ---------------------------------------------------------------

    def measure(self) -> MeasureExpr:
        t = self._next()
        if t.kind != "ident":
            raise MeasureSyntaxError(f"Expected a function such as count() at position {t.pos}.")
        name = t.text
        if name not in ALL_FUNCS:
            raise MeasureSyntaxError(f"Unknown function {name!r}. Allowed: {', '.join(ALL_FUNCS)}.")
        self._expect("(")
        result: MeasureExpr
        if name in {"count", "flaky_rate"}:
            result = Agg("count") if name == "count" else Named("flaky_rate")
        elif name == "count_if":
            result = Agg("count_if", pred=self.pred())
        elif name in FIELD_FUNCS:
            result = Agg(name, field=self._field())  # type: ignore[arg-type]
        elif name == "percentile":
            f = self._field()
            self._expect(",")
            p = self._number()
            if not 0 < p <= 100:
                raise MeasureSyntaxError("percentile() needs a percentile between 0 and 100.")
            result = Agg("percentile", field=f, p=p)
        elif name == "ratio":
            num = self.measure()
            self._expect(",")
            result = Ratio(num, self.measure())
        elif name == "rate":
            m = self.measure()
            self._expect(",")
            per = self._literal()
            if per not in ("second", "minute"):
                raise MeasureSyntaxError("rate() is per 'second' or per 'minute'.")
            result = Rate(m, per)  # type: ignore[arg-type]
        else:  # apdex
            f = self._field()
            self._expect(",")
            threshold = self._number()
            if threshold <= 0:
                raise MeasureSyntaxError("apdex() needs a positive threshold in ms.")
            result = Named("apdex", field=f, threshold=threshold)
        self._expect(")")
        return result

    # predicates ------------------------------------------------------------

    def pred(self) -> Pred:
        items = [self._conj()]
        while self.tok.text == "or":
            self._next()
            items.append(self._conj())
        return items[0] if len(items) == 1 else Or(tuple(items))

    def _conj(self) -> Pred:
        items = [self._neg()]
        while self.tok.text == "and":
            self._next()
            items.append(self._neg())
        return items[0] if len(items) == 1 else And(tuple(items))

    def _neg(self) -> Pred:
        if self.tok.text == "not":
            self._next()
            return Not(self._neg())
        if self.tok.text == "(":
            self._next()
            inner = self.pred()
            self._expect(")")
            return inner
        f = self._field()
        t = self._next()
        if t.text == "in":
            self._expect("[")
            values = [self._literal()]
            while self.tok.text == ",":
                self._next()
                values.append(self._literal())
            self._expect("]")
            return InList(f, tuple(values))
        if t.kind != "op":
            raise MeasureSyntaxError(f"Expected a comparison after {f!r} at position {t.pos}.")
        return Compare(f, t.text, self._literal())  # type: ignore[arg-type]


def parse_measure(expr: str) -> MeasureExpr:
    """Parse ``expr`` or raise :class:`MeasureSyntaxError` with a readable message."""
    if not expr.strip():
        raise MeasureSyntaxError("The measure expression is empty.")
    parser = _Parser(expr)
    result = parser.measure()
    if parser.tok.kind != "end":
        raise MeasureSyntaxError(
            f"Unexpected {parser.tok.text!r} at position {parser.tok.pos}: an expression is one "
            "function call, e.g. ratio(count_if(status == 'passed'), count())."
        )
    return result


# ---------------------------------------------------------------------------- helpers


def _fmt_literal(v: Scalar) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return repr(v)


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(v)


def format_pred(p: Pred) -> str:
    match p:
        case Compare(field, op, value):
            return f"{field} {op} {_fmt_literal(value)}"
        case InList(field, values):
            return f"{field} in [{', '.join(_fmt_literal(v) for v in values)}]"
        case And(items):
            return " and ".join(
                f"({format_pred(i)})" if isinstance(i, Or) else format_pred(i) for i in items
            )
        case Or(items):
            return " or ".join(format_pred(i) for i in items)
        case Not(item):
            inner = format_pred(item)
            return f"not ({inner})" if isinstance(item, And | Or) else f"not {inner}"


def format_measure(m: MeasureExpr) -> str:
    """Canonical text for an AST (``parse_measure(format_measure(a)) == a``)."""
    match m:
        case Agg("count"):
            return "count()"
        case Agg("count_if", pred=pred) if pred is not None:
            return f"count_if({format_pred(pred)})"
        case Agg("percentile", field=f, p=p) if p is not None:
            return f"percentile({f}, {_fmt_num(p)})"
        case Agg(func, field=f):
            return f"{func}({f})"
        case Ratio(n, d):
            return f"ratio({format_measure(n)}, {format_measure(d)})"
        case Rate(inner, per):
            return f"rate({format_measure(inner)}, '{per}')"
        case Named("flaky_rate"):
            return "flaky_rate()"
        case Named(_, field=f, threshold=t):
            return f"apdex({f}, {_fmt_num(t or 0)})"
    raise AssertionError(f"unhandled measure node {m!r}")  # pragma: no cover


def fields_used(m: MeasureExpr) -> set[str]:
    """Every column a measure reads."""

    def from_pred(p: Pred) -> set[str]:
        match p:
            case Compare(field, _, _) | InList(field, _):
                return {field}
            case And(items) | Or(items):
                return set().union(*(from_pred(i) for i in items))
            case Not(item):
                return from_pred(item)

    match m:
        case Agg(_, field=f, pred=pred):
            out = {f} if f else set()
            return out | (from_pred(pred) if pred is not None else set())
        case Ratio(n, d):
            return fields_used(n) | fields_used(d)
        case Rate(inner, _):
            return fields_used(inner)
        case Named(_, field=f):
            return {f} if f else set()
    raise AssertionError(f"unhandled measure node {m!r}")  # pragma: no cover
