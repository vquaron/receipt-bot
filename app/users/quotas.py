from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.config import Settings
from app.storage.paths import ensure_parent
from app.users.models import UserRole


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
        self.root = settings.data_dir / "usage"

    def check(self, user_id: int, role: UserRole, *, action: str = "receipt_process", now: datetime | None = None) -> QuotaDecision:
        daily_limit, monthly_limit = self._limits(role)
        if daily_limit == 0 and monthly_limit == 0:
            return QuotaDecision(allowed=True)
        now = now or datetime.now()
        usage = self._load(user_id, now.date())
        daily_used = _nested_count(usage, ["daily", now.date().isoformat(), action])
        monthly_used = _nested_count(usage, ["monthly", action])
        if daily_limit and daily_used >= daily_limit:
            return QuotaDecision(False, "daily_limit", daily_used, daily_limit, monthly_used, monthly_limit)
        if monthly_limit and monthly_used >= monthly_limit:
            return QuotaDecision(False, "monthly_limit", daily_used, daily_limit, monthly_used, monthly_limit)
        return QuotaDecision(True, "", daily_used, daily_limit, monthly_used, monthly_limit)

    def record(self, user_id: int, *, action: str = "receipt_process", now: datetime | None = None) -> None:
        now = now or datetime.now()
        usage = self._load(user_id, now.date())
        day = now.date().isoformat()
        usage.setdefault("user_id", user_id)
        usage.setdefault("period", f"{now:%Y-%m}")
        usage.setdefault("daily", {}).setdefault(day, {})
        usage.setdefault("monthly", {})
        usage["daily"][day][action] = int(usage["daily"][day].get(action, 0)) + 1
        usage["monthly"][action] = int(usage["monthly"].get(action, 0)) + 1
        self._save(user_id, now.date(), usage)

    def _limits(self, role: UserRole) -> tuple[int, int]:
        if role == UserRole.ADMIN:
            return 0, 0
        if role == UserRole.PRIVILEGED:
            return self.settings.privileged_daily_receipt_limit, self.settings.privileged_monthly_receipt_limit
        return self.settings.regular_daily_receipt_limit, self.settings.regular_monthly_receipt_limit

    def _path(self, user_id: int, day: date) -> Path:
        return self.root / f"{day:%Y-%m}" / f"{user_id}.json"

    def _load(self, user_id: int, day: date) -> dict[str, object]:
        path = self._path(user_id, day)
        if not path.exists():
            return {"user_id": user_id, "period": f"{day:%Y-%m}", "daily": {}, "monthly": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"user_id": user_id, "period": f"{day:%Y-%m}", "daily": {}, "monthly": {}}
        return data if isinstance(data, dict) else {"user_id": user_id, "period": f"{day:%Y-%m}", "daily": {}, "monthly": {}}

    def _save(self, user_id: int, day: date, data: dict[str, object]) -> None:
        path = self._path(user_id, day)
        ensure_parent(path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _nested_count(data: dict[str, object], keys: list[str]) -> int:
    current: object = data
    for key in keys:
        if not isinstance(current, dict):
            return 0
        current = current.get(key, 0)
    try:
        return int(current)
    except (TypeError, ValueError):
        return 0

