"""FastAPI dependencies for authenticating requests.

require_auth decodes/verifies the JWT from either the Authorization header
(the normal path, attached by the Angular interceptor) or a ?token= query
param -- the latter exists because CodeMind's UI is loaded cross-origin in an
iframe and can't share the Angular shell's localStorage/cookies, so its src
URL carries the token as a query param instead (see codemind.component.ts).
require_admin additionally checks the JWT's role claim.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request

from config import settings


def _extract_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer ") :]
    token = request.query_params.get("token")
    if token:
        return token
    raise HTTPException(status_code=401, detail="Not authenticated")


def require_auth(request: Request) -> dict:
    token = _extract_token(request)
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"username": payload["sub"], "role": payload["role"]}


def require_admin(user: dict = Depends(require_auth)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
