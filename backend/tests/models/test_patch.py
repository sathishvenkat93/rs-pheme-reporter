from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.patch import PatchError, RfcOp, WidgetPatchOp, apply_ops, apply_rfc, to_rfc_path
from app.models.spec import DashboardSpec

from .factories import r2_spec, specs, title, widget


def op(o: str, path: str, value: Any = None) -> WidgetPatchOp:
    return WidgetPatchOp(op=o, path=path, value=value)


def test_widget_id_paths_translate_to_index_paths() -> None:
    doc = r2_spec()
    assert to_rfc_path(doc, "/widgets/w_pass_trend/type") == ("/widgets/1/type", None)
    assert to_rfc_path(doc, "/layout/items/w_pass_kpi/w") == ("/layout/items/0/w", None)
    assert to_rfc_path(doc, "/widgets/w_pass_kpi/thresholds/0/below") == (
        "/widgets/0/thresholds/0/below",
        None,
    )
    assert to_rfc_path(doc, "/widgets/w_new", adding=True) == ("/widgets/2", "id")
    with pytest.raises(PatchError, match="No item with id 'w_missing'"):
        to_rfc_path(doc, "/widgets/w_missing/title")


def test_change_type_then_undo() -> None:
    doc = r2_spec()
    result = apply_ops(doc, [op("replace", "/widgets/w_pass_trend/type", "bar")])
    assert result.doc["widgets"][1]["type"] == "bar"  # type: ignore[index,call-overload]
    assert result.forward == [RfcOp("replace", "/widgets/1/type", "bar")]
    assert apply_rfc(result.doc, result.inverse) == doc
    DashboardSpec.model_validate(result.doc)


def test_remove_widget_with_layout_restores_original_position() -> None:
    doc = r2_spec()
    result = apply_ops(
        doc, [op("remove", "/widgets/w_pass_kpi"), op("remove", "/layout/items/w_pass_kpi")]
    )
    DashboardSpec.model_validate(result.doc)
    restored = apply_rfc(result.doc, result.inverse)
    assert json.dumps(restored) == json.dumps(doc)  # byte for byte, order included


def test_add_widget_checks_id_matches_path() -> None:
    doc = r2_spec()
    new = copy.deepcopy(doc["widgets"][0]) | {"id": "w_other"}
    with pytest.raises(PatchError, match="must match the path"):
        apply_ops(doc, [op("add", "/widgets/w_new", new)])


def test_input_document_is_not_mutated() -> None:
    doc = r2_spec()
    before = copy.deepcopy(doc)
    apply_ops(doc, [op("replace", "/title", "New title")])
    assert doc == before


@st.composite
def spec_and_ops(draw: st.DrawFn) -> tuple[dict[str, Any], list[WidgetPatchOp]]:
    """A valid spec plus a sequence of ops, each valid against the state it applies to."""
    doc = draw(specs())
    state = copy.deepcopy(doc)
    ops: list[WidgetPatchOp] = []
    for _ in range(draw(st.integers(1, 6))):
        wids = [w["id"] for w in state["widgets"]]
        choices = ["title", "add"]
        if wids:
            choices += ["rename", "type", "resize", "remove", "threshold"]
        kind = draw(st.sampled_from(choices))
        if kind == "title":
            o = op("replace", "/title", draw(title))
        elif kind == "add":
            wid = "w_new" + str(len(ops))
            if wid in wids:
                continue
            o = op("add", f"/widgets/{wid}", draw(widget(wid, ["pass_rate"])))
        elif kind == "rename":
            o = op("replace", f"/widgets/{draw(st.sampled_from(wids))}/title", draw(title))
        elif kind == "type":
            o = op("replace", f"/widgets/{draw(st.sampled_from(wids))}/type", "bar")
        elif kind == "resize":
            wid = draw(st.sampled_from(wids))
            if wid not in {i["widget"] for i in state["layout"]["items"]}:
                continue
            o = op("replace", f"/layout/items/{wid}/w", draw(st.integers(1, 6)))
        elif kind == "remove":
            o = op("remove", f"/widgets/{draw(st.sampled_from(wids))}")
        else:
            o = op("add", f"/widgets/{draw(st.sampled_from(wids))}/target", draw(st.floats(0, 1)))
        state = apply_ops(state, [o]).doc
        ops.append(o)
    return doc, ops


@settings(max_examples=200)
@given(spec_and_ops())
def test_applying_a_patch_then_its_inverse_returns_the_original(
    case: tuple[dict[str, Any], list[WidgetPatchOp]],
) -> None:
    doc, ops = case
    result = apply_ops(doc, ops)
    restored = apply_rfc(result.doc, result.inverse)
    assert restored == doc
    assert json.dumps(restored) == json.dumps(doc)
    DashboardSpec.model_validate(restored)
