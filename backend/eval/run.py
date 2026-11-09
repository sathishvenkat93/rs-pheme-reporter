"""Evaluation runner: ``python -m eval.run`` (``make eval``). Writes eval/reports/<date>.md.

Measures every R10 metric on the golden sets. Offline by default (FakeLLM: curation uses
its deterministic fallback); ``PHEME_LIVE_LLM=1`` uses the real models for curation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from app.core.security import Redactor
from app.dashboards.builder import BuildError, build_spec
from app.db.tables import DatasetRow, DatasetVersionRow
from app.feasibility.checks import check
from app.ingestion.detect import HEAD_BYTES, detect
from app.ingestion.normalize import ParseContext
from app.ingestion.parsers import parse, write_parquet
from app.models.chart import ChartRequest, FieldPhrase, FieldRoleHint, OptionStatus, VisualOption
from app.models.dataset import DatasetProfile, schema_hash
from app.models.grammar import fields_used
from app.models.patch import PatchError, WidgetPatchOp, apply_ops
from app.models.plan import DraftPlan
from app.models.spec import DashboardSpec
from app.orchestrator.llm import make_llm
from app.orchestrator.tasks import curate
from app.profiling.profiler import profile_paths
from app.query.compiler import QueryError, compile_query
from app.query.executor import default_time_field, run_compiled
from app.recommend.context import context_for, violations
from app.recommend.engine import recommend
from eval.golden import Descriptor, Golden, load_goldens

REPORTS = Path(__file__).parent / "reports"
TARGETS = {
    "parse_coverage": (">=", 0.98),
    "recommendation_overlap": (">=", 0.70),
    "spec_validity": (">=", 0.99),
    "feasibility_precision": (">=", 0.95),
    "feasibility_recall": (">=", 0.90),
    "edit_success": (">=", 0.90),
    "time_to_first_proposal_s": ("<", 30.0),
    "dashboard_reopen_s": ("<", 2.0),
}


@dataclass
class GoldenResult:
    name: str
    rows_expected: int = 0
    rows_parsed: int = 0
    domain_ok: bool = False
    domain: str | None = None
    overlaps: dict[str, float] = field(default_factory=dict)
    agreement: float | None = None
    picks: list[str] = field(default_factory=list)
    spec_valid: bool = False
    spec_errors: list[str] = field(default_factory=list)
    verdicts: list[tuple[str, str, str]] = field(default_factory=list)  # id, expected, got
    edits: list[tuple[str, bool, str]] = field(default_factory=list)  # name, ok, note
    reopen_s: float | None = None


# ----------------------------------------------------------------------------- parse coverage


def expected_rows(path: Path) -> int:
    """Independent row count (not via Pheme's parsers)."""
    head = path.read_bytes()[:HEAD_BYTES].lstrip()
    if head.startswith(b"<"):
        tags = {"testcase", "httpSample", "sample"}
        return sum(
            1
            for _, el in etree.iterparse(str(path), events=("end",))
            if etree.QName(el).localname in tags
        )
    if path.suffix in {".ndjson", ".jsonl"}:
        return sum(1 for line in path.read_text().splitlines() if line.strip())
    if path.suffix == ".json":
        return len(json.loads(path.read_text()))
    with path.open(newline="") as fh:
        return max(0, sum(1 for _ in csv.reader(fh)) - 1)


def load(g: Golden, tmp: Path) -> tuple[list[Path], DatasetProfile, int, int]:
    parts, rows_expected, rows_parsed, kind = [], 0, 0, None
    for i, src in enumerate(g.paths()):
        with src.open("rb") as fh:
            d = detect(src.name, fh.read(HEAD_BYTES))
        parsed = parse(src, d, ParseContext(f"eval-{i}", src.name, None, i + 1, "UTC"))
        out = tmp / f"{i}-{src.stem}.parquet"
        _, n = write_parquet(parsed, out, d.format)
        parts.append(out)
        rows_parsed += n
        rows_expected += expected_rows(src)
        kind = parsed.kind
    assert kind is not None
    return parts, profile_paths(parts, kind, uuid.uuid4()), rows_expected, rows_parsed


# ----------------------------------------------------------------------------- recommendation quality


def matches(o: VisualOption, d: Descriptor) -> bool:
    if o.chart_type.value not in d.charts():
        return False
    dims = [x for x in o.query.dimensions]
    if o.chart_type.value == "histogram":
        dims = dims[:1]
    if d.dims and set(dims) != set(d.dims):
        return False
    if d.measures:
        names = set(o.query.measures) | {f for m in o.measures for f in fields_used(m.ast())}
        if not set(d.measures) <= names and not (set(d.measures) & set(o.query.measures)):
            return False
    return True


def overlap(picks: list[VisualOption], expert: list[Descriptor]) -> float:
    """Share of the expert's charts that one of our picks covers."""
    return sum(1 for d in expert if any(matches(o, d) for o in picks)) / len(expert)


def same(a: Descriptor, b: Descriptor) -> bool:
    return (
        bool(a.charts() & b.charts())
        and set(a.dims) == set(b.dims)
        and (not a.measures or not b.measures or bool(set(a.measures) & set(b.measures)))
    )


def agreement(experts: list[list[Descriptor]]) -> float | None:
    if len(experts) < 2:
        return None
    pairs = [(a, b) for i, a in enumerate(experts) for b in experts[i + 1 :]]
    return sum(sum(1 for d in a if any(same(d, e) for e in b)) / len(a) for a, b in pairs) / len(
        pairs
    )


# ----------------------------------------------------------------------------- spec validity


def validate_spec(spec: DashboardSpec, profile: DatasetProfile, parts: list[Path]) -> list[str]:
    errors = []
    ctx = context_for(profile)
    columns = {c.name: c.type for c in profile.columns}
    defs = {m.id: m for m in spec.measures}
    for w in spec.widgets:
        opt = VisualOption(id=w.id, title=w.title, chart_type=w.type, query=w.query,
                           measures=[defs[m] for m in w.query.measures], status=OptionStatus.OPTIONAL)  # fmt: skip
        if problems := violations(opt, ctx):
            errors.append(f"{w.id}: {problems}")
            continue
        try:
            compiled = compile_query(w.query, columns, defs, time_field=default_time_field(columns))
            rows, _ = run_compiled(parts, compiled, timeout_s=30, row_cap=10_000)
            if not rows:
                errors.append(f"{w.id}: no rows")
        except QueryError as exc:
            errors.append(f"{w.id}: {exc}")
    return errors


def build(g: Golden, picks: list[VisualOption], profile: DatasetProfile) -> DashboardSpec:
    cols = [c for c in columns_of_profile(profile)]
    ds = DatasetRow(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), name=g.name, kind=profile.kind.value
    )
    ver = DatasetVersionRow(id=uuid.uuid4(), dataset_id=ds.id, version=1, parts=[], columns=[],
                            schema_hash=schema_hash(cols), row_count=profile.row_count, upload_ids=[])  # fmt: skip
    plan = DraftPlan(id=uuid.uuid4(), session_id=uuid.uuid4(), version=1, options=picks)
    return build_spec(plan, ds, ver, dashboard_id=g.name, domain=profile.domain)


