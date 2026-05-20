from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.obsidian.delete import ReceiptDeleteError, delete_receipt
from app.obsidian.writer import write_receipt_note
from app.receipts.repository import ReceiptRepository
from app.review.models import ReceiptSession
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


def test_quota_limits_regular_users_but_not_admins(tmp_path: Path) -> None:
    quota = QuotaService(settings(tmp_path))
    now = datetime(2026, 5, 20, 12, 0, 0)
    assert quota.check(222, UserRole.REGULAR, now=now).allowed
    quota.record(222, now=now)
    decision = quota.check(222, UserRole.REGULAR, now=now)
    assert not decision.allowed
    assert decision.reason == "daily_limit"
    assert quota.check(111, UserRole.ADMIN, now=now).allowed


def test_receipt_note_is_written_under_user_root_and_indexed(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    session = _session(tmp_path, user_id=222)
    artifact = write_receipt_note(app_settings, session, _parsed_receipt())

    assert artifact.note_path.relative_to(tmp_path).parts[:3] == ("Users", "222", "Receipts")
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    assert manifest["owner_user_id"] == 222
    assert manifest["receipt_id"] == artifact.receipt_id
    assert manifest["note"].startswith("Users/222/Receipts/")

    records = ReceiptRepository(app_settings).list_user_receipts(222)
    assert [record.receipt_id for record in records] == [artifact.receipt_id]

    archive = ReceiptRepository(app_settings).export_user_receipts(222)
    with zipfile.ZipFile(archive) as zip_file:
        assert any(name.startswith("Receipts/") and name.endswith(".md") for name in zip_file.namelist())

    copied = ReceiptRepository(app_settings).copy_receipt_to_user(artifact.receipt_id, 333)
    assert copied.owner_user_id == 333
    assert copied.note_rel.parts[:3] == ("Users", "333", "Receipts")
    copied_note = (tmp_path / copied.note_rel).read_text(encoding="utf-8")
    assert "Users/333/Attachments/receipts/" in copied_note
    assert "Users/222/Attachments/receipts/" not in copied_note


def test_delete_receipt_rejects_manifest_files_outside_user_root(tmp_path: Path) -> None:
    note = tmp_path / "Users/222/Receipts/2026/05/a.md"
    manifest = tmp_path / "Users/222/MANIFEST/receipts/2026/05/a.manifest.json"
    foreign = tmp_path / "Users/333/secret.txt"
    for path in (note, manifest, foreign):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "Users/222/Receipts/2026/05/a.md",
                "files": ["Users/222/Receipts/2026/05/a.md", "Users/333/secret.txt"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReceiptDeleteError):
        delete_receipt(tmp_path, "a.md", owner_user_id=222)
    assert foreign.exists()


def _session(tmp_path: Path, *, user_id: int) -> ReceiptSession:
    created_at = datetime(2026, 5, 20, 12, 0, 0)
    image = tmp_path / f"Users/{user_id}/Attachments/receipts/_tmp/2026/05/tmp.jpg"
    clean = tmp_path / f"Users/{user_id}/OCR/2026/05/tmp.clean.hy.txt"
    source = tmp_path / f"Users/{user_id}/OCR_VERIFIED/2026/05/tmp.verified.hy.txt"
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
