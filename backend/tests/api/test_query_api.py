from __future__ import annotations

from fastapi.testclient import TestClient

from ..ingestion.helpers import FIXTURES
from .conftest import upload


def test_query_endpoint_returns_rows_and_caches(client: TestClient) -> None:
    (dataset_id,) = upload(client, FIXTURES / "jmeter-load.jtl")["dataset_ids"]
    body = {
        "dataset_id": dataset_id,
        "query": {"dataset": "ds", "dimensions": ["label"], "measures": ["errors"], "top_n": 3,
                  "sort": [{"field": "errors", "dir": "desc"}]},
        "measures": [{"id": "errors", "expr": "count_if(success == false)"}],
    }  # fmt: skip
    first = client.post("/query", json=body)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["columns"] == ["label", "errors"]
    assert len(data["rows"]) == 4  # top 3 + Other
    assert data["cached"] is False
    assert client.post("/query", json=body).json()["cached"] is True


def test_query_errors_are_readable(client: TestClient) -> None:
    (dataset_id,) = upload(client, FIXTURES / "jmeter-load.jtl")["dataset_ids"]
    bad_field = {"dataset_id": dataset_id, "query": {"dataset": "ds", "dimensions": ["cpu"]}}
    resp = client.post("/query", json=bad_field)
    assert resp.status_code == 422
    assert "no field named 'cpu'" in resp.json()["detail"]

    bad_expr = {
        "dataset_id": dataset_id,
        "query": {"dataset": "ds", "measures": ["x"]},
        "measures": [{"id": "x", "expr": "count(*) FROM data; DROP"}],
    }
    assert client.post("/query", json=bad_expr).status_code == 422
    other = {
        "dataset_id": "6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a",
        "query": {"dataset": "ds", "measures": []},
    }
    assert client.post("/query", json=other).status_code in (404, 422)
