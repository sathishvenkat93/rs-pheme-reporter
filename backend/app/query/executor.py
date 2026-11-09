"""Run compiled queries: sandboxed connection, timeout, row cap and a Redis result cache."""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.telemetry import span
from app.models.dataset import ColumnSchema, ColumnType
from app.models.query import Measure, SemanticQuery
from app.query import engine
from app.query.compiler import TIME_TYPES, Compiled, QueryError, compile_query


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    query_hash: str
    truncated: bool
    cached: bool = False


@dataclass(frozen=True, slots=True)
class Target:
    """What a query runs against: one dataset version."""

    version_id: UUID
    parts: Sequence[str]
    columns: Sequence[ColumnSchema]


def default_time_field(columns: dict[str, ColumnType]) -> str | None:
    if columns.get("timestamp") in TIME_TYPES:
        return "timestamp"
    return next((n for n, t in columns.items() if t in TIME_TYPES), None)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, UUID):
        return str(v)
    return v


def query_hash(compiled: Compiled) -> str:
    payload = json.dumps(
        {"sql": compiled.sql, "params": [_jsonable(p) for p in compiled.params]}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run_compiled(
    paths: Sequence[Path], compiled: Compiled, *, timeout_s: float, row_cap: int
) -> tuple[list[list[Any]], bool]:
    con = engine.connect(paths)
    statements = con.extract_statements(compiled.sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        con.close()  # defence in depth: the compiler only ever emits one SELECT
        raise QueryError("Only a single read-only query can run.")
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        with span(
            "duckdb.query", **{"db.system": "duckdb", "db.statement": compiled.sql[:2000]}
        ) as s:
            rows = con.execute(compiled.sql, compiled.params).fetchall()
            s.set_attribute("db.rows", len(rows))
    except duckdb.InterruptException:
        raise QueryError(f"The query took longer than {timeout_s:g} s and was stopped.") from None
    except duckdb.Error as exc:
        raise QueryError(
            "The query couldn't run on this dataset: " + str(exc).splitlines()[0][:200]
        ) from None
    finally:
        timer.cancel()
        con.close()
    truncated = len(rows) > row_cap
    return [[_jsonable(v) for v in r] for r in rows[:row_cap]], truncated


def _cache_key(version_id: UUID, qhash: str) -> str:
    return f"pheme:query:{version_id}:{qhash}"


def execute(
    target: Target, query: SemanticQuery, measures: Sequence[Measure], *, use_cache: bool = True
) -> QueryResult:
    settings = get_settings()
    columns = {c.name: c.type for c in target.columns}
    compiled = compile_query(
        query,
        columns,
        {m.id: m for m in measures},
        time_field=default_time_field(columns),
        row_cap=settings.query_row_cap,
    )
    qhash = query_hash(compiled)
    key = _cache_key(target.version_id, qhash)
    if use_cache and (hit := get_redis().get(key)) is not None:
        return QueryResult.model_validate_json(hit).model_copy(update={"cached": True})
    rows, truncated = run_compiled(
        engine.local_parts(target.parts),
        compiled,
        timeout_s=settings.query_timeout_s,
        row_cap=settings.query_row_cap,
    )
    result = QueryResult(columns=compiled.columns, rows=rows, query_hash=qhash, truncated=truncated)
    if use_cache:
        get_redis().set(key, result.model_dump_json(), ex=settings.query_cache_ttl_s)
    return result
