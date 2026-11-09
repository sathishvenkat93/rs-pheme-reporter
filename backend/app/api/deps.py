"""Shared API dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import COOKIE, AuthError, authenticate, token_from
from app.core.db import get_db, scope
from app.core.principal import Principal
from app.orchestrator.llm import LLM, make_llm

DB = Annotated[Session, Depends(get_db)]


def principal(
    db: DB,
    authorization: Annotated[str | None, Header()] = None,
    pheme_session: Annotated[str | None, Cookie(alias=COOKIE)] = None,
) -> Principal:
    """The authenticated caller. Scopes the request's DB session to their workspace (RLS)."""
    try:
        who = authenticate(db, token_from(authorization, pheme_session))
    except AuthError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from None
    scope(db, who.workspace_id)
    return who


Caller = Annotated[Principal, Depends(principal)]


_LLM: LLM | None = None


def get_llm() -> LLM:
    """The app's LLM (offline FakeLLM unless PHEME_LLM=anthropic). Tests override this dependency."""
    global _LLM
    if _LLM is None:
        _LLM = make_llm()
    return _LLM


Model = Annotated[LLM, Depends(get_llm)]