def columns_of_profile(profile: DatasetProfile) -> list[Any]:
    from app.models.dataset import ColumnSchema

    return [ColumnSchema(name=c.name, type=c.type) for c in profile.columns]


# ----------------------------------------------------------------------------- feasibility & edits


def to_request(r: Any) -> ChartRequest:
    return ChartRequest(
        id=r.id, raw_text=r.text, chart_type=r.chart_type, time_grain=r.time_grain,
        field_phrases=[FieldPhrase(text=t, role=FieldRoleHint(role) if role else None) for t, role in r.phrases],
    )  # fmt: skip


def resolve_placeholders(value: Any, spec: DashboardSpec) -> Any:
    def first(kind: str) -> str:
        return next(w.id for w in spec.widgets if w.type.value == kind)

    if isinstance(value, str):
        return re.sub(r"\$(\w+)", lambda m: first(m.group(1)), value)
    if isinstance(value, list):
        return [resolve_placeholders(v, spec) for v in value]
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, spec) for k, v in value.items()}
    return value


def run_edit(edit: Any, spec: DashboardSpec, profile: DatasetProfile) -> tuple[bool, str]:
    """Apply the scenario's ops; success = valid result whose changed paths match, or a refusal
    when the scenario expects none."""
    try:
        ops = [WidgetPatchOp.model_validate(resolve_placeholders(o, spec)) for o in edit.ops]
        expected = resolve_placeholders(edit.expect_changed, spec)
    except StopIteration:
        return False, "no widget of the referenced type"
    doc = spec.model_dump(mode="json", exclude_none=True)
    try:
        result = apply_ops(doc, ops)
        new = DashboardSpec.model_validate(result.doc)
        ctx = context_for(profile)
        defs = {m.id: m for m in new.measures}
        for w in new.widgets:
            opt = VisualOption(id=w.id, title=w.title, chart_type=w.type, query=w.query,
                               measures=[defs[m] for m in w.query.measures], status=OptionStatus.OPTIONAL)  # fmt: skip
            if problems := violations(opt, ctx):
                raise PatchError(f"{w.id}: {problems}")
    except (PatchError, ValueError) as exc:
        return (not expected), f"refused: {str(exc)[:80]}"
    if not expected:
        return False, "should have been refused"
    changed = {op.path for op in result.forward}
    by_id = {w.id: i for i, w in enumerate(spec.widgets)}
    want = {
        re.sub(r"/widgets/([^/]+)", lambda m: f"/widgets/{by_id.get(m.group(1), m.group(1))}", p)
        for p in expected
    }
    return want <= changed, f"changed {sorted(changed)}"


