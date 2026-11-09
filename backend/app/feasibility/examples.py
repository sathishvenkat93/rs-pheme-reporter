"""The R5 example requests, as hand-written ChartRequests (text parsing arrives with M8).

Each is paired with the fixture where it is meaningful; Volume uses a synthetic 8M-row
profile because no fixture is that large.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.chart import ChartRequest, FieldPhrase, FieldRoleHint

X, Y, SPLIT, M = FieldRoleHint.X, FieldRoleHint.Y, FieldRoleHint.SPLIT, FieldRoleHint.MEASURE


def _req(
    rid: str,
    text: str,
    chart: str | None,
    *phrases: tuple[str, FieldRoleHint | None],
    grain: str | None = None,
) -> ChartRequest:
    return ChartRequest(
        id=rid, raw_text=text, chart_type=chart, time_grain=grain,
        field_phrases=[FieldPhrase(text=t, role=r) for t, r in phrases],
    )  # fmt: skip


@dataclass(frozen=True)
class Example:
    check: str
    dataset: str  # fixture set key; "jtl_8m" = JTL profile scaled to 8M rows
    request: ChartRequest


R5_EXAMPLES: tuple[Example, ...] = (
    Example(
        "field_exists",
        "jtl",
        _req("cpu", "CPU usage over time", "line", ("cpu usage", Y), ("time", X)),
    ),
    Example(
        "type_fit", "junit", _req("names", "Line chart of test names", "line", ("test names", X))
    ),
    Example(
        "granularity",
        "daily",
        _req("hourly", "Hourly trend of orders", "line", ("orders", Y), grain="hourly"),
    ),
    Example(
        "cardinality",
        "junit",
        _req("pie", "Pie of failures by test", "pie", ("failures", M), ("test", SPLIT)),
    ),
    Example(
        "volume",
        "jtl_8m",
        _req("scatter", "Scatter of latency vs bytes", "scatter", ("latency", X), ("bytes", Y)),
    ),
    Example("derivation", "junit", _req("mttr", "MTTR by week", "line", ("mttr", Y), grain="week")),
    Example("renderer", "daily", _req("globe", "3D globe of traffic", "3d globe", ("traffic", M))),
    Example("ambiguity", "jtl", _req("perf", "Show performance", None, ("performance", None))),
)
