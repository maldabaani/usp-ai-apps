"""Rewrites specific KEY=value lines in backend/.env in place, so the Settings
screen (api/routers/settings.py) can persist changes without disturbing
comments, ordering, or any key it wasn't asked to touch.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"


def update_env_file(updates: dict[str, str], env_path: Path = ENV_PATH) -> None:
    """Update or append KEY=value lines in env_path for each key in updates.

    Writes to a temp file and atomically replaces env_path, so a crash
    mid-write can't leave a corrupted/truncated .env behind.
    """
    remaining = dict(updates)
    lines: list[str] = []

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    lines.append(f"{key}={remaining.pop(key)}")
                    continue
            lines.append(line)

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n")
    os.replace(tmp_path, env_path)
