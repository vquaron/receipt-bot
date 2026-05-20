from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.storage.paths import dated_relpath


def user_root_rel(settings: Settings, user_id: int) -> Path:
    root = Path(settings.user_vault_root.strip("/"))
    if root.is_absolute() or ".." in root.parts:
        raise ValueError("USER_VAULT_ROOT must be a safe relative path.")
    return root / str(user_id)


def user_dated_relpath(settings: Settings, user_id: int, root: str, stamp: datetime, filename: str) -> Path:
    return user_root_rel(settings, user_id) / dated_relpath(root, stamp, filename)

