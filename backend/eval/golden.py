"""Golden-set format: one folder per dataset under eval/goldens/<name>/golden.yaml.

    name, description
    files: data files (paths relative to the golden folder, or bare names in tests/fixtures)
    domain: expected domain
    experts: [{name, source, top8: [chart descriptor, ...]}]   (one list per expert)
    requests: [{id, text, chart_type, phrases: [[text, role]], time_grain, expected}]
    edits: [{name, prompt, ops: [patch op with $type placeholders], expect_changed: [path prefixes]}]

A chart descriptor is {chart: type or [types], dims: [...], measures: [ids or field names]}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "tests" / "fixtures"


class Descriptor(BaseModel):
    chart: str | list[str]
    dims: list[str] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)

    def charts(self) -> set[str]:
        return {self.chart} if isinstance(self.chart, str) else set(self.chart)


class Expert(BaseModel):
    name: str
    source: str  # who made the list, and how independent it is
    top8: list[Descriptor] = Field(min_length=1, max_length=8)


class LabelledRequest(BaseModel):
    id: str
    text: str
    chart_type: str | None = None
    phrases: list[tuple[str, str | None]] = Field(default_factory=list)
    time_grain: str | None = None
    expected: Literal["feasible", "adjusted", "not_feasible", "needs_clarification"]


class EditScenario(BaseModel):
    name: str
    prompt: str
    ops: list[dict[str, Any]]
    expect_changed: list[str]


class Golden(BaseModel):
    name: str
    description: str = ""
    files: list[str]
    domain: str
    experts: list[Expert] = Field(default_factory=list)
    requests: list[LabelledRequest] = Field(default_factory=list)
    edits: list[EditScenario] = Field(default_factory=list)
    folder: Path = Path()

    def paths(self) -> list[Path]:
        out = []
        for f in self.files:
            p = (self.folder / f) if (self.folder / f).exists() else FIXTURES / f
            if not p.exists():
                raise FileNotFoundError(f"{self.name}: data file {f!r} not found")
            out.append(p)
        return out


def load_goldens(root: Path = HERE / "goldens", only: list[str] | None = None) -> list[Golden]:
    goldens = []
    for path in sorted(root.glob("*/golden.yaml")):
        g = Golden.model_validate({**yaml.safe_load(path.read_text()), "folder": path.parent})
        if only is None or g.name in only:
            goldens.append(g)
    return goldens