# ----------------------------------------------------------------------------- timings


def time_first_proposal(tmp: Path, target_mb: int) -> tuple[float, int]:
    """Parse + profile + recommend + curate on a synthetic CSV of about ``target_mb`` MB."""
    rng = random.Random(1)  # noqa: S311 - synthetic test data, not security
    path = tmp / "big.csv"
    with path.open("w") as fh:
        fh.write("timestamp,service,region,status,latency_ms,bytes\n")
        i = 0
        while fh.tell() < target_mb * 1024 * 1024:
            fh.write("\n".join(
                f"2026-10-{1 + (i + k) % 28:02d}T{(i + k) % 24:02d}:{k % 60:02d}:00Z,svc-{rng.randint(1, 20)},"
                f"{rng.choice(('emea', 'amer', 'apac'))},{rng.choice(('ok', 'ok', 'error'))},{rng.random() * 900:.1f},{rng.randint(100, 9000)}"
                for k in range(10_000)) + "\n")  # fmt: skip
            i += 10_000
    size = path.stat().st_size
    start = time.perf_counter()
    with path.open("rb") as fh:
        d = detect(path.name, fh.read(HEAD_BYTES))
    out = tmp / "big.parquet"
    write_parquet(parse(path, d, ParseContext("eval", path.name, None, 1, "UTC")), out, d.format)
    profile = profile_paths(
        [out], parse(path, d, ParseContext("eval", path.name, None, 1, "UTC")).kind, uuid.uuid4()
    )
    curate(make_llm(), recommend([profile]), [profile], redactor=Redactor(), samples=[])
    return time.perf_counter() - start, size


def time_reopen(spec: DashboardSpec, parts: list[Path], profile: DatasetProfile) -> float:
    """Second open of every widget, served from the Redis result cache (as the API does)."""
    from app.core.redis import get_redis
    from app.query.executor import QueryResult, query_hash

    columns = {c.name: c.type for c in profile.columns}
    defs = {m.id: m for m in spec.measures}
    redis = get_redis()
    compiled = [
        compile_query(w.query, columns, defs, time_field=default_time_field(columns))
        for w in spec.widgets
    ]
    for c in compiled:  # first open fills the cache
        rows, trunc = run_compiled(parts, c, timeout_s=30, row_cap=10_000)
        redis.set(f"pheme:eval:{query_hash(c)}", QueryResult(columns=c.columns, rows=rows, query_hash=query_hash(c),
                                                             truncated=trunc).model_dump_json(), ex=600)  # fmt: skip
    start = time.perf_counter()
    for c in compiled:
        QueryResult.model_validate_json(redis.get(f"pheme:eval:{query_hash(c)}"))  # type: ignore[arg-type]
    return time.perf_counter() - start


# ----------------------------------------------------------------------------- main


