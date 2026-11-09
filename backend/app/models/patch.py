"""Widget-id patch ops over a spec's JSON form.

Clients and the agent address collections by id (``/widgets/w_pass_kpi/title``,
``/layout/items/w_pass_kpi/w``), never by index. Each op is translated into an RFC 6902
op against the document as it stands at that moment, applied, and recorded together with
its exact inverse. Inverse ops are index-addressed, so a removed item returns to its
original position and ``apply_rfc(result.doc, result.inverse) == original``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Literal, Self, cast

from pydantic import Field, JsonValue, model_validator

from app.models.common import StrictModel

# Collections addressed by id, and the key that holds the id.
ID_COLLECTIONS: dict[tuple[str, ...], str] = {
    ("widgets",): "id",
    ("measures",): "id",
    ("filters",): "id",
    ("datasets",): "id",
    ("layout", "items"): "widget",
}

Json = JsonValue
Doc = dict[str, Json]


class PatchError(ValueError):
    """A patch op that can't be applied. The message is meant for users."""


class WidgetPatchOp(StrictModel):
    op: Literal["add", "remove", "replace"]
    path: str = Field(pattern=r"^(/[^/]*)+$")
    value: JsonValue = None

    @model_validator(mode="after")
    def _value(self) -> Self:
        if self.op == "remove" and self.value is not None:
            raise ValueError("remove takes no value")
        return self


@dataclass(frozen=True, slots=True)
class RfcOp:
    op: Literal["add", "remove", "replace"]
    path: str
    value: Json = None

    def to_json(self) -> dict[str, Json]:
        out: dict[str, Json] = {"op": self.op, "path": self.path}
        if self.op != "remove":
            out["value"] = self.value
        return out


@dataclass(frozen=True, slots=True)
class PatchResult:
    doc: Doc
    forward: list[RfcOp]
    inverse: list[RfcOp]


def _unescape(seg: str) -> str:
    return seg.replace("~1", "/").replace("~0", "~")


def _escape(seg: str) -> str:
    return seg.replace("~", "~0").replace("/", "~1")


def _split(path: str) -> list[str]:
    if not path.startswith("/"):
        raise PatchError(f"Path {path!r} must start with '/'.")
    return [_unescape(s) for s in path[1:].split("/")]


def _join(segs: list[str]) -> str:
    return "/" + "/".join(_escape(s) for s in segs)


def to_rfc_path(doc: Doc, path: str, *, adding: bool = False) -> tuple[str, str | None]:
    """Translate a widget-id path to an RFC 6902 index path.

    Returns ``(rfc_path, appended_key)``. ``appended_key`` is the id key (e.g. ``"id"``) when
    the final segment is a new id being added to an id collection (the item goes at the
    end), otherwise None.
    """
    segs = _split(path)
    out: list[str] = []
    node: Json = doc
    walked: list[str] = []
    for i, seg in enumerate(segs):
        last = i == len(segs) - 1
        if isinstance(node, list):
            key = ID_COLLECTIONS.get(tuple(walked))
            if key is not None:
                idx = next(
                    (j for j, it in enumerate(node) if isinstance(it, dict) and it.get(key) == seg),
                    None,
                )
                if idx is None:
                    if last and adding:
                        out.append(str(len(node)))
                        return _join(out), key
                    raise PatchError(f"No item with {key} {seg!r} at {_join(walked)}.")
                out.append(str(idx))
                node = node[idx]
                walked = []
                continue
            if not seg.isdigit() and not (last and adding and seg == "-"):
                raise PatchError(f"{_join(walked)} is a list; expected an index, got {seg!r}.")
            out.append(seg)
            if not last:
                if int(seg) >= len(node):
                    raise PatchError(f"Index {seg} is out of range at {_join(walked)}.")
                node = node[int(seg)]
                walked = []
            continue
        if isinstance(node, dict):
            out.append(seg)
            walked.append(seg)
            if not last:
                if seg not in node:
                    raise PatchError(f"{_join(out)} does not exist.")
                node = node[seg]
            continue
        raise PatchError(f"Can't descend into a value at {_join(out)}.")
    return _join(out), None


def _parent(doc: Doc, rfc_path: str) -> tuple[Json, str]:
    segs = _split(rfc_path)
    node: Json = doc
    for seg in segs[:-1]:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            raise PatchError(f"Path {rfc_path!r} does not exist.")
    return node, segs[-1]


def _apply_one(doc: Doc, op: RfcOp) -> RfcOp:
    """Apply one RFC op in place and return its inverse."""
    parent, key = _parent(doc, op.path)
    if isinstance(parent, dict):
        if op.op == "add":
            if key in parent:
                old = parent[key]
                parent[key] = copy.deepcopy(op.value)
                return RfcOp("replace", op.path, old)
            parent[key] = copy.deepcopy(op.value)
            return RfcOp("remove", op.path)
        if key not in parent:
            raise PatchError(f"{op.path} does not exist.")
        old = parent[key]
        if op.op == "remove":
            del parent[key]
            return RfcOp("add", op.path, old)
        parent[key] = copy.deepcopy(op.value)
        return RfcOp("replace", op.path, old)
    if isinstance(parent, list):
        if op.op == "add":
            idx = len(parent) if key == "-" else int(key)
            if idx > len(parent):
                raise PatchError(f"Index {idx} is out of range for {op.path}.")
            parent.insert(idx, copy.deepcopy(op.value))
            return RfcOp("remove", _join([*_split(op.path)[:-1], str(idx)]))
        if not key.isdigit() or int(key) >= len(parent):
            raise PatchError(f"{op.path} does not exist.")
        idx = int(key)
        old = parent[idx]
        if op.op == "remove":
            del parent[idx]
            return RfcOp("add", op.path, old)
        parent[idx] = copy.deepcopy(op.value)
        return RfcOp("replace", op.path, old)
    raise PatchError(f"Can't apply {op.op} at {op.path}.")


def apply_rfc(doc: Doc, ops: list[RfcOp]) -> Doc:
    """Apply RFC 6902 ops to a copy of ``doc``."""
    out = copy.deepcopy(doc)
    for op in ops:
        _apply_one(out, op)
    return out


def apply_ops(doc: Doc, ops: list[WidgetPatchOp]) -> PatchResult:
    """Apply widget-id ops to a copy of ``doc``; returns the result, forward and inverse ops.

    The result is not schema-validated here; callers validate the final document.
    """
    out = copy.deepcopy(doc)
    forward: list[RfcOp] = []
    inverse: list[RfcOp] = []
    for op in ops:
        rfc_path, appended_key = to_rfc_path(out, op.path, adding=op.op == "add")
        if appended_key is not None:
            new_id = _split(op.path)[-1]
            if not isinstance(op.value, dict) or op.value.get(appended_key) != new_id:
                raise PatchError(f"The new item's {appended_key} must match the path ({new_id!r}).")
        rfc = RfcOp(op.op, rfc_path, op.value)
        inverse.append(_apply_one(out, rfc))
        forward.append(rfc)
    inverse.reverse()
    return PatchResult(doc=out, forward=forward, inverse=inverse)


def ops_from_json(raw: list[dict[str, Json]]) -> list[WidgetPatchOp]:
    return [WidgetPatchOp.model_validate(cast(dict[str, object], r)) for r in raw]
