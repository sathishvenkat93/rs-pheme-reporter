"""The compact profile the LLM sees (design rule 1: profiles, not data).

Short keys, rounded numbers, truncated top-k and only the strongest relationships keep a
50-column profile within ``BUDGET_BYTES``. If it is still too big, detail is dropped in
stages (top-k, then numeric stats) until it fits.
"""

from __future__ import annotations

import json
import math
from typing import Any

from app.models.dataset import DatasetProfile

BUDGET_BYTES = 5 * 1024


def _r(x: float | None) -> float | int | None:
    if x is None or not math.isfinite(x):
        return None
    if x == 0:
        return 0
    v = round(x, max(0, 2 - math.floor(math.log10(abs(x)))))
    return int(v) if float(v).is_integer() else v


def _column(c: Any, level: int) -> dict[str, Any]:
    out: dict[str, Any] = {"n": c.name[:40], "r": c.role.value, "d": c.distinct}
    if c.null_pct:
        out["nul"] = _r(c.null_pct)
    if c.numeric and level < 2:
        nums = {
            "lo": c.numeric.min,
            "hi": c.numeric.max,
            "p50": c.numeric.p50,
            "p95": c.numeric.p95,
        }
        out |= {k: _r(v) for k, v in nums.items() if v is not None}
    if c.top_k and level < 1:
        out["top"] = [str(t.value)[:16] for t in c.top_k[:3]]
    if c.time and level < 2:
        out["gap_s"] = _r(c.time.median_gap_seconds)
    return out


def llm_view(profile: DatasetProfile) -> dict[str, Any]:
    """A JSON-ready summary of ``profile`` that fits in BUDGET_BYTES."""
    view: dict[str, Any] = {}
    for level in range(3):
        view = {
            "kind": profile.kind.value,
            "rows": profile.row_count,
            "domain": profile.domain,
            "grain": profile.grain,
            "cols": [_column(c, level) for c in profile.columns],
        }
        if profile.hierarchies:
            view["hier"] = profile.hierarchies[:3]
        if profile.correlations and level < 2:
            strongest = sorted(profile.correlations, key=lambda c: -abs(c.r))[:5]
            view["corr"] = [[c.a, c.b, _r(c.r)] for c in strongest]
        if profile.trends:
            strongest_t = sorted(profile.trends, key=lambda t: -t.strength)[:5]
            view["trend"] = [
                [t.measure, _r(t.strength * t.direction)] for t in strongest_t if t.strength >= 0.2
            ]
        if size(view) <= BUDGET_BYTES:
            return view
    return view


def size(view: dict[str, Any]) -> int:
    return len(json.dumps(view, separators=(",", ":")).encode())
