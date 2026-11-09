"""Turn fixtures into local Parquet files without the API (used by Phase 2 tests and demo-data)."""

from __future__ import annotations

from pathlib import Path

from app.ingestion.detect import HEAD_BYTES, detect
from app.ingestion.normalize import ParseContext
from app.ingestion.parsers import parse, write_parquet
from app.models.dataset import DatasetKind

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_parquet(names: list[str], out_dir: Path) -> tuple[list[Path], DatasetKind]:
    """Parse fixtures (one dataset, one part per file) into ``out_dir``."""
    paths: list[Path] = []
    kind: DatasetKind | None = None
    for i, name in enumerate(names):
        src = FIXTURES / name
        with src.open("rb") as fh:
            d = detect(name, fh.read(HEAD_BYTES))
        parsed = parse(src, d, ParseContext(f"run-{i + 1}", name, None, i + 1, "UTC"))
        out = out_dir / f"{Path(name).stem}.parquet"
        write_parquet(parsed, out, d.format)
        paths.append(out)
        kind = parsed.kind
    assert kind is not None
    return paths, kind
