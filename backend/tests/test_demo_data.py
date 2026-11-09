"""make demo-data prints what the Phase 2 exit criteria ask for."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_demo_data_output() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo_data.py")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    sections = {s.split("\n", 1)[0]: s for s in out.split("\n## ")}
    junit = next(v for k, v in sections.items() if k.startswith("junit"))
    jtl = next(v for k, v in sections.items() if k.startswith("jtl  "))
    for marker in (
        "kpi_pass_rate",
        "sig_pass_rate_by_build",
        "sig_failure_pareto",
        "sig_duration_by_suite",
    ):
        assert marker in junit
    for marker in (
        "sig_p95_over_time",
        "sig_latency_histogram",
        "sig_throughput_over_time",
        "sig_errors_by_label",
    ):
        assert marker in jtl
    assert "domain=test_execution" in junit
    assert "domain=performance" in jtl
    assert "domain=generic" in next(v for k, v in sections.items() if k.startswith("daily"))
    expected = {
        "field_exists": "not_feasible", "type_fit": "not_feasible", "granularity": "adjusted",
        "cardinality": "adjusted", "volume": "adjusted", "derivation": "not_feasible",
        "renderer": "not_feasible", "ambiguity": "needs_clarification",
    }  # fmt: skip
    for check_name, verdict in expected.items():
        line = next(ln for ln in out.splitlines() if ln.startswith(f"- [{check_name}]"))
        assert line.endswith(f"→ {verdict}"), line
    assert "an hourly trend" in out
