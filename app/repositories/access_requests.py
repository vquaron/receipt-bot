from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.users.models import AccessRequest


class AccessRequestRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        initialize_database(settings)

    def pending_request(self, user_id: int) -> AccessRequest | None:
        return self._request_by_status(user_id, "pending")

    def rejected_request(self, user_id: int) -> AccessRequest | None:
        return self._request_by_status(user_id, "rejected")

    def save_pending_request(self, request: AccessRequest) -> None:
        if self.pending_request(request.user_id) is not None:
            return
        self._insert_request(request, "pending")

    def resolve_pending_request(
        self,
        user_id: int,
        *,
        status: str,
        resolved_by: int | None = None,
        decision_reason: str = "",
    ) -> AccessRequest | None:
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select id, telegram_user_id, full_name, username, created_at
                from access_requests
                where telegram_user_id = ? and status = 'pending'
                order by created_at desc
                limit 1
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                update access_requests
                set status = ?, resolved_at = ?, resolved_by = ?, decision_reason = ?
                where id = ?
                """,
                (status, datetime.now().isoformat(), resolved_by, decision_reason, row["id"]),
            )
            return _request_from_row(row)

    def save_rejected_request(self, request: AccessRequest, *, resolved_by: int | None = None) -> None:
        self._insert_request(request, "rejected", resolved_at=datetime.now(), resolved_by=resolved_by)

    def load_requests(self) -> dict[str, dict[str, AccessRequest]]:
        result: dict[str, dict[str, AccessRequest]] = {"pending": {}, "rejected": {}}
        with connect_database(self.settings) as connection:
            rows = connection.execute(
                """
                select telegram_user_id, full_name, username, created_at, status
                from access_requests
                where status in ('pending', 'rejected')
                order by created_at
                """
            ).fetchall()
        for row in rows:
            status = str(row["status"])
            result[status][str(row["telegram_user_id"])] = _request_from_row(row)
        return result

    def _request_by_status(self, user_id: int, status: str) -> AccessRequest | None:
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select telegram_user_id, full_name, username, created_at
                from access_requests
                where telegram_user_id = ? and status = ?
                order by created_at desc
                limit 1
                """,
                (user_id, status),
            ).fetchone()
        return _request_from_row(row) if row is not None else None

    def _insert_request(
        self,
        request: AccessRequest,
        status: str,
        *,
        resolved_at: datetime | None = None,
        resolved_by: int | None = None,
    ) -> None:
        with connect_database(self.settings) as connection:
            connection.execute(
                """
                insert into access_requests(
                    id,
                    telegram_user_id,
                    username,
                    full_name,
                    status,
                    created_at,
                    resolved_at,
                    resolved_by
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    request.user_id,
                    request.username,
                    request.full_name,
                    status,
                    request.created_at.isoformat(),
                    resolved_at.isoformat() if resolved_at else None,
                    resolved_by,
                ),
            )


def _request_from_row(row) -> AccessRequest:
    return AccessRequest(
        user_id=int(row["telegram_user_id"]),
        full_name=str(row["full_name"] or ""),
        username=str(row["username"] or ""),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
