from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.connection import connect_database
from app.repositories.documents import (
    DOCUMENT_STATUS_STORAGE_FAILED,
    FILE_KIND_CLEAN_OCR,
    FILE_KIND_STORED_IMAGE,
    STORAGE_BACKEND_LOCAL,
    STORAGE_BACKEND_S3,
    DocumentRepository,
)
from app.review.models import ReceiptSession
from app.storage.health import SEVERITY_ERROR, StorageHealthService
from app.storage.object_store import ObjectHead
from app.telegram.handlers.storage import storage_health_command


def test_storage_health_reports_no_errors_for_healthy_db_document(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings), _parsed_receipt())

    report = StorageHealthService(app_settings).check()

    assert report.error_count == 0


def test_storage_health_reports_missing_non_file_unsafe_and_mismatched_local_files(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings), _parsed_receipt())
    repository = DocumentRepository(app_settings)
    clean_file = next(file for file in created.record.file_records if file.kind == FILE_KIND_CLEAN_OCR)
    clean_path = repository.file_path(clean_file)
    clean_path.unlink()

    with connect_database(app_settings) as connection:
        connection.execute(
            "update document_files set path = ?, storage_key = ? where document_id = ? and kind = ?",
            ("../escape.txt", "../escape.txt", created.record.document_id, FILE_KIND_STORED_IMAGE),
        )

    report = StorageHealthService(app_settings).check()

    assert _has_issue(report, "missing_file", FILE_KIND_CLEAN_OCR)
    assert _has_issue(report, "unsafe_path", FILE_KIND_STORED_IMAGE)

    with connect_database(app_settings) as connection:
        connection.execute(
            "update document_files set path = ?, storage_key = ?, size_bytes = ?, sha256 = ? where document_id = ? and kind = ?",
            ("documents/non-file", "documents/non-file", 1, "bad-sha", created.record.document_id, FILE_KIND_CLEAN_OCR),
        )
    non_file = app_settings.app_storage_dir / "documents" / "non-file"
    non_file.mkdir(parents=True, exist_ok=True)

    report = StorageHealthService(app_settings).check()
    assert _has_issue(report, "non_file_target", FILE_KIND_CLEAN_OCR)

    non_file.rmdir()
    non_file.write_text("changed", encoding="utf-8")
    report = StorageHealthService(app_settings).check()
    assert _has_issue(report, "size_mismatch", FILE_KIND_CLEAN_OCR) or _has_issue(report, "sha256_mismatch", FILE_KIND_CLEAN_OCR)


def test_storage_health_reports_s3_missing_and_metadata_mismatch(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(
        tmp_path,
        storage_image_backend="s3",
        s3_bucket_name="receipts",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="key",
        s3_secret_access_key="secret",
    )
    fake_image_store = _FakeImageStore()
    monkeypatch.setattr("app.repositories.documents.image_storage", lambda settings: fake_image_store)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings), _parsed_receipt())
    stored = next(file for file in created.record.file_records if file.kind == FILE_KIND_STORED_IMAGE)

    fake_health_store = _FakeS3HeadStore(
        {
            stored.storage_key: ObjectHead(
                backend=STORAGE_BACKEND_S3,
                bucket="receipts",
                key=stored.storage_key,
                mime_type="image/jpeg",
                size_bytes=stored.size_bytes + 10,
                sha256="wrong",
            )
        }
    )
    monkeypatch.setattr("app.storage.health.S3Storage", lambda settings: fake_health_store)

    report = StorageHealthService(app_settings).check()

    assert _has_issue(report, "size_mismatch", FILE_KIND_STORED_IMAGE)
    assert _has_issue(report, "sha256_mismatch", FILE_KIND_STORED_IMAGE)

    fake_health_store.objects = {}
    report = StorageHealthService(app_settings).check()
    assert _has_issue(report, "s3_object_missing_or_unavailable", FILE_KIND_STORED_IMAGE)


def test_storage_health_reports_unknown_backend_and_unsafe_s3_key(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings), _parsed_receipt())
    with connect_database(app_settings) as connection:
        connection.execute(
            "update document_files set storage_backend = ?, storage_key = ? where document_id = ? and kind = ?",
            ("mystery", "somewhere", created.record.document_id, FILE_KIND_CLEAN_OCR),
        )
        connection.execute(
            "update document_files set storage_backend = ?, storage_key = ? where document_id = ? and kind = ?",
            (STORAGE_BACKEND_S3, "../escape.jpg", created.record.document_id, FILE_KIND_STORED_IMAGE),
        )

    report = StorageHealthService(app_settings).check()

    assert _has_issue(report, "unknown_storage_backend", FILE_KIND_CLEAN_OCR)
    assert _has_issue(report, "unsafe_s3_key", FILE_KIND_STORED_IMAGE)


