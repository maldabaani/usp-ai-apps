"""Registry of user accounts, backing login (api/routers/auth.py).

Persisted as JSON in settings.JOBS_DIR, same load/save/module-cache pattern
as job_registry.py -- this is a small local user store, not a full identity
provider, appropriate for the same local/small-team deployment this whole app
targets.
"""
from __future__ import annotations

import json
import logging
import os

import bcrypt

from config import settings

logger = logging.getLogger(__name__)

_USERS_PATH = os.path.join(settings.JOBS_DIR, "users.json")
_users: dict[str, dict] | None = None

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def _load() -> dict[str, dict]:
    global _users
    if _users is not None:
        return _users
    if os.path.exists(_USERS_PATH):
        with open(_USERS_PATH) as f:
            _users = json.load(f)
    else:
        _users = {}
    return _users


def _save() -> None:
    os.makedirs(settings.JOBS_DIR, exist_ok=True)
    with open(_USERS_PATH, "w") as f:
        json.dump(_users, f)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_user(username: str, password: str, role: str = "user") -> None:
    """Create a new user. Raises ValueError if the username is already taken."""
    users = _load()
    if username in users:
        raise ValueError(f"User {username!r} already exists")
    users[username] = {
        "username": username,
        "password_hash": _hash_password(password),
        "role": role,
    }
    _save()


def get_user(username: str) -> dict | None:
    return _load().get(username)


def verify_password(username: str, password: str) -> dict | None:
    """Return the user dict (without password_hash) if the password is
    correct, else None."""
    user = get_user(username)
    if user is None:
        return None
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return None
    return {"username": user["username"], "role": user["role"]}


def ensure_default_admin() -> None:
    """Seed a default admin account on first run only, so there's no
    chicken-and-egg "no users exist yet" problem. Logs a warning so it's
    obvious this needs changing before any real/shared deployment."""
    if _load():
        return
    create_user(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, role="admin")
    logger.warning(
        "No users existed -- created default admin account (username=%r, "
        "password=%r). Log in and change this immediately.",
        DEFAULT_ADMIN_USERNAME,
        DEFAULT_ADMIN_PASSWORD,
    )