def evaluate(
    goldens: list[Golden], *, timing_mb: int = 50, skip_timing: bool = False
) -> dict[str, Any]:
    results: list[GoldenResult] = []
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for g in goldens:
            r = GoldenResult(name=g.name)
            folder = tmp / g.name
            folder.mkdir()
            parts, profile, r.rows_expected, r.rows_parsed = load(g, folder)
            r.domain, r.domain_ok = profile.domain, profile.domain == g.domain
            curated = curate(
                make_llm(), recommend([profile]), [profile], redactor=Redactor(), samples=[]
            )
            picks = [o for o in curated if o.status == OptionStatus.RECOMMENDED][:8]
            r.picks = [o.id for o in picks]
            r.overlaps = {e.name: overlap(picks, e.top8) for e in g.experts}
            r.agreement = agreement([e.top8 for e in g.experts])
            try:
                spec = build(g, picks, profile)
                r.spec_errors = validate_spec(spec, profile, parts)
                r.spec_valid = not r.spec_errors
            except BuildError as exc:
                r.spec_errors = [str(exc)]
                spec = None
            for req in g.requests:
                got = check(to_request(req), profile).verdict.kind.value
                r.verdicts.append((req.id, req.expected, got))
            if spec is not None:
                for e in g.edits:
                    ok, note = run_edit(e, spec, profile)
                    r.edits.append((e.name, ok, note))
                if not skip_timing:
                    r.reopen_s = time_reopen(spec, parts, profile)
            results.append(r)
        first_proposal = None if skip_timing else time_first_proposal(tmp, timing_mb)
    return summarize(results, first_proposal)


def summarize(
    results: list[GoldenResult], first_proposal: tuple[float, int] | None
) -> dict[str, Any]:
    verdicts = [v for r in results for v in r.verdicts]
    positive = {"not_feasible"}
    tp = sum(1 for _, exp, got in verdicts if exp in positive and got in positive)
    fp = sum(1 for _, exp, got in verdicts if exp not in positive and got in positive)
    fn = sum(1 for _, exp, got in verdicts if exp in positive and got not in positive)
    overlaps = [v for r in results for v in r.overlaps.values()]
    agreements = [r.agreement for r in results if r.agreement is not None]
    edits = [ok for r in results for _, ok, _ in r.edits]
    reopen = [r.reopen_s for r in results if r.reopen_s is not None]
    metrics: dict[str, float | None] = {
        "parse_coverage": sum(r.rows_parsed for r in results)
        / max(1, sum(r.rows_expected for r in results)),
        "recommendation_overlap": sum(overlaps) / len(overlaps) if overlaps else None,
        "expert_agreement": sum(agreements) / len(agreements) if agreements else None,
        "spec_validity": sum(r.spec_valid for r in results) / len(results) if results else None,
        "feasibility_precision": tp / (tp + fp) if tp + fp else None,
        "feasibility_recall": tp / (tp + fn) if tp + fn else None,
        "verdict_accuracy": sum(1 for _, e, g in verdicts if e == g) / len(verdicts)
        if verdicts
        else None,
        "edit_success": sum(edits) / len(edits) if edits else None,
        "time_to_first_proposal_s": first_proposal[0] if first_proposal else None,
        "dashboard_reopen_s": max(reopen) if reopen else None,
        "domain_accuracy": sum(r.domain_ok for r in results) / len(results) if results else None,
    }
    return {
        "metrics": metrics,
        "results": results,
        "first_proposal_bytes": first_proposal[1] if first_proposal else None,
    }


def target_for(name: str, metrics: dict[str, float | None]) -> tuple[str, float] | None:
    """R10 targets. Recommendation overlap: >= 70%, or within 10 points of expert-to-expert
    agreement when the experts agree less than that (the corrected plan's rule)."""
    if name not in TARGETS:
        return None
    op, target = TARGETS[name]
    agreement_ = metrics.get("expert_agreement")
    if name == "recommendation_overlap" and agreement_ is not None:
        target = min(target, max(0.0, agreement_ - 0.10))
    return op, target


def passes(name: str, value: float | None, metrics: dict[str, float | None]) -> str:
    t = target_for(name, metrics)
    if t is None or value is None:
        return "—"
    op, target = t
    ok = value >= target if op == ">=" else value < target
    return "✅" if ok else "❌"


