from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, User

from app.config import Settings
from app.storage.paths import ensure_parent


@dataclass(frozen=True, slots=True)
class AccessRequest:
    user_id: int
    full_name: str
    username: str


class AccessControl:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.data_dir / "access.json"
        self.state = self._load_state()
        self._bootstrap_env_users()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.admin_telegram_user_ids

    def is_allowed(self, user_id: int) -> bool:
        return self.is_admin(user_id) or str(user_id) in self.state["allowed"]

    def is_pending(self, user_id: int) -> bool:
        return str(user_id) in self.state["pending"]

    def create_request(self, user: User) -> tuple[AccessRequest, bool]:
        request = AccessRequest(
            user_id=user.id,
            full_name=user.full_name,
            username=user.username or "",
        )
        if self.is_allowed(request.user_id) or self.is_pending(request.user_id):
            return request, False
        self.state["pending"][str(request.user_id)] = {
            "full_name": request.full_name,
            "username": request.username,
        }
        self.state["rejected"].pop(str(request.user_id), None)
        self._save_state()
        return request, True

    def approve(self, user_id: int) -> None:
        key = str(user_id)
        self.state["allowed"][key] = self.state["pending"].pop(key, {})
        self.state["rejected"].pop(key, None)
        self._save_state()

    def reject(self, user_id: int) -> None:
        key = str(user_id)
        payload = self.state["pending"].pop(key, {})
        self.state["allowed"].pop(key, None)
        self.state["rejected"][key] = payload
        self._save_state()

    def revoke(self, user_id: int) -> bool:
        if self.is_admin(user_id):
            return False
        key = str(user_id)
        existed = key in self.state["allowed"]
        if existed:
            self.state["allowed"].pop(key, None)
            self.state["rejected"][key] = {"revoked": True}
            self._save_state()
        return existed

    def allowed_users_text(self) -> str:
        ids = sorted({int(user_id) for user_id in self.state["allowed"]})
        if not ids:
            return "Allowed users: empty"
        return "Allowed users:\n" + "\n".join(str(user_id) for user_id in ids)

    def _bootstrap_env_users(self) -> None:
        changed = False
        for user_id in (
            set(self.settings.admin_telegram_user_ids)
            | set(self.settings.allowed_telegram_user_ids)
        ):
            key = str(user_id)
            if key not in self.state["allowed"]:
                self.state["allowed"][key] = {"source": "env"}
                changed = True
        if changed:
            self._save_state()

    def _load_state(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return _empty_state()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _empty_state()
        state = _empty_state()
        if isinstance(loaded, dict):
            for key in state:
                value = loaded.get(key)
                if isinstance(value, dict):
                    state[key] = {
                        str(user_id): payload if isinstance(payload, dict) else {}
                        for user_id, payload in value.items()
                    }
                elif isinstance(value, list):
                    state[key] = {str(user_id): {} for user_id in value}
        return state

    def _save_state(self) -> None:
        ensure_parent(self.path)
        self.path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def access_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"access_approve:{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"access_reject:{user_id}"),
            ]
        ]
    )


def access_request_text(request: AccessRequest) -> str:
    username = f"@{request.username}" if request.username else "не указан"
    return "\n".join(
        [
            "Новая заявка на доступ:",
            f"Name: {request.full_name}",
            f"Username: {username}",
            f"User ID: {request.user_id}",
        ]
    )


def _empty_state() -> dict[str, dict[str, object]]:
    return {"allowed": {}, "pending": {}, "rejected": {}}
