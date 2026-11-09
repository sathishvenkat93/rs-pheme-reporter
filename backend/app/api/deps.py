"""Shared API dependencies."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import COOKIE, AuthError, authenticate, token_from
from app.core.config import get_settings
from app.core.db import get_db, scope
from app.core.principal import Principal
from app.core.redis import get_redis
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
    _rate_limit(who)
    return who


def _rate_limit(who: Principal) -> None:
    """Fixed one-minute window per workspace (Redis counter). 0 disables."""
    limit = get_settings().rate_limit_per_min
    if limit <= 0:
        return
    now = int(time.time())
    window = now // 60
    key = f"pheme:ratelimit:{who.workspace_id}:{window}"
    redis = get_redis()
    count = int(redis.incr(key))
    if count == 1:
        redis.expire(key, 61)
    if count > limit:
        retry = 60 - now % 60
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many requests for this workspace. Try again in {retry} s.",
            headers={"Retry-After": str(retry)},
        )


Caller = Annotated[Principal, Depends(principal)]


_LLM: LLM | None = None


def get_llm() -> LLM:
    """The app's LLM (offline FakeLLM unless PHEME_LLM=anthropic). Tests override this dependency."""
    global _LLM
    if _LLM is None:
        _LLM = make_llm()
    return _LLM


Model = Annotated[LLM, Depends(get_llm)]
