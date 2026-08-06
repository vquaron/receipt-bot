from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.receipts.repository import ReceiptRepository
from app.repositories.documents import DocumentRepository
from app.review.models import ReceiptSession
from app.telegram.handlers.receipt import _quota_message
from app.telegram.handlers.receipts import _cleanup_materialized_tmp_file
from app.users.access_service import AccessControl
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
        "regular_monthly_receipt_limit": 2,
    }
    values.update(overrides)
    return Settings(**values)


def test_access_bootstraps_roles(tmp_path: Path) -> None:
    access = AccessControl(settings(tmp_path))
    assert access.role_for(111) == UserRole.ADMIN
    assert access.role_for(222) == UserRole.REGULAR
    assert access.role_for(333) == UserRole.PRIVILEGED
    assert access.is_allowed(333)


def test_revoked_env_user_stays_revoked_after_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    access = AccessControl(app_settings)

    assert access.revoke(222)
    assert not access.is_allowed(222)

    restarted_access = AccessControl(app_settings)
    assert not restarted_access.is_allowed(222)
    assert restarted_access.is_allowed(111)


def test_quota_limits_regular_users_but_not_admins(tmp_path: Path) -> None:
    quota = QuotaService(settings(tmp_path))
    now = datetime(2026, 5, 20, 12, 0, 0)
    assert quota.check(222, UserRole.REGULAR, now=now).allowed
    quota.record(222, now=now)
    decision = quota.check(222, UserRole.REGULAR, now=now)
    assert not decision.allowed
    assert decision.reason == "daily_limit"
    assert quota.check(111, UserRole.ADMIN, now=now).allowed


def test_quota_message_describes_attempt_limit() -> None:
    assert "лимит попыток" in _quota_message("daily_limit", 1, 1, 1, 10)
    assert "лимит попыток" in _quota_message("monthly_limit", 10, 20, 20, 20)


def test_db_receipt_export_is_written_under_user_root_and_indexed(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())

    assert created.record.note_rel.parts[:3] == ("Users", "222", "Receipts")
    assert not (tmp_path / "Users/222/MANIFEST").exists()
    assert not (tmp_path / "Users/222/OCR").exists()
    assert not (tmp_path / "Users/222/OCR_VERIFIED").exists()

    records = ReceiptRepository(app_settings).list_user_receipts(222)
    assert [record.receipt_id for record in records] == [created.record.receipt_id]

    archive = ReceiptRepository(app_settings).export_user_receipts(222)
    with zipfile.ZipFile(archive) as zip_file:
        assert any(name.startswith("Receipts/") and name.endswith(".md") for name in zip_file.namelist())

    copied = ReceiptRepository(app_settings).copy_receipt_to_user(created.record.document_id, 333)
    assert copied.owner_user_id == 333
    assert copied.note_rel.parts[:3] == ("Users", "333", "Receipts")
    copied_note = (tmp_path / copied.note_rel).read_text(encoding="utf-8")
    assert "Users/333/Attachments/receipts/" in copied_note
    assert "Users/222/Attachments/receipts/" not in copied_note


def test_cleanup_materialized_tmp_file_removes_only_tmp_paths(tmp_path: Path) -> None:
    tmp_root = tmp_path / "data" / "tmp"
    materialized = tmp_root / "telegram" / "222" / "receipt-1" / "stored.jpg"
    materialized.parent.mkdir(parents=True, exist_ok=True)
    materialized.write_text("image", encoding="utf-8")
    outside = tmp_path / "Users" / "222" / "Attachments" / "stored.jpg"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("image", encoding="utf-8")

    _cleanup_materialized_tmp_file(materialized, tmp_root)
    _cleanup_materialized_tmp_file(outside, tmp_root)

    assert not materialized.exists()
    assert not (tmp_root / "telegram" / "222" / "receipt-1").exists()
    assert outside.exists()


def _session(app_settings: Settings, *, user_id: int) -> ReceiptSession:
    created_at = datetime.now()
    temp_dir = app_settings.tmp_storage_dir / "processing" / f"session-{user_id}"
    image = temp_dir / "original.jpg"
    clean = temp_dir / "clean.hy.txt"
    source = temp_dir / "source.hy.txt"
    for path in (image, clean, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return ReceiptSession(
        user_id=user_id,
        image_path=image,
        clean_ocr_path=clean,
        source_ocr_path=source,
        temporary_base_name="tmp",
        created_at=created_at,
    )


def _parsed_receipt() -> dict[str, object]:
    return {
        "date": "2026-05-20",
        "time": "12:00:00",
        "merchant": "Zovq Supermarket",
        "amount": "1234.50",
        "currency": "AMD",
        "category": "Grocery",
        "summary_ru": "Покупка",
        "items": [],
        "possible_errors": [],
    }
