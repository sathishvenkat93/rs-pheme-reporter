"""Example objects and hypothesis strategies for the domain models."""

from __future__ import annotations

import copy
from typing import Any

from hypothesis import strategies as st

from app.models.dataset import ColumnSchema, ColumnType, schema_hash
from app.models.spec import WidgetType

HASH = schema_hash(
    [
        ColumnSchema(name="build", type=ColumnType.STRING),
        ColumnSchema(name="status", type=ColumnType.STRING),
    ]
)

# R2, adapted to the corrected plan (dataset_id added; version lives outside the body).
R2_SPEC: dict[str, Any] = {
    "dashboard_id": "dsh_7f3a",
    "title": "Regression run — Sprint 42",
    "datasets": [
        {
            "id": "ds_tests",
            "dataset_id": "6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a",
            "kind": "test_results",
            "schema_hash": HASH,
            "mode": "live",
            "version_id": None,
        }
    ],
    "filters": [{"id": "f_suite", "field": "suite", "control": "multiselect"}],
    "measures": [
        {
            "id": "pass_rate",
            "expr": "ratio(count_if(status == 'passed'), count())",
            "format": "percent",
        }
    ],
    "layout": {
        "columns": 12,
        "items": [
            {"widget": "w_pass_kpi", "x": 0, "y": 0, "w": 3, "h": 2},
            {"widget": "w_pass_trend", "x": 3, "y": 0, "w": 9, "h": 4},
        ],
    },
    "widgets": [
        {
            "id": "w_pass_kpi",
            "type": "kpi",
            "title": "Pass rate",
            "query": {"dataset": "ds_tests", "measures": ["pass_rate"]},
            "target": 0.95,
            "thresholds": [{"below": 0.90, "color": "critical"}],
        },
        {
            "id": "w_pass_trend",
            "type": "line",
            "title": "Pass rate by build",
            "query": {
                "dataset": "ds_tests",
                "dimensions": ["build"],
                "measures": ["pass_rate"],
                "sort": [{"field": "build", "dir": "asc"}],
            },
            "encoding": {"x": "build", "y": "pass_rate"},
            "annotations": [{"type": "rule", "y": 0.95, "label": "Target"}],
            "provenance": {"option_id": "O2", "rationale": "Trend across 14 builds"},
        },
    ],
}


def r2_spec() -> dict[str, Any]:
    return copy.deepcopy(R2_SPEC)


# ------------------------------------------------------------------- hypothesis strategies

ident = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
title = st.text(
    st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), min_size=1, max_size=30
)
widget_type = st.sampled_from([t.value for t in WidgetType])
field = st.sampled_from(["build", "suite", "case", "status", "duration_ms", "timestamp"])


@st.composite
def widget(draw: st.DrawFn, wid: str, measure_ids: list[str]) -> dict[str, Any]:
    w: dict[str, Any] = {
        "id": wid,
        "type": draw(widget_type),
        "title": draw(title),
        "query": {
            "dataset": "ds_tests",
            "dimensions": draw(st.lists(field, max_size=2, unique=True)),
            "measures": draw(
                st.lists(st.sampled_from(measure_ids), min_size=1, max_size=2, unique=True)
            ),
        },
    }
    if draw(st.booleans()):
        w["thresholds"] = [{"below": draw(st.floats(0, 1)), "color": "critical"}]
    if draw(st.booleans()):
        w["annotations"] = [{"type": "rule", "y": draw(st.floats(0, 1)), "label": draw(title)}]
    if draw(st.booleans()):
        w["encoding"] = {"x": draw(field), "y": draw(field)}
    return w


@st.composite
def specs(draw: st.DrawFn) -> dict[str, Any]:
    """Valid DashboardSpec JSON documents."""
    spec = r2_spec()
    n = draw(st.integers(0, 5))
    wids = draw(st.lists(ident.map(lambda s: "w_" + s), min_size=n, max_size=n, unique=True))
    measure_ids = ["pass_rate"]
    if draw(st.booleans()):
        spec["measures"].append({"id": "p95", "expr": "percentile(duration_ms, 95)"})
        measure_ids.append("p95")
    spec["widgets"] = [draw(widget(w, measure_ids)) for w in wids]
    spec["layout"]["items"] = [
        {"widget": w, "x": (i % 2) * 6, "y": (i // 2) * 4, "w": 6, "h": 4}
        for i, w in enumerate(wids)
    ]
    spec["title"] = draw(title)
    return spec
