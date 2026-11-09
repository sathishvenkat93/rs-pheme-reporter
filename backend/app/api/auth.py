"""Sign-in for PHEME_AUTH_MODE=dev (email login), and who-am-I for every mode."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.deps import DB, Caller
from app.core.auth import COOKIE, issue_token, provision
from app.core.config import get_settings
from app.db.tables import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    # A plain address pattern: dev setups often use internal domains (.local, .test).
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)


class LoginOut(BaseModel):
    token: str
    user_id: UUID
    workspace_id: UUID


class Me(BaseModel):
    user_id: UUID
    workspace_id: UUID
    email: str | None


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, db: DB, response: Response) -> LoginOut:
    s = get_settings()
    if s.auth_mode != "dev":
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Email login is only available in dev auth mode."
        )
    who = provision(db, str(body.email))
    token = issue_token(who, str(body.email))
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=s.env == "production",
                        max_age=s.session_ttl_s)  # fmt: skip
    return LoginOut(token=token, user_id=who.user_id, workspace_id=who.workspace_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE)


@router.get("/me", response_model=Me)
def me(caller: Caller, db: DB) -> Me:
    user = db.get(User, caller.user_id)
    return Me(
        user_id=caller.user_id, workspace_id=caller.workspace_id, email=user.email if user else None
    )
