"""Sandboxed DuckDB connections over a dataset version's Parquet parts.

Parts are immutable, so they are cached locally under their object key. Each connection
can read only those files: external access is disabled except ``allowed_paths``, and the
configuration is locked so a query can't turn it back on.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

import duckdb

from app.core.config import get_settings
from app.ingestion import storage


def cache_path(key: str) -> Path:
    root = get_settings().local_cache_dir
    return (root / key).resolve()


def local_parts(parts: Sequence[str]) -> list[Path]:
    """Return local paths for ``parts``, downloading any that aren't cached yet."""
    out: list[Path] = []
    for key in parts:
        dest = cache_path(key)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
            os.close(fd)
            storage.get_file(key, Path(tmp))
            os.replace(tmp, dest)  # atomic: concurrent readers never see a partial file
        out.append(dest)
    return out


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def connect(
    paths: Sequence[Path], *, row_numbers: bool = False, threads: int = 4
) -> duckdb.DuckDBPyConnection:
    """An in-memory connection with a ``data`` view over ``paths`` and nothing else readable.

    With ``row_numbers``, a second view ``data_rows`` adds ``__file`` and ``__row`` (file
    order) for checks that need it, such as monotonic timestamps.
    """
    resolved = [p.resolve() for p in paths]
    if not resolved:
        raise ValueError("a dataset version needs at least one part")
    con = duckdb.connect(":memory:")
    con.execute(f"SET threads={int(threads)}")
    con.execute("SET allowed_paths=?", [[str(p) for p in resolved]])
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    files = "[" + ", ".join(_literal(p) for p in resolved) + "]"
    con.execute(f"CREATE VIEW data AS SELECT * FROM read_parquet({files}, union_by_name=true)")
    if row_numbers:
        con.execute(
            "CREATE VIEW data_rows AS SELECT * EXCLUDE (filename, file_row_number), "
            "filename AS __file, file_row_number AS __row "
            f"FROM read_parquet({files}, union_by_name=true, filename=true, file_row_number=true)"
        )
    return con


def quote(name: str) -> str:
    """Quote an identifier for DuckDB. Callers check names against the schema first."""
    return '"' + name.replace('"', '""') + '"'
