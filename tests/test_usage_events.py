from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.db.connection import connect_database
from app.repositories.usage import RECEIPT_ATTEMPT_EVENT
from app.users.models import UserRole
from app.users.quotas import QuotaService


def settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path,
        "data_dir": tmp_path / "data",
        "admin_telegram_user_ids": frozenset({111}),
        "allowed_telegram_user_ids": frozenset({222}),
        "privileged_telegram_user_ids": frozenset({333}),
        "regular_daily_receipt_limit": 1,
        "regular_monthly_receipt_limit": 10,
    }
    values.update(overrides)
    return Settings(**values)


def test_regular_attempt_is_recorded_and_denied_attempt_is_not_recorded(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    quota = QuotaService(app_settings)
    now = datetime(2026, 5, 22, 10, 0, 0)

    first = quota.check_and_record_attempt(222, UserRole.REGULAR, document_type="receipt", now=now)
    assert first.allowed
    assert _event_count(app_settings, 222) == 1

    denied = quota.check_and_record_attempt(222, UserRole.REGULAR, document_type="receipt", now=now)
    assert not denied.allowed
    assert denied.reason == "daily_limit"
    assert denied.daily_used == 1
    assert _event_count(app_settings, 222) == 1


def test_monthly_limit_counts_events_across_days(tmp_path: Path) -> None:
    app_settings = settings(tmp_path, regular_daily_receipt_limit=10, regular_monthly_receipt_limit=1)
    quota = QuotaService(app_settings)

    assert quota.check_and_record_attempt(222, UserRole.REGULAR, now=datetime(2026, 5, 20, 12, 0, 0)).allowed
    denied = quota.check_and_record_attempt(222, UserRole.REGULAR, now=datetime(2026, 5, 21, 12, 0, 0))

    assert not denied.allowed
    assert denied.reason == "monthly_limit"
    assert denied.monthly_used == 1
    assert _event_count(app_settings, 222) == 1


def test_unlimited_admin_and_privileged_attempts_are_recorded_for_audit(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    quota = QuotaService(app_settings)
    now = datetime(2026, 5, 22, 10, 0, 0)

    assert quota.check_and_record_attempt(111, UserRole.ADMIN, document_type="receipt", now=now).allowed
    assert quota.check_and_record_attempt(333, UserRole.PRIVILEGED, document_type="order", now=now).allowed

    assert _event_count(app_settings, 111) == 1
    assert _event_count(app_settings, 333) == 1
    assert _document_type(app_settings, 333) == "order"


def test_quota_storage_does_not_create_usage_json_files(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    quota = QuotaService(app_settings)

    quota.check_and_record_attempt(222, UserRole.REGULAR, now=datetime(2026, 5, 22, 10, 0, 0))

    assert not list((app_settings.data_dir / "usage").glob("**/*.json"))


def test_legacy_usage_json_cleanup_is_path_safe(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    usage_dir = app_settings.data_dir / "usage"
    old_month = usage_dir / "2026-05"
    old_month.mkdir(parents=True)
    old_json = old_month / "222.json"
    old_json.write_text("{}", encoding="utf-8")
    notes = old_month / "notes.txt"
    notes.write_text("keep", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_json = outside / "outside.json"
    outside_json.write_text("keep", encoding="utf-8")
    try:
        (usage_dir / "linked_outside").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available in this environment")

    QuotaService(app_settings)

    assert not old_json.exists()
    assert notes.exists()
    assert outside_json.exists()


def _event_count(app_settings: Settings, user_id: int) -> int:
    with connect_database(app_settings) as connection:
        row = connection.execute(
            """
            select count(*)
            from usage_events
            where telegram_user_id = ?
              and event_type = ?
            """,
            (user_id, RECEIPT_ATTEMPT_EVENT),
        ).fetchone()
    return int(row[0])


def _document_type(app_settings: Settings, user_id: int) -> str:
    with connect_database(app_settings) as connection:
        row = connection.execute(
            """
            select document_type
            from usage_events
            where telegram_user_id = ?
            order by id desc
            limit 1
            """,
            (user_id,),
        ).fetchone()
    return str(row["document_type"])

