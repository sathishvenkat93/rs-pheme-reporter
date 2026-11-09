"""Write JSON Schemas for the spec and the plan: ``python -m app.models.export_schemas``."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.models.plan import DraftPlan
from app.models.spec import DashboardSpec

SCHEMA_DIR = Path(__file__).parent / "schemas"
SCHEMAS: dict[str, type[BaseModel]] = {
    "dashboard_spec.schema.json": DashboardSpec,
    "draft_plan.schema.json": DraftPlan,
}


def render(model: type[BaseModel]) -> str:
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    for name, model in SCHEMAS.items():
        (SCHEMA_DIR / name).write_text(render(model))
        print(f"wrote {SCHEMA_DIR / name}")


if __name__ == "__main__":
    main()
