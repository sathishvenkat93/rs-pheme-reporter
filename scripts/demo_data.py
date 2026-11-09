"""make demo-data: profile, domain, top-10 candidates and R5 verdicts for every fixture.

Runs fully offline from backend/tests/fixtures; no database, object store or LLM.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.feasibility.checks import check  # noqa: E402
from app.feasibility.examples import R5_EXAMPLES  # noqa: E402
from app.ingestion.detect import HEAD_BYTES, detect  # noqa: E402
from app.ingestion.normalize import ParseContext  # noqa: E402
from app.ingestion.parsers import parse, write_parquet  # noqa: E402
from app.models.dataset import DatasetProfile  # noqa: E402
from app.profiling.profiler import profile_paths  # noqa: E402
from app.recommend.engine import recommend  # noqa: E402

FIXTURES = BACKEND / "tests" / "fixtures"
SETS = {
    "junit": [f"junit-build-{b}.xml" for b in range(101, 106)],
    "jtl": ["jmeter-load.jtl"],
    "jtl_xml": ["jmeter-small-xml.jtl"],
    "daily": ["daily-metrics.csv"],
    "deployments": ["deployments.json"],
    "events": ["events.ndjson"],
    "inventory": ["inventory.xlsx"],
}


def load(names: list[str], tmp: Path) -> DatasetProfile:
    paths = []
    kind = None
    for i, name in enumerate(names):
        src = FIXTURES / name
        with src.open("rb") as fh:
            d = detect(name, fh.read(HEAD_BYTES))
        parsed = parse(src, d, ParseContext(f"run-{i + 1}", name, None, i + 1, "UTC"))
        out = tmp / f"{Path(name).stem}.parquet"
        write_parquet(parsed, out, d.format)
        paths.append(out)
        kind = parsed.kind
    assert kind is not None
    return profile_paths(paths, kind, uuid.UUID(int=0))


def summary(key: str, p: DatasetProfile) -> list[str]:
    lines = [
        f"## {key}  ({', '.join(SETS[key]) if len(SETS[key]) < 3 else f'{len(SETS[key])} files'})",
        f"rows={p.row_count}  kind={p.kind.value}  domain={p.domain} ({p.domain_confidence})  grain: {p.grain}",
        "columns: " + ", ".join(f"{c.name}:{c.role.value}({c.distinct})" for c in p.columns),
    ]
    if p.candidate_keys:
        lines.append(f"keys: {p.candidate_keys}")
    if p.hierarchies:
        lines.append(f"hierarchies: {p.hierarchies}")
    strong = [t for t in p.trends if t.strength >= 0.2]
    if strong:
        lines.append("trends: " + ", ".join(f"{t.measure} {'+' if t.direction > 0 else '-'}{t.strength}" for t in strong))
    return lines


def top10(p: DatasetProfile) -> list[str]:
    lines = ["top 10 candidates:", "   #  total  chart        id                                   fit  int  ins  per  pri  red  clu"]
    for i, o in enumerate(recommend([p])[:10], 1):
        s = o.score
        assert s is not None
        lines.append(
            f"  {i:2}  {s.total:5.3f}  {o.chart_type.value:11}  {o.id[:36]:36} "
            f"{s.data_fit:4.2f} {s.intent:4.2f} {s.insight:4.2f} {s.perception:4.2f} "
            f"{s.domain_prior:4.2f} {s.redundancy:4.2f} {s.clutter:4.2f}   {o.title}"
        )
    return lines


def verdicts(profiles: dict[str, DatasetProfile]) -> list[str]:
    lines = ["## R5 feasibility examples"]
    for ex in R5_EXAMPLES:
        result = check(ex.request, profiles[ex.dataset])
        v = result.verdict
        lines.append(f'- [{ex.check}] "{ex.request.raw_text}" on {ex.dataset} → {v.kind.value}')
        for r in v.reasons:
            lines.append(f"    reason: {r}")
        for alt in v.alternatives:
            lines.append(f"    alternative: {alt.raw_text} ({alt.chart_type})")
        if v.applied_change:
            lines.append(f"    change: {v.applied_change.before} → {v.applied_change.after}")
        if v.candidates:
            lines.append(f"    ask: {', '.join(v.candidates)}")
    return lines


def main() -> int:
    out: list[str] = ["# Pheme demo data (deterministic, no LLM)", ""]
    profiles: dict[str, DatasetProfile] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for key, names in SETS.items():
            d = Path(tmp) / key
            d.mkdir()
            p = load(names, d)
            profiles[key] = p
            out += [*summary(key, p), *top10(p), ""]
    profiles["jtl_8m"] = profiles["jtl"].model_copy(update={"row_count": 8_000_000})
    out += verdicts(profiles)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
