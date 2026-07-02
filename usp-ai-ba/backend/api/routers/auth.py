"""Login endpoint: issues the JWT that both StoryForge and CodeMind trust
(see config.py's JWT_SECRET -- both apps must share the same secret for
CodeMind's login/SSO to accept a StoryForge-issued token)."""
from __future__ import annotations

import datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_auth
from api.user_store import verify_password
from config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    username: str
    role: str


def _issue_token(username: str, role: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    user = verify_password(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _issue_token(user["username"], user["role"])
    return LoginResponse(access_token=token, username=user["username"], role=user["role"])


@router.post("/logout")
async def logout() -> dict:
    # Stateless JWT -- nothing to invalidate server-side; the client just
    # discards the token. Endpoint exists so the frontend has a symmetric
    # call to make, and so a future move to server-side session/blocklist
    # tracking wouldn't need a new route.
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: dict = Depends(require_auth)) -> dict:
    return user
