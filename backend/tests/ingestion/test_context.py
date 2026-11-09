from __future__ import annotations

from app.ingestion.context import extract


def test_extracts_key_values_and_thresholds() -> None:
    found = extract("p95 must be < 800 ms, env=staging")
    assert found.facts == {"env": "staging"}
    assert [t.model_dump() for t in found.thresholds] == [
        {"metric": "p95", "op": "<", "value": 800.0, "unit": "ms"}
    ]


def test_threshold_phrasings() -> None:
    found = extract(
        "Error rate should stay below 1%; pass rate at least 95 %. Throughput over 200 rps"
    )
    assert [(t.metric, t.op, t.value, t.unit) for t in found.thresholds] == [
        ("error_rate", "<", 1.0, "%"),
        ("pass_rate", ">=", 95.0, "%"),
        ("throughput", ">", 200.0, "rps"),
    ]


def test_no_facts_in_plain_text() -> None:
    found = extract("Nightly regression from the payments team")
    assert found.facts == {}
    assert found.thresholds == []