def test_storage_health_reports_non_final_status_orphan_and_deleted_leftover(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings), _parsed_receipt())
    orphan = app_settings.app_storage_dir / "documents" / "orphan-doc" / "extra.txt"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("orphan", encoding="utf-8")
    clean_file = next(file for file in created.record.file_records if file.kind == FILE_KIND_CLEAN_OCR)
    clean_path = DocumentRepository(app_settings).file_path(clean_file)
    with connect_database(app_settings) as connection:
        connection.execute("update documents set status = ? where id = ?", (DOCUMENT_STATUS_STORAGE_FAILED, created.record.document_id))

    report = StorageHealthService(app_settings).check()
    assert _has_issue(report, "document_non_final_status", "")
    assert _has_issue(report, "orphan_app_file", "")

    with connect_database(app_settings) as connection:
        connection.execute("update documents set status = 'deleted', deleted_at = ? where id = ?", (datetime.now().isoformat(), created.record.document_id))
    clean_path.write_text("leftover", encoding="utf-8")

    report = StorageHealthService(app_settings).check()
    assert _has_issue(report, "deleted_file_leftover", FILE_KIND_CLEAN_OCR)
    assert not _has_error(report, "missing_file")


def test_storage_health_command_is_admin_only_and_renders_summary(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    context = _context(app_settings, admin=True)
    update = _update(user_id=111)

    asyncio.run(storage_health_command(update, context))

    assert any("Storage health:" in message for message in update.message.messages)

    non_admin = _update(user_id=222)
    asyncio.run(storage_health_command(non_admin, _context(app_settings, admin=False)))
    assert non_admin.message.messages == ["Команда доступна только администратору."]


def test_storage_health_command_handles_failures(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(tmp_path)
    update = _update(user_id=111)

    def _boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.storage.health.StorageHealthService.check", _boom)
    asyncio.run(storage_health_command(update, _context(app_settings, admin=True)))

    assert "Не удалось выполнить storage health check." in update.message.messages


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path / "vault",
        "data_dir": tmp_path / "data",
        "admin_telegram_user_ids": frozenset({111}),
        "allowed_telegram_user_ids": frozenset({222}),
    }
    values.update(overrides)
    values["obsidian_vault"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)


def _session(app_settings: Settings) -> ReceiptSession:
    temp_dir = app_settings.tmp_storage_dir / "processing" / "session"
    image = temp_dir / "original.jpg"
    clean = temp_dir / "clean.hy.txt"
    source = temp_dir / "source.hy.txt"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_text("image", encoding="utf-8")
    clean.write_text("clean ocr", encoding="utf-8")
    source.write_text("source ocr", encoding="utf-8")
    return ReceiptSession(
        session_id="session",
        user_id=222,
        image_path=image,
        clean_ocr_path=clean,
        source_ocr_path=source,
        temporary_base_name="tmp",
        created_at=datetime.now(),
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


def _has_issue(report, code: str, kind: str) -> bool:
    return any(issue.code == code and issue.file_kind == kind for issue in report.issues)


def _has_error(report, code: str) -> bool:
    return any(issue.severity == SEVERITY_ERROR and issue.code == code for issue in report.issues)


class _FakeImageStore:
    backend = "s3"
    bucket = "receipts"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_file(self, source: Path, key: str, *, content_type: str = ""):
        from app.storage.object_store import StoredObject

        content = source.read_bytes()
        self.objects[key] = content
        return StoredObject(
            backend=self.backend,
            key=key,
            bucket=self.bucket,
            mime_type=content_type,
            size_bytes=len(content),
            sha256="sha",
        )

    def delete_all_versions(self, key: str) -> bool:
        self.objects.pop(key, None)
        return True


class _FakeS3HeadStore:
    def __init__(self, objects: dict[str, ObjectHead]) -> None:
        self.objects = objects

    def head(self, key: str) -> ObjectHead:
        if key not in self.objects:
            raise RuntimeError("missing")
        return self.objects[key]


def _context(app_settings: Settings, *, admin: bool):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": app_settings,
                "access_control": SimpleNamespace(is_admin=lambda user_id: admin),
            }
        )
    )


def _update(*, user_id: int):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), message=_Message())


class _Message:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.messages.append(text)
