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
import secrets

import bcrypt

from config import settings

logger = logging.getLogger(__name__)

_USERS_PATH = os.path.join(settings.JOBS_DIR, "users.json")
_users: dict[str, dict] | None = None

DEFAULT_ADMIN_USERNAME = "admin"


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
    tmp_path = _USERS_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(_users, f)
    os.replace(tmp_path, _USERS_PATH)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_user(
    username: str, password: str, role: str = "user", *, must_change_password: bool = False
) -> None:
    """Create a new user. Raises ValueError if the username is already taken."""
    users = _load()
    if username in users:
        raise ValueError(f"User {username!r} already exists")
    users[username] = {
        "username": username,
        "password_hash": _hash_password(password),
        "role": role,
        "must_change_password": must_change_password,
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
    return {
        "username": user["username"],
        "role": user["role"],
        "must_change_password": user.get("must_change_password", False),
    }


def set_password(username: str, password: str) -> None:
    """Update a user's password and clear must_change_password. Raises
    ValueError if the user doesn't exist."""
    users = _load()
    user = users.get(username)
    if user is None:
        raise ValueError(f"User {username!r} does not exist")
    user["password_hash"] = _hash_password(password)
    user["must_change_password"] = False
    _save()


def ensure_default_admin() -> None:
    """Seed a default admin account on first run only, so there's no
    chicken-and-egg "no users exist yet" problem. The password is a random
    one-time value, printed once to the log -- never the literal "admin" --
    and the account is flagged must_change_password so /auth/login surfaces
    that it needs to be rotated before anything else."""
    if _load():
        return
    password = secrets.token_urlsafe(16)
    create_user(DEFAULT_ADMIN_USERNAME, password, role="admin", must_change_password=True)
    logger.warning(
        "No users existed -- created default admin account (username=%r, "
        "password=%r). This password is shown only this once: log in with "
        "it now and change it via POST /auth/change-password immediately.",
        DEFAULT_ADMIN_USERNAME,
        password,
    )
