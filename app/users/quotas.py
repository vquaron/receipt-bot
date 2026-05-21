from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings
from app.repositories.usage import RECEIPT_ATTEMPT_EVENT, UsageRepository
from app.users.models import UserRole


class QuotaStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    reason: str = ""
    daily_used: int = 0
    daily_limit: int = 0
    monthly_used: int = 0
    monthly_limit: int = 0


class QuotaService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = UsageRepository(settings)

    def check(
        self,
        user_id: int,
        role: UserRole,
        *,
        action: str = RECEIPT_ATTEMPT_EVENT,
        now: datetime | None = None,
    ) -> QuotaDecision:
        daily_limit, monthly_limit = self._limits(role)
        if daily_limit == 0 and monthly_limit == 0:
            return QuotaDecision(allowed=True)
        now = now or datetime.now()
        try:
            daily_used = (
                self.repository.count_events(
                    user_id,
                    action,
                    start_at=datetime(now.year, now.month, now.day),
                    end_at=_next_day(now),
                )
                if daily_limit
                else 0
            )
            monthly_used = (
                self.repository.count_events(
                    user_id,
                    action,
                    start_at=datetime(now.year, now.month, 1),
                    end_at=_next_month(now),
                )
                if monthly_limit
                else 0
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise QuotaStorageError("Failed to read quota usage.") from exc
        if daily_limit and daily_used >= daily_limit:
            return QuotaDecision(False, "daily_limit", daily_used, daily_limit, monthly_used, monthly_limit)
        if monthly_limit and monthly_used >= monthly_limit:
            return QuotaDecision(False, "monthly_limit", daily_used, daily_limit, monthly_used, monthly_limit)
        return QuotaDecision(True, "", daily_used, daily_limit, monthly_used, monthly_limit)

    def check_and_record_attempt(
        self,
        user_id: int,
        role: UserRole,
        *,
        document_type: str = "",
        now: datetime | None = None,
    ) -> QuotaDecision:
        daily_limit, monthly_limit = self._limits(role)
        try:
            result = self.repository.record_attempt_if_allowed(
                user_id,
                daily_limit=daily_limit,
                monthly_limit=monthly_limit,
                document_type=document_type,
                created_at=now,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise QuotaStorageError("Failed to record quota usage.") from exc
        return QuotaDecision(
            allowed=result.allowed,
            reason=result.reason,
            daily_used=result.daily_used,
            daily_limit=result.daily_limit,
            monthly_used=result.monthly_used,
            monthly_limit=result.monthly_limit,
        )

    def record(self, user_id: int, *, action: str = RECEIPT_ATTEMPT_EVENT, now: datetime | None = None) -> None:
        try:
            self.repository.record_event(user_id, action, created_at=now)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise QuotaStorageError("Failed to record quota usage.") from exc

    def _limits(self, role: UserRole) -> tuple[int, int]:
        if role == UserRole.ADMIN:
            return 0, 0
        if role == UserRole.PRIVILEGED:
            return self.settings.privileged_daily_receipt_limit, self.settings.privileged_monthly_receipt_limit
        return self.settings.regular_daily_receipt_limit, self.settings.regular_monthly_receipt_limit


def _next_day(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day) + timedelta(days=1)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1)
    return datetime(value.year, value.month + 1, 1)
