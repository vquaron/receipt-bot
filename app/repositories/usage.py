from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database


RECEIPT_ATTEMPT_EVENT = "receipt_attempt"
LEGACY_RECEIPT_ACTION = "receipt_process"


@dataclass(frozen=True, slots=True)
class UsageAttemptResult:
    allowed: bool
    reason: str = ""
    event_id: int | None = None
    daily_used: int = 0
    daily_limit: int = 0
    monthly_used: int = 0
    monthly_limit: int = 0


class UsageRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        initialize_database(settings)
        cleanup_legacy_usage_json(settings.data_dir)

    def record_event(
        self,
        user_id: int,
        event_type: str,
        *,
        document_id: str | None = None,
        document_type: str = "",
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        created_at = created_at or datetime.now()
        with connect_database(self.settings) as connection:
            return _insert_event(
                connection,
                user_id=user_id,
                event_type=_normalize_event_type(event_type),
                document_id=document_id,
                document_type=document_type,
                created_at=created_at,
                metadata=metadata,
            )

    def update_event_document_type(self, event_id: int, document_type: str) -> None:
        with connect_database(self.settings) as connection:
            cursor = connection.execute(
                """
                update usage_events
                set document_type = ?
                where id = ? and event_type = ?
                """,
                (document_type, event_id, RECEIPT_ATTEMPT_EVENT),
            )
            if cursor.rowcount != 0:
                return
            row = connection.execute(
                """
                select 1
                from usage_events
                where id = ? and event_type = ?
                limit 1
                """,
                (event_id, RECEIPT_ATTEMPT_EVENT),
            ).fetchone()
            if row is None:
                raise ValueError(f"Usage event not found for id={event_id} and event_type={RECEIPT_ATTEMPT_EVENT}.")

    def count_events(
        self,
        user_id: int,
        event_type: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> int:
        with connect_database(self.settings) as connection:
            return _count_events(
                connection,
                user_id=user_id,
                event_type=_normalize_event_type(event_type),
                start_at=start_at,
                end_at=end_at,
            )

    def record_attempt_if_allowed(
        self,
        user_id: int,
        *,
        daily_limit: int,
        monthly_limit: int,
        document_type: str = "",
        created_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UsageAttemptResult:
        created_at = created_at or datetime.now()
        day_start, day_end = _day_bounds(created_at)
        month_start, month_end = _month_bounds(created_at)

        with connect_database(self.settings) as connection:
            connection.execute("begin immediate")
            daily_used = (
                _count_events(
                    connection,
                    user_id=user_id,
                    event_type=RECEIPT_ATTEMPT_EVENT,
                    start_at=day_start,
                    end_at=day_end,
                )
                if daily_limit
                else 0
            )
            monthly_used = (
                _count_events(
                    connection,
                    user_id=user_id,
                    event_type=RECEIPT_ATTEMPT_EVENT,
                    start_at=month_start,
                    end_at=month_end,
                )
                if monthly_limit
                else 0
            )
            if daily_limit and daily_used >= daily_limit:
                return UsageAttemptResult(
                    allowed=False,
                    reason="daily_limit",
                    event_id=None,
                    daily_used=daily_used,
                    daily_limit=daily_limit,
                    monthly_used=monthly_used,
                    monthly_limit=monthly_limit,
                )
            if monthly_limit and monthly_used >= monthly_limit:
                return UsageAttemptResult(
                    allowed=False,
                    reason="monthly_limit",
                    event_id=None,
                    daily_used=daily_used,
                    daily_limit=daily_limit,
                    monthly_used=monthly_used,
                    monthly_limit=monthly_limit,
                )
            event_id = _insert_event(
                connection,
                user_id=user_id,
                event_type=RECEIPT_ATTEMPT_EVENT,
                document_type=document_type,
                created_at=created_at,
                metadata=metadata,
            )
            return UsageAttemptResult(
                allowed=True,
                event_id=event_id,
                daily_used=daily_used,
                daily_limit=daily_limit,
                monthly_used=monthly_used,
                monthly_limit=monthly_limit,
            )


def cleanup_legacy_usage_json(data_dir: Path) -> None:
    usage_root = data_dir / "usage"
    if not usage_root.exists() or not usage_root.is_dir() or usage_root.is_symlink():
        return
    _cleanup_usage_dir(usage_root)
    try:
        usage_root.rmdir()
    except OSError:
        pass


def _cleanup_usage_dir(path: Path) -> None:
    for child in list(path.iterdir()):
        if child.is_symlink():
            continue
        if child.is_dir():
            _cleanup_usage_dir(child)
            try:
                child.rmdir()
            except OSError:
                pass
            continue
        if child.is_file() and child.suffix == ".json":
            try:
                child.unlink()
            except OSError:
                pass


def _insert_event(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    event_type: str,
    document_id: str | None = None,
    document_type: str = "",
    created_at: datetime,
    metadata: dict[str, Any] | None = None,
) -> int:
    cursor = connection.execute(
        """
        insert into usage_events(
            telegram_user_id,
            event_type,
            document_id,
            document_type,
            created_at,
            metadata_json
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            event_type,
            document_id,
            document_type,
            created_at.isoformat(),
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    )
    return int(cursor.lastrowid)


def _count_events(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    event_type: str,
    start_at: datetime,
    end_at: datetime,
) -> int:
    row = connection.execute(
        """
        select count(*)
        from usage_events
        where telegram_user_id = ?
          and event_type = ?
          and created_at >= ?
          and created_at < ?
        """,
        (user_id, event_type, start_at.isoformat(), end_at.isoformat()),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _normalize_event_type(event_type: str) -> str:
    if event_type == LEGACY_RECEIPT_ACTION:
        return RECEIPT_ATTEMPT_EVENT
    return event_type


def _day_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime(value.year, value.month, value.day)
    return start, start + timedelta(days=1)


def _month_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime(value.year, value.month, 1)
    if value.month == 12:
        end = datetime(value.year + 1, 1, 1)
    else:
        end = datetime(value.year, value.month + 1, 1)
    return start, end
