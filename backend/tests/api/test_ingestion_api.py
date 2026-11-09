from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.storage import get_storage
from app.ingestion.storage import TooLarge

from ..ingestion.helpers import FIXTURES, JUNIT, MALFORMED, MANIFEST, VALID, assert_readable
from .conftest import upload


@pytest.mark.parametrize("name", VALID)
def test_every_valid_fixture_becomes_a_registered_parquet_dataset(
    client: TestClient, name: str
) -> None:
    status = upload(client, FIXTURES / name)
    assert status["status"] == "done", status
    assert status["detected_format"] == MANIFEST[name]["format"]
    (dataset_id,) = status["dataset_ids"]

    ds = client.get(f"/datasets/{dataset_id}").json()
    assert ds["kind"] == MANIFEST[name]["kind"]
    (version,) = ds["versions"]
    assert version["version"] == 1
    assert version["row_count"] == MANIFEST[name]["rows"]
    (part,) = version["parts"]
    assert part.endswith(".parquet")
    stat = get_storage().stat_object(get_settings().s3_bucket, part)
    assert stat.size is not None
    assert stat.size > 0


@pytest.mark.parametrize("name", [*MALFORMED, "app.log"])
def test_bad_files_fail_with_a_readable_message(client: TestClient, name: str) -> None:
    status = upload(client, FIXTURES / name)
    assert status["status"] == "failed"
    assert status["dataset_ids"] == []
    assert status["detected_format"] == MANIFEST[name]["format"]
    assert_readable(status["error"])


def test_upload_records_size_and_sha256(client: TestClient) -> None:
    import hashlib

    path = FIXTURES / "daily-metrics.csv"
    status = upload(client, path)
    assert status["size_bytes"] == path.stat().st_size
    assert status["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_columns_give_the_same_schema_hash(client: TestClient, tmp_path: Path) -> None:
    original = FIXTURES / "daily-metrics.csv"
    lines = original.read_text().splitlines()
    other = tmp_path / "daily-metrics-other.csv"
    other.write_text(
        "\n".join([lines[0], *reversed(lines[1:50])]) + "\n"
    )  # same columns, other rows

    hashes = []
    for path in (original, original, other):
        (dataset_id,) = upload(client, path)["dataset_ids"]
        hashes.append(client.get(f"/datasets/{dataset_id}").json()["versions"][0]["schema_hash"])
    assert len(set(hashes)) == 1


def test_append_creates_a_new_version_with_all_parts(client: TestClient) -> None:
    first = upload(client, FIXTURES / JUNIT[0])
    (dataset_id,) = first["dataset_ids"]
    second = upload(client, FIXTURES / JUNIT[1], dataset_id=dataset_id)
    assert second["status"] == "done", second
    assert second["dataset_ids"] == [dataset_id]

    versions = client.get(f"/datasets/{dataset_id}").json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert len(versions[1]["parts"]) == 2
    assert versions[1]["parts"][0] == versions[0]["parts"][0]
    assert versions[1]["row_count"] == MANIFEST[JUNIT[0]]["rows"] + MANIFEST[JUNIT[1]]["rows"]


def test_append_with_the_wrong_kind_is_refused(client: TestClient) -> None:
    (dataset_id,) = upload(client, FIXTURES / JUNIT[0])["dataset_ids"]
    status = upload(client, FIXTURES / "jmeter-load.jtl", dataset_id=dataset_id)
    assert status["status"] == "failed"
    assert "test results" in status["error"]
    assert_readable(status["error"])


def test_upload_over_the_size_limit_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1024)
    path = FIXTURES / "jmeter-load.jtl"
    with path.open("rb") as fh:
        resp = client.post("/uploads", files={"file": (path.name, fh)})
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]


def test_streaming_reader_enforces_the_limit() -> None:
    import io

    from app.ingestion.storage import _HashingReader

    reader = _HashingReader(io.BytesIO(b"x" * 100), limit=50)
    reader.read(40)
    with pytest.raises(TooLarge):
        reader.read(40)


def test_unknown_session_or_dataset_is_404(client: TestClient) -> None:
    path = FIXTURES / "daily-metrics.csv"
    for field in ("session_id", "dataset_id"):
        with path.open("rb") as fh:
            resp = client.post(
                "/uploads",
                files={"file": (path.name, fh)},
                data={field: "6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a"},
            )
        assert resp.status_code == 404
    assert client.get("/uploads/6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a").status_code == 404
    assert client.get("/datasets/6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a").status_code == 404


def test_session_context_is_stored_with_extracted_facts(client: TestClient) -> None:
    session = client.post("/sessions")
    assert session.status_code == 201
    assert session.json()["state"] == "INGEST"
    sid = session.json()["id"]

    resp = client.post(
        f"/sessions/{sid}/context", json={"text": "p95 must be < 800 ms, env=staging"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["facts"] == {"env": "staging"}
    assert body["thresholds"] == [{"metric": "p95", "op": "<", "value": 800.0, "unit": "ms"}]

    from uuid import UUID

    from app.core.db import new_session
    from app.db.tables import SessionRow

    with new_session() as db:
        row = db.get(SessionRow, UUID(sid))
        assert row is not None
        assert row.context[0]["text"] == "p95 must be < 800 ms, env=staging"

    assert (
        client.post(
            "/sessions/6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a/context", json={"text": "x"}
        ).status_code
        == 404
    )


def test_upload_can_attach_to_a_session_and_carry_a_build(client: TestClient) -> None:
    sid = client.post("/sessions").json()["id"]
    status = upload(client, FIXTURES / JUNIT[0], session_id=sid, build="77")
    assert status["status"] == "done"
