from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.connection import connect_database
from app.review.models import ReceiptSession, SessionState
from app.storage.sessions import SessionStorageError, SessionStore, session_temp_dir
from app.telegram.handlers import common as common_handler
from app.telegram.handlers import receipt as receipt_handler


def settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path / "vault",
        "data_dir": tmp_path / "data",
        "admin_telegram_user_ids": frozenset(),
        "allowed_telegram_user_ids": frozenset(),
    }
    values["obsidian_vault"].mkdir()
    values.update(overrides)
    return Settings(**values)


def test_session_store_persists_and_restores_review_sessions(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session(app_settings, state=SessionState.WAITING_FOR_RUSSIAN_REVIEW)

    SessionStore(app_settings).save(session)
    restored = SessionStore(app_settings).load_all()

    assert restored[session.user_id].session_id == session.session_id
    assert restored[session.user_id].state == SessionState.WAITING_FOR_RUSSIAN_REVIEW
    assert not (app_settings.data_dir / "sessions").exists()


def test_session_store_restores_latest_session_per_user(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    store = SessionStore(app_settings)
    older = _session_with_identity(app_settings, state=SessionState.WAITING_FOR_RUSSIAN_REVIEW, session_id="older", user_id=333)
    newer = _session_with_identity(app_settings, state=SessionState.WAITING_FOR_CORRECTED_REVIEW, session_id="newer", user_id=333)

    store.save(older)
    store.save(newer)

    restored = store.load_all()
    assert restored[333].session_id == "newer"
    assert restored[333].state == SessionState.WAITING_FOR_CORRECTED_REVIEW


def test_session_store_does_not_restore_final_sessions(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session(app_settings, state=SessionState.DONE)

    SessionStore(app_settings).save(session)

    assert SessionStore(app_settings).load_all() == {}


def test_legacy_file_sessions_are_ignored(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    legacy_root = app_settings.data_dir / "sessions"
    legacy_root.mkdir(parents=True)
    legacy_session = _session(app_settings, state=SessionState.WAITING_FOR_RUSSIAN_REVIEW)
    (legacy_root / f"{legacy_session.user_id}.json").write_text(
        json.dumps(legacy_session.to_json()),
        encoding="utf-8",
    )

    assert SessionStore(app_settings).load_all() == {}
    assert (legacy_root / f"{legacy_session.user_id}.json").exists()


def test_stale_processing_sessions_fail_and_cleanup_temp_dir(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session(app_settings, state=SessionState.PROCESSING_OPENAI)
    session.image_path.parent.mkdir(parents=True, exist_ok=True)
    session.image_path.write_text("image", encoding="utf-8")

    SessionStore(app_settings).save(session)
    SessionStore(app_settings)

    assert _db_state(app_settings, session.session_id) == SessionState.FAILED.value
    assert not session.image_path.parent.exists()
    assert SessionStore(app_settings).load_all() == {}


def test_finish_marks_final_and_cleans_temp_dir(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session(app_settings, state=SessionState.WAITING_FOR_CORRECTED_REVIEW)
    session.image_path.parent.mkdir(parents=True, exist_ok=True)
    session.image_path.write_text("image", encoding="utf-8")
    store = SessionStore(app_settings)
    store.save(session)

    store.finish(session.user_id, SessionState.CANCELLED)

    assert _db_state(app_settings, session.session_id) == SessionState.CANCELLED.value
    assert not session.image_path.parent.exists()
    assert store.load_all() == {}


def test_session_temp_dir_is_under_tmp_processing_root(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)

    temp_dir = session_temp_dir(app_settings, "session-1")

    assert temp_dir == app_settings.tmp_storage_dir / "processing" / "session-1"
    assert temp_dir.exists()
    assert not temp_dir.is_relative_to(app_settings.obsidian_vault)


def test_session_temp_dir_rejects_escape(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)

    with pytest.raises(ValueError):
        session_temp_dir(app_settings, "../outside")

    assert not (app_settings.tmp_storage_dir / "outside").exists()


def test_cleanup_does_not_follow_symlink_escape(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    store = SessionStore(app_settings)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    symlink = store.processing_root / "linked-outside"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available in this environment")

    store.cleanup_session_temp("linked-outside")

    assert secret.exists()
    assert not symlink.exists()


def test_active_review_session_blocks_new_photo_before_quota(monkeypatch, tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    active = _session(app_settings, state=SessionState.WAITING_FOR_RUSSIAN_REVIEW)
    receipt_handler.SESSIONS.clear()
    receipt_handler.SESSIONS[active.user_id] = active

    async def allow_access(update, context) -> bool:
        return True

    def fail_if_quota_is_used(context):
        raise AssertionError("quota check should not run when an active session exists")

    monkeypatch.setattr(receipt_handler, "ensure_access", allow_access)
    monkeypatch.setattr(receipt_handler, "quotas", fail_if_quota_is_used)
    message = _FakeMessage()
    update = SimpleNamespace(effective_user=SimpleNamespace(id=active.user_id), message=message)
    context = SimpleNamespace(user_data={})

    try:
        asyncio.run(receipt_handler.handle_photo(update, context))
    finally:
        receipt_handler.SESSIONS.clear()

    assert message.texts == ["У вас уже есть активная обработка. Подтвердите или отмените текущую сессию."]


def test_save_session_does_not_mutate_memory_when_storage_fails(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session_with_identity(
        app_settings,
        state=SessionState.WAITING_FOR_RUSSIAN_REVIEW,
        session_id="save-fails",
        user_id=444,
    )
    common_handler.SESSIONS.clear()

    class _FailingStore:
        def save(self, _session: ReceiptSession) -> None:
            raise SessionStorageError("boom")

    context = _context_with_store(_FailingStore())

    with pytest.raises(SessionStorageError):
        common_handler.save_session(session, context)

    assert 444 not in common_handler.SESSIONS


def test_delete_session_keeps_memory_when_storage_fails(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session_with_identity(
        app_settings,
        state=SessionState.WAITING_FOR_RUSSIAN_REVIEW,
        session_id="finish-fails",
        user_id=555,
    )
    common_handler.SESSIONS.clear()
    common_handler.SESSIONS[555] = session

    class _FailingStore:
        def finish(self, user_id: int, final_state: SessionState) -> None:
            raise SessionStorageError(f"{user_id}:{final_state.value}")

    context = _context_with_store(_FailingStore())
    common_handler.delete_session(555, context, final_state=SessionState.CANCELLED)

    assert common_handler.SESSIONS[555] is session


class _FakeMessage:
    def __init__(self) -> None:
        self.photo = [object()]
        self.caption = ""
        self.texts: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.texts.append(text)


def _session(app_settings: Settings, *, state: SessionState) -> ReceiptSession:
    session_id = f"session-{state.value}"
    temp_dir = app_settings.tmp_storage_dir / "processing" / session_id
    return ReceiptSession(
        user_id=222,
        image_path=temp_dir / "original.jpg",
        clean_ocr_path=temp_dir / "clean.hy.txt",
        source_ocr_path=temp_dir / "source.hy.txt",
        temporary_base_name="tmp",
        created_at=datetime(2026, 5, 22, 12, 0, 0),
        state=state,
        session_id=session_id,
    )


def _session_with_identity(
    app_settings: Settings,
    *,
    state: SessionState,
    session_id: str,
    user_id: int,
) -> ReceiptSession:
    temp_dir = app_settings.tmp_storage_dir / "processing" / session_id
    return ReceiptSession(
        user_id=user_id,
        image_path=temp_dir / "original.jpg",
        clean_ocr_path=temp_dir / "clean.hy.txt",
        source_ocr_path=temp_dir / "source.hy.txt",
        temporary_base_name="tmp",
        created_at=datetime(2026, 5, 22, 12, 0, 0),
        state=state,
        session_id=session_id,
    )


def _context_with_store(store):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"session_store": store}))


def _db_state(app_settings: Settings, session_id: str) -> str:
    with connect_database(app_settings) as connection:
        row = connection.execute(
            """
            select state
            from processing_sessions
            where id = ?
            """,
            (session_id,),
        ).fetchone()
    assert row is not None
    return str(row["state"])
