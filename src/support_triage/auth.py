"""Caller authentication for the web app.

The deployed shape had no caller authentication at all: anyone reaching the task's public IP
could triage a case and approve a state change. This is the smallest credible gate — a shared
bearer token from ``APP_TOKEN``, compared in constant time. It is not identity: it is a caller
gate. The named approver is still the human in the approval record.

Fail closed: with ``APP_TOKEN`` unset, every API request gets 503 rather than an open door.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException

TOKEN_ENV = "APP_TOKEN"


def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: require ``Authorization: Bearer <APP_TOKEN>``."""
    token = os.getenv(TOKEN_ENV, "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail=f"{TOKEN_ENV} is not configured; the API is closed",
        )
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, token):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
