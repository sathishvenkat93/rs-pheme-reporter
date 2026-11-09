from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.models.grammar import (
    Agg,
    And,
    Compare,
    InList,
    MeasureSyntaxError,
    Named,
    Not,
    Or,
    Rate,
    Ratio,
    fields_used,
    format_measure,
    parse_measure,
)

VALID = [
    "count()",
    "count_if(status == 'passed')",
    'count_if(status in ["failed", "error"])',
    "count_if(status == 'failed' and suite != 'smoke')",
    "count_if(not (status == 'passed' or status == 'skipped'))",
    "count_if(latency_ms > 800)",
    "count_distinct(case)",
    "sum(bytes)",
    "avg(duration_ms)",
    "min(latency_ms)",
    "max(latency_ms)",
    "percentile(latency_ms, 95)",
    "percentile(latency_ms, 99.9)",
    "ratio(count_if(status == 'passed'), count())",
    "ratio(count_if(success == false), count())",
    "rate(count(), 'second')",
    "apdex(latency_ms, 500)",
    "flaky_rate()",
]


@pytest.mark.parametrize("expr", VALID)
def test_valid_expressions_parse_and_round_trip(expr: str) -> None:
    ast = parse_measure(expr)
    assert parse_measure(format_measure(ast)) == ast


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        ("count_if(status = 'passed') / count()", "Use =="),
        ("ratio(count(), count()) / 2", "Unexpected"),
        ("SELECT * FROM t", "Unexpected character|Unknown function"),
        ("count(); DROP TABLE x", "Unexpected character"),
        ("median(latency_ms)", "Unknown function 'median'"),
        ("percentile(latency_ms, 150)", "between 0 and 100"),
        ("rate(count(), 'hour')", "per 'second' or per 'minute'"),
        ("apdex(latency_ms, 0)", "positive threshold"),
        ("sum()", "field name"),
        ("count_if(status)", "comparison"),
        ("", "empty"),
        ("read_csv('/etc/passwd')", "Unknown function"),
    ],
)
def test_expressions_outside_the_grammar_are_rejected(expr: str, message: str) -> None:
    with pytest.raises(MeasureSyntaxError, match=message):
        parse_measure(expr)


def test_ast_shape() -> None:
    ast = parse_measure("ratio(count_if(status == 'passed' and suite in ['a', 'b']), count())")
    assert ast == Ratio(
        Agg("count_if", pred=And((Compare("status", "==", "passed"), InList("suite", ("a", "b"))))),
        Agg("count"),
    )
    assert fields_used(ast) == {"status", "suite"}
    assert fields_used(parse_measure("apdex(latency_ms, 500)")) == {"latency_ms"}
    assert parse_measure("rate(count(), 'minute')") == Rate(Agg("count"), "minute")
    assert parse_measure("flaky_rate()") == Named("flaky_rate")


# Property: any AST built from the grammar formats to text that parses back to itself.
fields = st.sampled_from(["status", "suite", "latency_ms", "build"])
literals = st.one_of(
    st.integers(-1000, 1000),
    st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False).filter(
        lambda f: not f.is_integer()
    ),
    st.text(st.characters(codec="ascii", exclude_categories=["Cc"]), max_size=8),
    st.booleans(),
)
compares = st.builds(Compare, fields, st.sampled_from(["==", "!=", "<", "<=", ">", ">="]), literals)
in_lists = st.builds(InList, fields, st.lists(literals, min_size=1, max_size=3).map(tuple))
preds = st.recursive(
    st.one_of(compares, in_lists),
    lambda inner: st.one_of(
        st.builds(And, st.lists(inner, min_size=2, max_size=3).map(tuple)),
        st.builds(Or, st.lists(inner, min_size=2, max_size=3).map(tuple)),
        st.builds(Not, inner),
    ),
    max_leaves=6,
)
aggs = st.one_of(
    st.just(Agg("count")),
    st.builds(lambda p: Agg("count_if", pred=p), preds),
    st.builds(
        lambda f, fn: Agg(fn, field=f), fields, st.sampled_from(["sum", "avg", "min", "max"])
    ),
    st.builds(lambda f, p: Agg("percentile", field=f, p=float(p)), fields, st.integers(1, 100)),
)
measures = st.recursive(
    aggs,
    lambda inner: st.one_of(
        st.builds(Ratio, inner, inner),
        st.builds(Rate, inner, st.sampled_from(["second", "minute"])),
    ),
    max_leaves=4,
)


def _normalise(p: object) -> object:
    """Nested And-in-And (or Or-in-Or) flattens when printed; compare in flattened form."""
    if isinstance(p, And | Or):
        items: list[object] = []
        for i in p.items:
            n = _normalise(i)
            items.extend(n.items if type(n) is type(p) else (n,))
        return type(p)(tuple(items))  # type: ignore[arg-type]
    if isinstance(p, Not):
        return Not(_normalise(p.item))  # type: ignore[arg-type]
    if isinstance(p, Agg) and p.pred is not None:
        return Agg(p.func, p.field, _normalise(p.pred), p.p)  # type: ignore[arg-type]
    if isinstance(p, Ratio):
        return Ratio(_normalise(p.numerator), _normalise(p.denominator))  # type: ignore[arg-type]
    if isinstance(p, Rate):
        return Rate(_normalise(p.measure), p.per)  # type: ignore[arg-type]
    return p


@given(measures)
def test_format_then_parse_is_identity(ast: object) -> None:
    text = format_measure(ast)  # type: ignore[arg-type]
    assert _normalise(parse_measure(text)) == _normalise(ast)
