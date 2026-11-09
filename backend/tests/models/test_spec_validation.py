from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.models.chart import ChartRequest
from app.models.plan import Decision, DecisionChoice
from app.models.query import EqFilter, SemanticQuery
from app.models.spec import DashboardSpec, LayoutItem, WidgetType

from .factories import r2_spec


def _invalid(doc: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        DashboardSpec.model_validate(doc)


def test_rejects_unknown_widget_type() -> None:
    doc = r2_spec()
    doc["widgets"][0]["type"] = "globe_3d"
    _invalid(doc, "type")


def test_rejects_duplicate_widget_id() -> None:
    doc = r2_spec()
    doc["widgets"][1]["id"] = "w_pass_kpi"
    _invalid(doc, "duplicate widget id")


def test_rejects_layout_item_for_missing_widget() -> None:
    doc = r2_spec()
    doc["layout"]["items"].append({"widget": "w_ghost", "x": 0, "y": 6, "w": 6, "h": 4})
    _invalid(doc, "layout references missing widget")


@pytest.mark.parametrize(
    "expr",
    [
        "count_if(status = 'passed') / count()",  # the old R2 form: SQL-like, outside the grammar
        "SELECT count(*) FROM tests",
        "median(duration_ms)",
    ],
)
def test_rejects_measure_expr_outside_grammar(expr: str) -> None:
    doc = r2_spec()
    doc["measures"][0]["expr"] = expr
    _invalid(doc, "measures")


def test_rejects_other_integrity_problems() -> None:
    doc = r2_spec()
    doc["widgets"][0]["query"]["measures"] = ["nope"]
    _invalid(doc, "undefined measure")

    doc = r2_spec()
    doc["widgets"][0]["query"]["dataset"] = "ds_other"
    _invalid(doc, "unknown dataset")

    doc = r2_spec()
    doc["layout"]["items"].pop()
    _invalid(doc, "without a layout item")

    doc = r2_spec()
    doc["datasets"][0]["mode"] = "snapshot"
    _invalid(doc, "snapshot mode needs version_id")

    doc = r2_spec()
    doc["widgets"][0]["surprise"] = True
    _invalid(doc, "Extra inputs are not permitted")


def test_layout_must_fit_grid() -> None:
    with pytest.raises(ValidationError, match="overflows"):
        LayoutItem(widget="w", x=9, y=0, w=6, h=2)


def test_scalars_keep_their_type() -> None:
    assert EqFilter(field="build", value="101").value == "101"
    assert EqFilter(field="build", value=101).value == 101
    assert EqFilter(field="flag", value=True).value is True


def test_query_needs_something_to_compute() -> None:
    with pytest.raises(ValidationError, match="at least one dimension or measure"):
        SemanticQuery(dataset="ds")


def test_chart_request_maps_user_words_to_widget_types() -> None:
    def wt(chart_type: str | None) -> WidgetType | None:
        return ChartRequest(id="r", raw_text="x", chart_type=chart_type).widget_type()

    assert wt("pie") is WidgetType.DONUT
    assert wt("Stacked Bar") is WidgetType.STACKED_BAR
    assert wt("histogram") is WidgetType.HISTOGRAM
    assert wt("3D globe") is None  # representable, but unsupported (M7: renderer check)
    assert wt(None) is None


def test_decision_choice_must_be_offered_and_complete() -> None:
    with pytest.raises(ValidationError, match="set together"):
        Decision(
            id="D", item="O1", reason="r", options=[DecisionChoice.DROP], choice=DecisionChoice.DROP
        )
    with pytest.raises(ValidationError, match="not one of the offered"):
        Decision.model_validate(
            {
                "id": "D",
                "item": "O1",
                "reason": "r",
                "options": ["drop"],
                "choice": "keep_as_asked",
                "decided_by": "6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a",
                "decided_at": "2026-11-09T10:00:00Z",
            }
        )
