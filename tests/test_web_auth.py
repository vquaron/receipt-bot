from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.db.connection import connect_database
from app.web.auth import WebAuthRepository, token_hash


def test_magic_link_stores_hash_and_redeems_once(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    repository = WebAuthRepository(app_settings)

    link = repository.create_magic_link(222, created_at=datetime(2026, 5, 23, 10, 0, 0))

    with connect_database(app_settings) as connection:
        row = connection.execute("select token_hash, used_at from magic_links where id = ?", (link.id,)).fetchone()
    assert row["token_hash"] == token_hash(link.token)
    assert link.token not in row["token_hash"]
    assert row["used_at"] is None

    session = repository.redeem_magic_token(
        link.token,
        now=datetime(2026, 5, 23, 10, 1, 0),
        user_agent="test-agent",
        ip_address="127.0.0.1",
    )
    assert session.telegram_user_id == 222
    assert repository.get_session(session.token, now=datetime(2026, 5, 23, 10, 2, 0)) is not None

    with connect_database(app_settings) as connection:
        link_row = connection.execute("select used_at, user_agent_used from magic_links where id = ?", (link.id,)).fetchone()
        session_row = connection.execute("select session_hash, user_agent, ip_address from web_sessions where id = ?", (session.id,)).fetchone()
    assert link_row["used_at"]
    assert link_row["user_agent_used"] == "test-agent"
    assert session_row["session_hash"] == token_hash(session.token)
    assert session_row["user_agent"] == "test-agent"
    assert session_row["ip_address"] == "127.0.0.1"


def test_magic_link_rejects_used_expired_and_unknown_tokens(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    repository = WebAuthRepository(app_settings)
    link = repository.create_magic_link(222, created_at=datetime(2026, 5, 23, 10, 0, 0))

    repository.redeem_magic_token(link.token, now=datetime(2026, 5, 23, 10, 1, 0))

    for token in (link.token, "unknown"):
        try:
            repository.redeem_magic_token(token, now=datetime(2026, 5, 23, 10, 2, 0))
        except RuntimeError:
            pass
        else:
            raise AssertionError("token should be rejected")

    expired = repository.create_magic_link(222, created_at=datetime(2026, 5, 23, 10, 0, 0))
    try:
        repository.redeem_magic_token(expired.token, now=datetime(2026, 5, 23, 10, 11, 0))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expired token should be rejected")


def test_session_rejects_expired_and_revoked(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path, web_session_ttl_days=1)
    repository = WebAuthRepository(app_settings)
    link = repository.create_magic_link(222, created_at=datetime(2026, 5, 23, 10, 0, 0))
    session = repository.redeem_magic_token(link.token, now=datetime(2026, 5, 23, 10, 1, 0))

    assert repository.get_session(session.token, now=datetime(2026, 5, 24, 10, 2, 0)) is None
    assert repository.get_session(session.token, now=datetime(2026, 5, 23, 10, 2, 0)) is not None
    assert repository.revoke_session(session.token, now=datetime(2026, 5, 23, 10, 3, 0))
    assert repository.get_session(session.token, now=datetime(2026, 5, 23, 10, 4, 0)) is None


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
        "web_base_url": "https://receipts.example",
    }
    values.update(overrides)
    values["obsidian_vault"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)
