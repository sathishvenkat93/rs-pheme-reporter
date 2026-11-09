"""The caller's identity. A dev stub until real auth arrives in M12."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.config import get_settings


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling, and in which workspace."""

    user_id: UUID
    workspace_id: UUID


def current_principal() -> Principal:
    """FastAPI dependency returning the caller.

    TODO(M12): replace with OIDC / email-login auth.
    """
    s = get_settings()
    return Principal(user_id=s.dev_user_id, workspace_id=s.dev_workspace_id)
