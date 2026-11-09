"""Migrations apply and roll back cleanly on an empty database."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.tables import Base

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


@pytest.fixture
def empty_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    base = make_url(get_settings().migration_database_url)
    name = f"pheme_migrate_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name)
    monkeypatch.setenv("PHEME_MIGRATION_DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _alembic() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def test_upgrade_downgrade_upgrade(empty_db: Engine) -> None:
    cfg = _alembic()
    command.upgrade(cfg, "head")
    tables = set(inspect(empty_db).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    assert len(tables) == 16  # + dashboard_shares (M12)

    command.downgrade(cfg, "base")
    assert set(inspect(empty_db).get_table_names()) <= {"alembic_version"}

    command.upgrade(cfg, "head")
    assert set(inspect(empty_db).get_table_names()) - {"alembic_version"} == tables


def test_every_workspace_owned_table_has_workspace_id(empty_db: Engine) -> None:
    command.upgrade(_alembic(), "head")
    insp = inspect(empty_db)
    for table in Base.metadata.tables:
        if table == "workspaces":
            continue
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert "workspace_id" in cols, table
        assert cols["workspace_id"]["nullable"] is False, table


def test_dashboard_versions_are_append_only(empty_db: Engine) -> None:
    command.upgrade(_alembic(), "head")
    ws, user, dash = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with empty_db.begin() as conn:
        conn.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, 'w')"), {"id": ws})
        conn.execute(
            text("INSERT INTO users (id, workspace_id, email) VALUES (:id, :ws, 'a@example.com')"),
            {"id": user, "ws": ws},
        )
        conn.execute(
            text(
                "INSERT INTO dashboards (id, workspace_id, owner_id, title, current_version) "
                "VALUES (:id, :ws, :u, 'd', 1)"
            ),
            {"id": dash, "ws": ws, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO dashboard_versions (id, workspace_id, dashboard_id, version, spec, author_id) "
                "VALUES (:id, :ws, :d, 1, '{}', :u)"
            ),
            {"id": uuid.uuid4(), "ws": ws, "d": dash, "u": user},
        )
    for stmt in ("UPDATE dashboard_versions SET version = 2", "DELETE FROM dashboard_versions"):
        with pytest.raises(DBAPIError, match="append-only"), empty_db.begin() as conn:
            conn.execute(text(stmt))
    with pytest.raises(DBAPIError, match="duplicate key"), empty_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dashboard_versions (id, workspace_id, dashboard_id, version, spec, author_id) "
                "VALUES (:id, :ws, :d, 1, '{}', :u)"
            ),
            {"id": uuid.uuid4(), "ws": ws, "d": dash, "u": user},
        )
