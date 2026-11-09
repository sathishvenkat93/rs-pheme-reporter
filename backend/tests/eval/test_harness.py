"""The evaluation harness runs on the seed goldens and computes every R10 metric."""

from __future__ import annotations

from pathlib import Path

from eval.golden import Descriptor, load_goldens
from eval.run import agreement, evaluate, main, render, same, target_for


def test_harness_computes_every_metric() -> None:
    summary = evaluate(load_goldens(), skip_timing=True)
    m = summary["metrics"]
    assert m["parse_coverage"] == 1.0
    assert m["spec_validity"] == 1.0
    assert m["domain_accuracy"] == 1.0
    assert m["feasibility_precision"] is not None
    assert m["feasibility_recall"] is not None
    assert m["edit_success"] is not None
    assert 0 <= m["recommendation_overlap"] <= 1
    assert {r.name for r in summary["results"]} == {
        "junit_regression",
        "jmeter_load",
        "daily_metrics",
    }
    report = render(summary, live=False)
    assert "| Parse coverage | 100.0% |" in report
    assert "not by independent experts" in report


def test_recommendation_target_follows_expert_agreement() -> None:
    assert target_for("recommendation_overlap", {"expert_agreement": 0.56}) == (">=", 0.56 - 0.10)
    assert target_for("recommendation_overlap", {"expert_agreement": 0.95}) == (">=", 0.70)
    assert target_for("recommendation_overlap", {}) == (">=", 0.70)


def test_descriptor_agreement() -> None:
    a = [Descriptor(chart="kpi", measures=["pass_rate"]), Descriptor(chart="line", dims=["build"])]
    b = [Descriptor(chart=["kpi"], measures=["pass_rate"]), Descriptor(chart="bar", dims=["suite"])]
    assert same(a[0], b[0])
    assert agreement([a, b]) == 0.5
    assert agreement([a]) is None


def test_cli_writes_report_json_and_rating_sheet(tmp_path: Path) -> None:
    assert main(["--only", "jmeter_load", "--skip-timing", "--out", str(tmp_path)]) == 0
    files = sorted(p.suffix for p in tmp_path.iterdir())
    assert files == [".csv", ".json", ".md"]
    sheet = next(tmp_path.glob("*.csv")).read_text().splitlines()
    assert sheet[0].startswith("golden,edit,prompt")
    assert len(sheet) == 3  # header + two edit scenarios
