from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.db.connection import connect_database
from app.telegram.handlers.web import web_command
from app.users.access_service import AccessControl


def test_web_command_creates_magic_link_for_allowed_user(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path, web_base_url="https://receipts.example")
    update = _update(user_id=222)

    asyncio.run(web_command(update, _context(app_settings)))

    assert len(update.message.messages) == 1
    message = update.message.messages[0]
    assert update.message.replies[0][1]["link_preview_options"].is_disabled is True
    assert "https://receipts.example/auth/magic?token=" in message
    with connect_database(app_settings) as connection:
        row = connection.execute("select telegram_user_id, token_hash, used_at from magic_links").fetchone()
    assert row["telegram_user_id"] == 222
    assert row["token_hash"] not in message
    assert row["used_at"] is None


def test_web_command_requires_base_url(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path, web_base_url="")
    update = _update(user_id=222)

    asyncio.run(web_command(update, _context(app_settings)))

    assert update.message.messages == ["WEB_BASE_URL пока не настроен. Веб-вход недоступен."]
    with connect_database(app_settings) as connection:
        count = connection.execute("select count(*) from magic_links").fetchone()[0]
    assert count == 0


def test_web_command_uses_existing_access_flow_for_unauthorized_user(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path, web_base_url="https://receipts.example")
    update = _update(user_id=999)

    asyncio.run(web_command(update, _context(app_settings)))

    assert update.message.messages == ["Заявка на доступ создана, но ADMIN_TELEGRAM_USER_IDS пока не настроен."]
    with connect_database(app_settings) as connection:
        count = connection.execute("select count(*) from magic_links").fetchone()[0]
    assert count == 0


def _context(app_settings: Settings):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": app_settings,
                "access_control": AccessControl(app_settings),
            }
        ),
        bot=SimpleNamespace(send_message=_send_message),
    )


async def _send_message(*args, **kwargs) -> None:
    return None


def _update(*, user_id: int):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, full_name=f"User {user_id}", username="user"),
        message=_Message(),
    )


class _Message:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.messages.append(text)
        self.replies.append((text, kwargs))


def _settings(tmp_path: Path, **overrides) -> Settings:
    data_dir = tmp_path / "data"
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path / "vault",
        "data_dir": data_dir,
        "admin_telegram_user_ids": frozenset(),
        "allowed_telegram_user_ids": frozenset({222}),
        "database_url": f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        "app_storage_dir": data_dir / "storage",
        "tmp_storage_dir": data_dir / "tmp",
        "export_storage_dir": data_dir / "exports",
        "debug_storage_dir": data_dir / "debug",
    }
    values.update(overrides)
    values["obsidian_vault"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)
