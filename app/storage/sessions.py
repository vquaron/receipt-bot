from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.review.models import ReceiptSession, SessionState


LOGGER = logging.getLogger(__name__)

RESTORABLE_STATES = {
    SessionState.WAITING_FOR_RUSSIAN_REVIEW.value,
    SessionState.WAITING_FOR_CORRECTED_REVIEW.value,
}
PROCESSING_STATES = {
    SessionState.PROCESSING_OCR.value,
    SessionState.PROCESSING_OPENAI.value,
}
FINAL_STATES = {
    SessionState.DONE.value,
    SessionState.CANCELLED.value,
    SessionState.FAILED.value,
}


class SessionStorageError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.processing_root = settings.tmp_storage_dir / "processing"
        self.processing_root.mkdir(parents=True, exist_ok=True)
        initialize_database(settings)
        self.cleanup_startup_state()

    def load_all(self) -> dict[int, ReceiptSession]:
        now = datetime.now().isoformat()
        sessions: dict[int, ReceiptSession] = {}
        try:
            with connect_database(self.settings) as connection:
                rows = connection.execute(
                    """
                    select telegram_user_id, session_json
                    from processing_sessions
                    where state in ({})
                      and (expires_at is null or expires_at >= ?)
                    order by updated_at desc
                    """.format(_placeholders(RESTORABLE_STATES)),
                    (*sorted(RESTORABLE_STATES), now),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to load processing sessions.") from exc

        for row in rows:
            try:
                session = ReceiptSession.from_json(json.loads(str(row["session_json"])))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                LOGGER.warning("Skipping invalid processing session user_id=%s", row["telegram_user_id"])
                continue
            sessions[session.user_id] = session
        return sessions

    def get_active(self, user_id: int) -> ReceiptSession | None:
        try:
            with connect_database(self.settings) as connection:
                row = connection.execute(
                    """
                    select session_json
                    from processing_sessions
                    where telegram_user_id = ?
                      and state not in ({})
                      and (expires_at is null or expires_at >= ?)
                    order by updated_at desc
                    limit 1
                    """.format(_placeholders(FINAL_STATES)),
                    (user_id, *sorted(FINAL_STATES), datetime.now().isoformat()),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to read processing session.") from exc
        if row is None:
            return None
        try:
            return ReceiptSession.from_json(json.loads(str(row["session_json"])))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SessionStorageError("Invalid processing session payload.") from exc

    def save(self, session: ReceiptSession) -> None:
        now = datetime.now()
        expires_at = _session_expires_at(session, self.settings)
        try:
            with connect_database(self.settings) as connection:
                connection.execute(
                    """
                    insert into processing_sessions(
                        id,
                        telegram_user_id,
                        document_id,
                        state,
                        document_type,
                        session_json,
                        created_at,
                        updated_at,
                        expires_at
                    )
                    values (?, ?, null, ?, ?, ?, ?, ?, ?)
                    on conflict(id) do update set
                        telegram_user_id = excluded.telegram_user_id,
                        state = excluded.state,
                        document_type = excluded.document_type,
                        session_json = excluded.session_json,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        session.session_id,
                        session.user_id,
                        session.state.value,
                        session.document_type,
                        json.dumps(session.to_json(), ensure_ascii=False, sort_keys=True),
                        session.created_at.isoformat(),
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to save processing session.") from exc

    def finish(self, user_id: int, final_state: SessionState) -> None:
        if final_state.value not in FINAL_STATES:
            raise ValueError("final_state must be done, cancelled, or failed.")
        session = self.get_active(user_id)
        if session is None:
            return
        session.state = final_state
        now = datetime.now().isoformat()
        try:
            with connect_database(self.settings) as connection:
                connection.execute(
                    """
                    update processing_sessions
                    set state = ?,
                        session_json = ?,
                        updated_at = ?
                    where id = ?
                    """,
                    (
                        final_state.value,
                        json.dumps(session.to_json(), ensure_ascii=False, sort_keys=True),
                        now,
                        session.session_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to finish processing session.") from exc
        self.cleanup_session_temp(session.session_id)

    def cleanup_startup_state(self) -> None:
        self._fail_stale_processing_sessions()
        self._expire_old_sessions()
        self.cleanup_old_temp_dirs()

    def cleanup_session_temp(self, session_id: str) -> None:
        if not session_id:
            return
        self._remove_inside_processing_root(self.processing_root / session_id)

    def cleanup_old_temp_dirs(self) -> None:
        if not self.processing_root.exists() or self.processing_root.is_symlink():
            return
        cutoff = datetime.now() - _retention_delta(self.settings)
        for child in list(self.processing_root.iterdir()):
            try:
                if child.is_symlink():
                    child.unlink()
                    continue
                if not child.is_dir():
                    continue
                modified_at = datetime.fromtimestamp(child.stat().st_mtime)
            except OSError:
                continue
            if modified_at < cutoff:
                self._remove_inside_processing_root(child)

    def _fail_stale_processing_sessions(self) -> None:
        try:
            with connect_database(self.settings) as connection:
                rows = connection.execute(
                    """
                    select id, session_json
                    from processing_sessions
                    where state in ({})
                    """.format(_placeholders(PROCESSING_STATES)),
                    tuple(sorted(PROCESSING_STATES)),
                ).fetchall()
                for row in rows:
                    session_id = str(row["id"])
                    try:
                        session = ReceiptSession.from_json(json.loads(str(row["session_json"])))
                        session.state = SessionState.FAILED
                        payload = json.dumps(session.to_json(), ensure_ascii=False, sort_keys=True)
                    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                        payload = str(row["session_json"])
                    connection.execute(
                        """
                        update processing_sessions
                        set state = ?,
                            session_json = ?,
                            updated_at = ?
                        where id = ?
                        """,
                        (SessionState.FAILED.value, payload, datetime.now().isoformat(), session_id),
                    )
                    self.cleanup_session_temp(session_id)
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to cleanup stale processing sessions.") from exc

    def _expire_old_sessions(self) -> None:
        now = datetime.now().isoformat()
        try:
            with connect_database(self.settings) as connection:
                rows = connection.execute(
                    """
                    select id, session_json
                    from processing_sessions
                    where state not in ({})
                      and expires_at is not null
                      and expires_at < ?
                    """.format(_placeholders(FINAL_STATES)),
                    (*sorted(FINAL_STATES), now),
                ).fetchall()
                for row in rows:
                    session_id = str(row["id"])
                    try:
                        session = ReceiptSession.from_json(json.loads(str(row["session_json"])))
                        session.state = SessionState.FAILED
                        payload = json.dumps(session.to_json(), ensure_ascii=False, sort_keys=True)
                    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                        payload = str(row["session_json"])
                    connection.execute(
                        """
                        update processing_sessions
                        set state = ?,
                            session_json = ?,
                            updated_at = ?
                        where id = ?
                        """,
                        (SessionState.FAILED.value, payload, datetime.now().isoformat(), session_id),
                    )
                    self.cleanup_session_temp(session_id)
        except sqlite3.Error as exc:
            raise SessionStorageError("Failed to expire processing sessions.") from exc

    def _remove_inside_processing_root(self, path: Path) -> None:
        root = self.processing_root.resolve()
        candidate = path.expanduser()
        try:
            parent = candidate.parent.resolve(strict=False)
        except OSError:
            return
        if candidate.is_symlink():
            if parent.is_relative_to(root):
                try:
                    candidate.unlink()
                except OSError:
                    LOGGER.warning("Failed to cleanup processing temp symlink: %s", path, exc_info=True)
            return
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            return
        if not resolved.is_relative_to(root):
            LOGGER.warning("Refusing to cleanup temp path outside processing root: %s", path)
            return
        try:
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink()
            elif candidate.is_dir():
                shutil.rmtree(candidate)
        except OSError:
            LOGGER.warning("Failed to cleanup processing temp path: %s", path, exc_info=True)


def session_temp_dir(settings: Settings, session_id: str) -> Path:
    root = settings.tmp_storage_dir / "processing"
    root.mkdir(parents=True, exist_ok=True)
    path = root / session_id
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if not path_resolved.is_relative_to(root_resolved):
        raise ValueError("Session temp path escapes processing root.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_expires_at(session: ReceiptSession, settings: Settings) -> datetime:
    return session.created_at + _retention_delta(settings)


def _retention_delta(settings: Settings) -> timedelta:
    return timedelta(hours=max(1, settings.storage_retention_tmp_hours))


def _placeholders(values: set[str]) -> str:
    return ", ".join("?" for _ in values)