def render(summary: dict[str, Any], live: bool) -> str:
    m = summary["metrics"]

    def fmt(v: float | None, pct: bool = True) -> str:
        return "n/a" if v is None else (f"{v:.1%}" if pct else f"{v:.2f} s")

    rows = [
        ("Parse coverage", "parse_coverage", True), ("Recommendation overlap (curated top 8 vs experts)", "recommendation_overlap", True),
        ("Expert-to-expert agreement (baseline)", "expert_agreement", True), ("Spec validity", "spec_validity", True),
        ("Feasibility precision (can't-build)", "feasibility_precision", True), ("Feasibility recall (can't-build)", "feasibility_recall", True),
        ("Verdict accuracy (all labels)", "verdict_accuracy", True), ("Edit success (automated)", "edit_success", True),
        ("Time to first proposal", "time_to_first_proposal_s", False), ("Dashboard reopen from cache", "dashboard_reopen_s", False),
        ("Domain accuracy", "domain_accuracy", True),
    ]  # fmt: skip
    target_txt = {}
    for k in TARGETS:
        op, v = target_for(k, m) or TARGETS[k]
        target_txt[k] = (
            f"{op} {v:.0%}"
            if k not in ("time_to_first_proposal_s", "dashboard_reopen_s")
            else f"{op} {v:g} s"
        )
    if (
        m.get("expert_agreement") is not None
        and target_for("recommendation_overlap", m) != TARGETS["recommendation_overlap"]
    ):
        target_txt["recommendation_overlap"] += " (agreement minus 10 pts)"
    out = [
        f"# Pheme evaluation — {dt.date.today()}", "",
        f"LLM: {'live models' if live else 'FakeLLM (curation = deterministic fallback)'}. "
        f"Goldens: {', '.join(r.name for r in summary['results'])}.", "",
        "| Metric | Value | Target (R10) | |", "| --- | --- | --- | --- |",
    ]  # fmt: skip
    for label, key, pct in rows:
        out.append(
            f"| {label} | {fmt(m[key], pct)} | {target_txt.get(key, '—')} | {passes(key, m[key], m)} |"
        )
    if summary["first_proposal_bytes"]:
        out.append(f"\nTime to first proposal measured on a {summary['first_proposal_bytes'] / 2**20:.0f} MB synthetic CSV "
                   "(parse, profile, recommend, curate; excludes network upload).")  # fmt: skip
    for r in summary["results"]:
        out += ["", f"## {r.name}", "",
                f"- rows parsed {r.rows_parsed} / {r.rows_expected}; domain {r.domain} ({'ok' if r.domain_ok else 'WRONG'})",
                f"- curated top 8: {', '.join(r.picks)}",
                f"- overlap per expert: {', '.join(f'{k} {v:.0%}' for k, v in r.overlaps.items()) or 'n/a'}; "
                f"agreement {'n/a' if r.agreement is None else f'{r.agreement:.0%}'}",
                f"- spec valid: {r.spec_valid}{'' if r.spec_valid else ' — ' + '; '.join(r.spec_errors)[:300]}"]  # fmt: skip
        bad = [f"{i} (expected {e}, got {g})" for i, e, g in r.verdicts if e != g]
        out.append(
            f"- verdicts: {len(r.verdicts) - len(bad)}/{len(r.verdicts)} as labelled"
            + (f"; mismatches: {', '.join(bad)}" if bad else "")
        )
        out.append(
            "- edits: "
            + "; ".join(f"{n}: {'ok' if ok else 'FAIL'} ({note})" for n, ok, note in r.edits)
        )
        if r.reopen_s is not None:
            out.append(f"- reopen (all widgets from cache): {r.reopen_s * 1000:.0f} ms")
    out += ["", "Expert lists in the seed goldens were written by the build agent, not by independent experts; "
            "recommendation overlap and agreement are indicative only until pilot labels replace them.",
            "Edit success here is automated (ops applied and checked). The human-rated R10 metric uses the rating "
            "sheet written next to this report."]  # fmt: skip
    return "\n".join(out) + "\n"


def write_rating_sheet(summary: dict[str, Any], goldens: list[Golden], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "golden",
                "edit",
                "prompt",
                "automated_result",
                "rater",
                "applied_correctly_first_try (y/n)",
                "notes",
            ]
        )
        by_name = {g.name: g for g in goldens}
        for r in summary["results"]:
            for (name, ok, note), e in zip(r.edits, by_name[r.name].edits, strict=False):
                w.writerow(
                    [r.name, name, e.prompt, f"{'ok' if ok else 'fail'}: {note}", "", "", ""]
                )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the Pheme evaluation harness.")
    ap.add_argument("--only", nargs="*", help="golden names to run")
    ap.add_argument("--timing-mb", type=int, default=50)
    ap.add_argument("--skip-timing", action="store_true")
    ap.add_argument("--out", type=Path, default=REPORTS)
    args = ap.parse_args(argv)
    from app.core.config import get_settings

    goldens = load_goldens(only=args.only)
    summary = evaluate(goldens, timing_mb=args.timing_mb, skip_timing=args.skip_timing)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    report = render(summary, live=get_settings().live_llm)
    (args.out / f"{stamp}.md").write_text(report)
    (args.out / f"{stamp}.json").write_text(json.dumps(summary["metrics"], indent=2))
    write_rating_sheet(summary, goldens, args.out / f"{stamp}-edit-ratings.csv")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
