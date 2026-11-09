from __future__ import annotations

from app.models.export_schemas import SCHEMA_DIR, SCHEMAS, render


def test_committed_json_schemas_are_up_to_date() -> None:
    for name, model in SCHEMAS.items():
        committed = (SCHEMA_DIR / name).read_text()
        assert committed == render(model), f"{name} is stale: run `make types`"
