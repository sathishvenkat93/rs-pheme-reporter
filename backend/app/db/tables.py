"""SQLAlchemy tables. Every workspace-owned table carries ``workspace_id`` (RLS in M12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Json = dict[str, Any]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _ws() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    state: Mapped[str] = mapped_column(String(32), default="INGEST")
    dashboard_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dashboards.id", use_alter=True, ondelete="SET NULL")
    )
    context: Mapped[list[Json]] = mapped_column(JSONB, default=list, server_default="[]")
    # Reversible PII placeholders for this session (placeholder -> original). Never sent to the LLM.
    redactions: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    pending_edit: Mapped[Json | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    build: Mapped[str | None] = mapped_column(String(200))
    target_dataset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id"))
    detected_format: Mapped[str | None] = mapped_column(String(32))
    dataset_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class DatasetRow(Base):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()


class DatasetVersionRow(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    parts: Mapped[list[str]] = mapped_column(JSONB)
    columns: Mapped[list[Json]] = mapped_column(JSONB)
    schema_hash: Mapped[str] = mapped_column(String(64), index=True)
    row_count: Mapped[int] = mapped_column(BigInteger)
    upload_ids: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), unique=True
    )
    profile: Mapped[Json] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class ParserProfile(Base):
    """Learned parsers for unknown logs (post-MVP; table exists now)."""

    __tablename__ = "parser_profiles"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    name: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(32))
    mapping: Mapped[Json] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Json] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class DraftPlanRow(Base):
    __tablename__ = "draft_plans"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("draft_plans.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    plan: Mapped[Json] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class DecisionRow(Base):
    __tablename__ = "decisions"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("draft_plans.id", ondelete="CASCADE"), index=True
    )
    item: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSONB)
    choice: Mapped[str | None] = mapped_column(String(32))
    choice_detail: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()


class DashboardRow(Base):
    __tablename__ = "dashboards"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(32))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class DashboardVersionRow(Base):
    """Append-only (a trigger rejects UPDATE and DELETE)."""

    __tablename__ = "dashboard_versions"
    __table_args__ = (UniqueConstraint("dashboard_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dashboards.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[Json] = mapped_column(JSONB)
    plan_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan_versions.id"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = _ws()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[Json] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _created()
