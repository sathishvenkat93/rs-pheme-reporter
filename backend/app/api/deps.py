"""Shared API dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.principal import Principal, current_principal
from app.db.tables import User, Workspace

DB = Annotated[Session, Depends(get_db)]


def principal(db: DB, who: Annotated[Principal, Depends(current_principal)]) -> Principal:
    """The caller, with their workspace and user rows guaranteed to exist (dev bootstrap).

    TODO(M12): real auth provisions these; drop the auto-create.
    """
    if db.get(Workspace, who.workspace_id) is None:
        db.add(Workspace(id=who.workspace_id, name="Dev workspace"))
        db.flush()
    if db.get(User, who.user_id) is None:
        db.add(
            User(
                id=who.user_id,
                workspace_id=who.workspace_id,
                email=f"dev+{who.user_id}@pheme.local",
            )
        )
    db.commit()
    return who


Caller = Annotated[Principal, Depends(principal)]
