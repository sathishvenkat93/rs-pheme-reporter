"""A 200 MB CSV ingests without being loaded fully into memory."""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

TARGET_BYTES = 200 * 1024 * 1024

CHILD = r"""
import json, resource, sys, tempfile
from pathlib import Path
from app.ingestion.detect import HEAD_BYTES, detect
from app.ingestion.normalize import ParseContext
from app.ingestion.parsers import parse, write_parquet

def peak() -> int:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024  # macOS: bytes, Linux: KiB

src = Path(sys.argv[1])
before = peak()
with open(src, "rb") as fh:
    d = detect(src.name, fh.read(HEAD_BYTES))
parsed = parse(src, d, ParseContext("u", src.name, None, 1, "UTC"))
_, rows = write_parquet(parsed, Path(tempfile.mkdtemp()) / "out.parquet", d.format)
print(json.dumps({"rows": rows, "before": before, "after": peak(), "format": d.format.value}))
"""


def _write_csv(path: Path) -> int:
    rng = random.Random(7)
    rows = 0
    with path.open("w") as fh:
        fh.write("timestamp,service,region,status,latency_ms,bytes,message\n")
        while fh.tell() < TARGET_BYTES:
            chunk = [
                f"2026-10-{1 + (rows + i) % 28:02d}T{(rows + i) % 24:02d}:00:00Z,svc-{rng.randint(1, 40)},"
                f"{rng.choice(('emea', 'amer', 'apac'))},{rng.choice(('ok', 'ok', 'ok', 'error'))},"
                f"{rng.random() * 900:.2f},{rng.randint(100, 90000)},request handled for user {rng.randint(1, 10**6)}"
                for i in range(20_000)
            ]
            fh.write("\n".join(chunk) + "\n")
            rows += len(chunk)
    return rows


def _peak_growth(src: Path) -> tuple[int, int]:
    backend = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(src)],
        cwd=backend,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["format"] == "csv"
    return int(result["rows"]), int(result["after"]) - int(result["before"])


@pytest.mark.slow
def test_200mb_csv_streams_without_loading_into_memory(tmp_path: Path) -> None:
    """Memory must not scale with the file.

    Two checks: (1) peak RSS growth stays below the file's size (a fully loaded copy needs
    at least that); (2) going from a 50 MB to a 200 MB file adds less than 35% of the extra
    bytes. The engine's fixed overhead (thread pools, allocator arenas) is ~130 MiB, so an
    absolute "half the file" bound would measure that overhead, not streaming.
    """
    big = tmp_path / "big.csv"
    rows = _write_csv(big)
    size = big.stat().st_size
    assert size >= TARGET_BYTES

    small = tmp_path / "small.csv"
    with big.open("rb") as src, small.open("wb") as dst:
        dst.writelines(src.readline() for _ in range(rows // 4 + 1))  # header + ~25% of rows
    small_size = small.stat().st_size

    big_rows, big_growth = _peak_growth(big)
    _, small_growth = _peak_growth(small)
    assert big_rows == rows
    mib = 2**20
    print(
        f"{small_size / mib:.0f} MiB → +{small_growth / mib:.0f} MiB RSS; "
        f"{size / mib:.0f} MiB → +{big_growth / mib:.0f} MiB RSS"
    )
    assert big_growth < size, "peak memory grew by more than the file size"
    assert big_growth - small_growth < 0.35 * (size - small_size), "memory scales with the input"
