from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.connection import connect_database
from app.obsidian.writer import export_receipt_note, write_receipt_note
from app.receipts.repository import ReceiptDeleteError, ReceiptRepository
from app.repositories.documents import (
    FILE_KIND_CLEAN_OCR,
    FILE_KIND_OBSIDIAN_ATTACHMENT,
    FILE_KIND_OBSIDIAN_NOTE,
    FILE_KIND_ORIGINAL_IMAGE,
    FILE_KIND_SOURCE_OCR,
    FILE_KIND_STORED_IMAGE,
    DOCUMENT_STATUS_STORAGE_FAILED,
    PARSED_SCHEMA_VERSION,
    PARSER_VERSION,
    PROMPT_VERSION,
    DocumentRepository,
    DocumentStorageError,
)
from app.review.models import ReceiptSession, SessionState
from app.storage.object_store import StoredObject
from app.storage.images import create_stored_image
from app.storage.sessions import SessionStore
from app.telegram.handlers import receipt as receipt_handler
import app.repositories.documents as documents_module


def test_confirmed_document_creates_db_rows_files_and_obsidian_export(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    session = _session(app_settings, user_id=222)
    result = DocumentRepository(app_settings).create_confirmed_from_session(session, _parsed_receipt())

    assert result.record.source == "db"
    assert result.record.receipt_id == "2026-05-20_zovq_supermarket_1234.5AMD"
    assert result.record.document_id
    assert session.image_path.exists()
    assert session.clean_ocr_path.exists()
    assert session.source_ocr_path.exists()

    document_root = app_settings.app_storage_dir / "documents" / result.record.document_id
    assert (document_root / "original.jpg").read_text(encoding="utf-8") == "image"
    assert (document_root / "stored.jpg").read_text(encoding="utf-8") == "image"
    assert (document_root / "clean.hy.txt").read_text(encoding="utf-8") == "clean ocr"
    assert (document_root / "source.hy.txt").read_text(encoding="utf-8") == "source ocr"

    note_path = tmp_path / "Users/222/Receipts/2026/05/2026-05-20_zovq_supermarket_1234.5AMD.md"
    attachment_path = tmp_path / "Users/222/Attachments/receipts/2026/05/2026-05-20_zovq_supermarket_1234.5AMD.jpg"
    assert note_path.exists()
    assert attachment_path.exists()
    assert not (tmp_path / "Users/222/MANIFEST").exists()
    assert "Attachments/receipts/2026/05/2026-05-20_zovq_supermarket_1234.5AMD.jpg" in note_path.read_text(encoding="utf-8")

    with connect_database(app_settings) as connection:
        document = connection.execute("select * from documents where id = ?", (result.record.document_id,)).fetchone()
        items = connection.execute("select * from document_items where document_id = ?", (result.record.document_id,)).fetchall()
        files = connection.execute(
            "select kind, path, storage_backend, storage_key, bucket, is_canonical, size_bytes, sha256 from document_files where document_id = ?",
            (result.record.document_id,),
        ).fetchall()

    assert document["status"] == "confirmed"
    assert document["owner_telegram_user_id"] == 222
    assert document["document_type"] == "receipt"
    assert document["parser_version"] == PARSER_VERSION
    assert document["schema_version"] == PARSED_SCHEMA_VERSION
    assert document["prompt_version"] == PROMPT_VERSION
    parsed_json = json.loads(document["parsed_json"])
    review_payload = json.loads(document["review_payload_json"])
    possible_errors = json.loads(document["possible_errors_json"])
    assert parsed_json["merchant"] == "Zovq Supermarket"
    assert review_payload["items"][0]["name_ru"] == "Пакет"
    assert possible_errors == ["amount: проверить сумму"]
    assert [(item["position"], item["name_ru"], item["possible_error"]) for item in items] == [(1, "Пакет", None)]
    assert {file["kind"] for file in files} == {
        FILE_KIND_ORIGINAL_IMAGE,
        FILE_KIND_STORED_IMAGE,
        FILE_KIND_CLEAN_OCR,
        FILE_KIND_SOURCE_OCR,
        FILE_KIND_OBSIDIAN_NOTE,
        FILE_KIND_OBSIDIAN_ATTACHMENT,
    }
    assert all(file["size_bytes"] > 0 and file["sha256"] for file in files)
    assert {file["storage_backend"] for file in files if file["is_canonical"]} == {"local"}
    assert all(file["storage_key"] == file["path"] for file in files)


def test_file_stem_suffixes_per_user_and_can_repeat_across_users(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    repository = DocumentRepository(app_settings)

    first = repository.create_confirmed_from_session(_session(app_settings, user_id=222, session_id="one"), _parsed_receipt())
    second = repository.create_confirmed_from_session(_session(app_settings, user_id=222, session_id="two"), _parsed_receipt())
    other_user = repository.create_confirmed_from_session(_session(app_settings, user_id=333, session_id="three"), _parsed_receipt())

    assert first.record.receipt_id == "2026-05-20_zovq_supermarket_1234.5AMD"
    assert second.record.receipt_id == "2026-05-20_zovq_supermarket_1234.5AMD_2"
    assert other_user.record.receipt_id == "2026-05-20_zovq_supermarket_1234.5AMD"


def test_receipt_repository_lists_and_finds_db_documents_before_manifest_fallback(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)

    listed = repository.list_user_receipts(222)
    found = repository.find_user_receipt(222, created.record.receipt_id)

    assert listed[0].source == "db"
    assert listed[0].receipt_id == created.record.receipt_id
    assert found is not None
    assert found.document_id == created.record.document_id
    image_file = next(file for file in found.file_records if file.kind == FILE_KIND_ORIGINAL_IMAGE)
    assert repository.file_path(image_file).read_text(encoding="utf-8") == "image"


def test_delete_db_document_removes_files_and_soft_deletes_row(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)
    files = [repository.file_path(file) for file in created.record.file_records]

    result = repository.delete_receipt(created.record.receipt_id, owner_user_id=222)

    assert result.source == "db"
    assert result.receipt_id == created.record.receipt_id
    assert len(result.deleted) == len(files)
    assert result.missing == []
    assert all(not path.exists() for path in files)
    assert repository.list_user_receipts(222) == []
    with connect_database(app_settings) as connection:
        document = connection.execute("select status, deleted_at from documents where id = ?", (created.record.document_id,)).fetchone()
        file_count = connection.execute("select count(*) from document_files where document_id = ?", (created.record.document_id,)).fetchone()[0]
    assert document["status"] == "deleted"
    assert document["deleted_at"]
    assert file_count == len(files)


def test_delete_db_document_counts_missing_files(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)
    missing_file = next(file for file in created.record.file_records if file.kind == FILE_KIND_CLEAN_OCR)
    repository.file_path(missing_file).unlink()

    result = repository.delete_receipt(created.record.document_id, owner_user_id=111, allow_all_users=True)

    assert len(result.missing) == 1
    assert result.missing[0].name == "clean.hy.txt"
    with connect_database(app_settings) as connection:
        document = connection.execute("select status from documents where id = ?", (created.record.document_id,)).fetchone()
    assert document["status"] == "deleted"


def test_delete_db_document_rejects_unsafe_path_before_deleting(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)
    existing_paths = [repository.file_path(file) for file in created.record.file_records]
    with connect_database(app_settings) as connection:
        connection.execute(
            """
            update document_files
            set path = ?
            where document_id = ? and kind = ?
            """,
            ("../escape.jpg", created.record.document_id, FILE_KIND_ORIGINAL_IMAGE),
        )

    with pytest.raises(ReceiptDeleteError) as exc_info:
        repository.delete_receipt(created.record.receipt_id, owner_user_id=222)

    assert "../escape.jpg" in str(exc_info.value)
    assert all(path.exists() for path in existing_paths)
    with connect_database(app_settings) as connection:
        document = connection.execute("select deleted_at from documents where id = ?", (created.record.document_id,)).fetchone()
    assert document["deleted_at"] is None


def test_delete_legacy_receipt_returns_vault_relative_note_path(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    legacy_artifact = write_receipt_note(app_settings, _legacy_session(tmp_path, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)

    result = repository.delete_receipt(legacy_artifact.receipt_id, owner_user_id=222)

    assert result.source == "legacy"
    assert result.note_path == legacy_artifact.note_path.relative_to(app_settings.obsidian_vault)


def test_non_admin_cannot_delete_another_users_db_document(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)
    original_file = next(file for file in created.record.file_records if file.kind == FILE_KIND_ORIGINAL_IMAGE)

    with pytest.raises(ReceiptDeleteError):
        repository.delete_receipt(created.record.document_id, owner_user_id=333)

    assert repository.file_path(original_file).exists()
    assert repository.find_user_receipt(222, created.record.receipt_id) is not None


def test_admin_global_delete_requires_document_id_for_ambiguous_file_stem(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    first = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222, session_id="one"), _parsed_receipt())
    second = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=333, session_id="two"), _parsed_receipt())
    repository = ReceiptRepository(app_settings)

    with pytest.raises(ReceiptDeleteError):
        repository.delete_receipt(first.record.receipt_id, owner_user_id=111, allow_all_users=True)

    result = repository.delete_receipt(second.record.document_id, owner_user_id=111, allow_all_users=True)
    assert result.document_id == second.record.document_id
    assert repository.find_user_receipt(333, second.record.receipt_id) is None
    assert repository.find_user_receipt(222, first.record.receipt_id) is not None


def test_copy_db_document_deep_copies_rows_and_files(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    source = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)

    copied = repository.copy_receipt_to_user(source.record.document_id, 333)

    assert copied.source == "db"
    assert copied.owner_user_id == 333
    assert copied.document_id != source.record.document_id
    assert copied.receipt_id == source.record.receipt_id
    assert copied.note_rel.parts[:3] == ("Users", "333", "Receipts")
    source_original = repository.file_path(next(file for file in source.record.file_records if file.kind == FILE_KIND_ORIGINAL_IMAGE))
    copied_original = repository.file_path(next(file for file in copied.file_records if file.kind == FILE_KIND_ORIGINAL_IMAGE))
    assert source_original != copied_original
    assert copied_original.read_text(encoding="utf-8") == "image"
    with connect_database(app_settings) as connection:
        source_doc = connection.execute("select parsed_json, parser_version from documents where id = ?", (source.record.document_id,)).fetchone()
        copied_doc = connection.execute("select parsed_json, parser_version from documents where id = ?", (copied.document_id,)).fetchone()
        copied_items = connection.execute("select name_ru from document_items where document_id = ?", (copied.document_id,)).fetchall()
    assert copied_doc["parsed_json"] == source_doc["parsed_json"]
    assert copied_doc["parser_version"] == source_doc["parser_version"]
    assert [row["name_ru"] for row in copied_items] == ["Пакет"]

    repository.delete_receipt(source.record.document_id, owner_user_id=111, allow_all_users=True)
    assert copied_original.exists()
    assert repository.find_user_receipt(333, copied.receipt_id) is not None


def test_copy_db_document_suffixes_target_file_stem_on_collision(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    repository = ReceiptRepository(app_settings)
    source = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222, session_id="source"), _parsed_receipt())
    DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=333, session_id="existing"), _parsed_receipt())

    copied = repository.copy_receipt_to_user(source.record.document_id, 333)

    assert copied.receipt_id == f"{source.record.receipt_id}_2"


def test_export_user_receipts_includes_legacy_and_db_canonical_files(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    legacy_session = _legacy_session(tmp_path, user_id=222)
    legacy_artifact = write_receipt_note(app_settings, legacy_session, _parsed_receipt())
    db_created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt(merchant="DB Store"))

    archive_path = ReceiptRepository(app_settings).export_user_receipts(222)

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "Receipts/2026/05/" + legacy_artifact.file_name in names
    assert f"Canonical/{db_created.record.receipt_id}/original.jpg" in names
    assert f"Canonical/{db_created.record.receipt_id}/stored.jpg" in names
    assert f"Canonical/{db_created.record.receipt_id}/clean.hy.txt" in names
    assert f"Canonical/{db_created.record.receipt_id}/source.hy.txt" in names


def test_export_user_receipts_uses_configured_export_storage_dir(tmp_path: Path) -> None:
    export_root = tmp_path / "custom-exports"
    app_settings = _settings(tmp_path, export_storage_dir=export_root)
    DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())

    archive_path = ReceiptRepository(app_settings).export_user_receipts(222)

    assert archive_path.is_relative_to(export_root)
    assert not (app_settings.data_dir / "exports").exists()


def test_export_user_receipts_includes_canonical_when_obsidian_export_missing(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(tmp_path)

    def _boom(**kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr("app.repositories.documents.export_receipt_note", _boom)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())

    archive_path = ReceiptRepository(app_settings).export_user_receipts(222)

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert f"Canonical/{created.record.receipt_id}/original.jpg" in names
    assert f"Canonical/{created.record.receipt_id}/stored.jpg" in names
    assert not any(name.startswith("Receipts/") for name in names)


def test_s3_image_storage_creates_storage_refs_and_materializes(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(
        tmp_path,
        storage_image_backend="s3",
        s3_bucket_name="receipts",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="key-id",
        s3_secret_access_key="secret",
    )
    fake_store = _FakeImageStore(bucket="receipts")
    monkeypatch.setattr("app.repositories.documents.image_storage", lambda settings: fake_store)

    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    image_files = {file.kind: file for file in created.record.file_records if file.kind in {FILE_KIND_ORIGINAL_IMAGE, FILE_KIND_STORED_IMAGE}}

    assert image_files[FILE_KIND_ORIGINAL_IMAGE].storage_backend == "s3"
    assert image_files[FILE_KIND_ORIGINAL_IMAGE].bucket == "receipts"
    assert image_files[FILE_KIND_ORIGINAL_IMAGE].storage_key.startswith("receipt-bot/documents/")
    assert image_files[FILE_KIND_STORED_IMAGE].storage_key.endswith("/stored.jpg")
    materialized = ReceiptRepository(app_settings).materialize_file(
        image_files[FILE_KIND_STORED_IMAGE],
        app_settings.tmp_storage_dir / "materialized-test",
    )
    assert materialized.read_bytes() == b"image"


def test_s3_storage_failure_marks_document_failed_and_keeps_temp(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(
        tmp_path,
        storage_image_backend="s3",
        s3_bucket_name="receipts",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="key-id",
        s3_secret_access_key="secret",
    )
    fake_store = _FailingImageStore(bucket="receipts")
    monkeypatch.setattr("app.repositories.documents.image_storage", lambda settings: fake_store)
    session = _session(app_settings, user_id=222)

    with pytest.raises(DocumentStorageError):
        DocumentRepository(app_settings).create_confirmed_from_session(session, _parsed_receipt())

    assert session.image_path.exists()
    with connect_database(app_settings) as connection:
        row = connection.execute("select status from documents").fetchone()
    assert row["status"] == DOCUMENT_STATUS_STORAGE_FAILED


def test_confirmed_document_storage_failure_cleans_local_artifacts(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(tmp_path)
    session = _session(app_settings, user_id=222)
    original_insert_local_file = documents_module._insert_local_file

    def _failing_insert_local_file(connection, document_id, kind, root, absolute_path, *, created_at, is_canonical=False):
        if kind == FILE_KIND_SOURCE_OCR:
            raise sqlite3.OperationalError("insert failed")
        return original_insert_local_file(
            connection,
            document_id,
            kind,
            root,
            absolute_path,
            created_at=created_at,
            is_canonical=is_canonical,
        )

    monkeypatch.setattr(documents_module, "_insert_local_file", _failing_insert_local_file)

    with pytest.raises(DocumentStorageError):
        DocumentRepository(app_settings).create_confirmed_from_session(session, _parsed_receipt())

    with connect_database(app_settings) as connection:
        row = connection.execute("select id, status from documents").fetchone()
    assert row["status"] == DOCUMENT_STATUS_STORAGE_FAILED
    assert not (app_settings.app_storage_dir / "documents" / row["id"]).exists()
    assert not (session.image_path.parent / "stored.jpg").exists()


def test_s3_copy_export_and_delete_use_object_storage(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(
        tmp_path,
        storage_image_backend="s3",
        s3_bucket_name="receipts",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="key-id",
        s3_secret_access_key="secret",
    )
    fake_store = _FakeImageStore(bucket="receipts")
    monkeypatch.setattr("app.repositories.documents.image_storage", lambda settings: fake_store)
    source = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    repository = ReceiptRepository(app_settings)

    copied = repository.copy_receipt_to_user(source.record.document_id, 333)
    copied_original = next(file for file in copied.file_records if file.kind == FILE_KIND_ORIGINAL_IMAGE)
    source_original = next(file for file in source.record.file_records if file.kind == FILE_KIND_ORIGINAL_IMAGE)
    assert copied_original.storage_key != source_original.storage_key
    assert copied_original.storage_key in fake_store.objects

    archive_path = repository.export_user_receipts(333)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert f"Canonical/{copied.receipt_id}/original.jpg" in names
    assert f"Canonical/{copied.receipt_id}/stored.jpg" in names
    assert not any((app_settings.tmp_storage_dir / "exports").glob("*"))

    repository.delete_receipt(source.record.document_id, owner_user_id=111, allow_all_users=True)
    assert source_original.storage_key in fake_store.deleted


def test_materialize_file_rejects_unsafe_s3_storage_key(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(
        tmp_path,
        storage_image_backend="s3",
        s3_bucket_name="receipts",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="key-id",
        s3_secret_access_key="secret",
    )
    fake_store = _FakeImageStore(bucket="receipts")
    monkeypatch.setattr("app.repositories.documents.image_storage", lambda settings: fake_store)
    created = DocumentRepository(app_settings).create_confirmed_from_session(_session(app_settings, user_id=222), _parsed_receipt())
    stored = next(file for file in created.record.file_records if file.kind == FILE_KIND_STORED_IMAGE)

    with pytest.raises(DocumentStorageError):
        ReceiptRepository(app_settings).materialize_file(
            replace(stored, storage_key="../escape.jpg"),
            app_settings.tmp_storage_dir / "materialized-test",
        )


def test_stored_image_is_optimized_and_exif_stripped(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    app_settings = _settings(
        tmp_path,
        storage_stored_image_max_edge_px=100,
        storage_stored_image_jpeg_quality=80,
    )
    source = tmp_path / "source.jpg"
    target = tmp_path / "stored.jpg"
    image = Image.new("RGB", (400, 200), color="white")
    exif = Image.Exif()
    exif[271] = "Camera"
    image.save(source, format="JPEG", exif=exif)

    create_stored_image(source, target, app_settings)

    with Image.open(target) as stored:
        assert max(stored.size) <= 100
        assert not stored.getexif()


def test_export_receipt_note_ignores_non_list_possible_errors(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    source_image_path = tmp_path / "source.jpg"
    source_image_path.write_text("image", encoding="utf-8")

    artifact = export_receipt_note(
        app_settings,
        user_id=222,
        file_stem="2026-05-20_test_1AMD",
        document_type="receipt",
        parsed={"date": "2026-05-20", "merchant": "Store", "amount": "1", "possible_errors": "bad-value"},
        source_image_path=source_image_path,
    )

    note_text = artifact.note_path.read_text(encoding="utf-8")
    assert "bad-value" not in note_text
    assert "## Возможные ошибки распознавания\n\n- Нет" in note_text


def test_telegram_finalize_persists_corrected_review_before_finishing_session(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path, web_base_url="https://app.finbot.uk")
    session_store = SessionStore(app_settings)
    repository = ReceiptRepository(app_settings)
    session = _session(app_settings, user_id=222)
    session.parsed_receipt = _parsed_receipt(merchant="Corrected Merchant")
    session.state = SessionState.WAITING_FOR_RUSSIAN_REVIEW
    session_store.save(session)
    receipt_handler.SESSIONS[222] = session
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": app_settings,
                "session_store": session_store,
                "receipt_repository": repository,
            }
        )
    )
    reply_target = _ReplyTarget()

    asyncio.run(receipt_handler.create_note_from_review(session, reply_target, context))

    message = reply_target.messages[0]
    assert "Готово: чек сохранён." in message
    assert "Открыть на сайте: https://app.finbot.uk/auth/magic?token=" in message
    assert "next=%2Freceipts%2F" in message
    assert "создана заметка" not in message
    assert ".md" not in message
    assert "receipt_id:" in message
    assert reply_target.replies[0][1]["link_preview_options"].is_disabled is True
    assert 222 not in receipt_handler.SESSIONS
    record = repository.list_user_receipts(222)[0]
    with connect_database(app_settings) as connection:
        document = connection.execute("select parsed_json from documents where id = ?", (record.document_id,)).fetchone()
        state = connection.execute("select state from processing_sessions where id = ?", (session.session_id,)).fetchone()
        link = connection.execute("select telegram_user_id, token_hash, used_at from magic_links").fetchone()
    assert json.loads(document["parsed_json"])["merchant"] == "Corrected Merchant"
    assert state["state"] == SessionState.DONE.value
    assert link["telegram_user_id"] == 222
    assert link["used_at"] is None


def test_telegram_finalize_without_web_base_url_omits_web_link(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    session_store = SessionStore(app_settings)
    repository = ReceiptRepository(app_settings)
    session = _session(app_settings, user_id=222)
    session.parsed_receipt = _parsed_receipt()
    session.state = SessionState.WAITING_FOR_RUSSIAN_REVIEW
    session_store.save(session)
    receipt_handler.SESSIONS[222] = session
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": app_settings,
                "session_store": session_store,
                "receipt_repository": repository,
            }
        )
    )
    reply_target = _ReplyTarget()

    asyncio.run(receipt_handler.create_note_from_review(session, reply_target, context))

    message = reply_target.messages[0]
    assert "Готово: чек сохранён." in message
    assert "Открыть на сайте:" not in message
    assert "создана заметка" not in message
    assert ".md" not in message
    with connect_database(app_settings) as connection:
        count = connection.execute("select count(*) from magic_links").fetchone()[0]
    assert count == 0


def test_confirmed_document_marks_export_failed_without_raising(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(tmp_path)
    session = _session(app_settings, user_id=222)

    def _boom(**kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr("app.repositories.documents.export_receipt_note", _boom)
    result = DocumentRepository(app_settings).create_confirmed_from_session(session, _parsed_receipt())

    assert result.artifact is None
    with connect_database(app_settings) as connection:
        document = connection.execute("select status from documents where id = ?", (result.record.document_id,)).fetchone()
    assert document["status"] == "export_failed"


def test_confirmed_document_ignores_non_list_possible_errors(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    session = _session(app_settings, user_id=222)
    result = DocumentRepository(app_settings).create_confirmed_from_session(
        session,
        {**_parsed_receipt(), "possible_errors": "bad-value"},
    )

    with connect_database(app_settings) as connection:
        document = connection.execute(
            "select possible_errors_json from documents where id = ?",
            (result.record.document_id,),
        ).fetchone()
    assert json.loads(document["possible_errors_json"]) == []


def test_telegram_finalize_reports_export_failure_but_marks_session_done(tmp_path: Path, monkeypatch) -> None:
    app_settings = _settings(tmp_path)
    session_store = SessionStore(app_settings)
    repository = ReceiptRepository(app_settings)
    session = _session(app_settings, user_id=222)
    session.parsed_receipt = _parsed_receipt(merchant="Corrected Merchant")
    session.state = SessionState.WAITING_FOR_RUSSIAN_REVIEW
    session_store.save(session)
    receipt_handler.SESSIONS[222] = session
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": app_settings,
                "session_store": session_store,
                "receipt_repository": repository,
            }
        )
    )
    reply_target = _ReplyTarget()

    def _boom(**kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr("app.repositories.documents.export_receipt_note", _boom)
    asyncio.run(receipt_handler.create_note_from_review(session, reply_target, context))

    assert any("Документ сохранён, но экспорт в Obsidian завершился ошибкой." in message for message in reply_target.messages)
    with connect_database(app_settings) as connection:
        state = connection.execute("select state from processing_sessions where id = ?", (session.session_id,)).fetchone()
    assert state["state"] == SessionState.DONE.value


class _ReplyTarget:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.messages.append(text)
        self.replies.append((text, kwargs))


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path,
        "data_dir": tmp_path / "data",
        "admin_telegram_user_ids": frozenset(),
        "allowed_telegram_user_ids": frozenset({222, 333}),
    }
    values.update(overrides)
    return Settings(
        **values,
    )


def _session(app_settings: Settings, *, user_id: int, session_id: str = "session") -> ReceiptSession:
    created_at = datetime.now()
    temp_dir = app_settings.tmp_storage_dir / "processing" / session_id
    image = temp_dir / "original.jpg"
    clean = temp_dir / "clean.hy.txt"
    source = temp_dir / "source.hy.txt"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_text("image", encoding="utf-8")
    clean.write_text("clean ocr", encoding="utf-8")
    source.write_text("source ocr", encoding="utf-8")
    return ReceiptSession(
        session_id=session_id,
        user_id=user_id,
        image_path=image,
        clean_ocr_path=clean,
        source_ocr_path=source,
        temporary_base_name="tmp",
        created_at=created_at,
    )


def _legacy_session(tmp_path: Path, *, user_id: int) -> ReceiptSession:
    created_at = datetime(2026, 5, 20, 12, 0, 0)
    image = tmp_path / f"Users/{user_id}/Attachments/receipts/_tmp/2026/05/tmp.jpg"
    clean = tmp_path / f"Users/{user_id}/OCR/2026/05/tmp.clean.hy.txt"
    source = tmp_path / f"Users/{user_id}/OCR_VERIFIED/2026/05/tmp.verified.hy.txt"
    for path in (image, clean, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("legacy", encoding="utf-8")
    return ReceiptSession(
        user_id=user_id,
        image_path=image,
        clean_ocr_path=clean,
        source_ocr_path=source,
        temporary_base_name="tmp",
        created_at=created_at,
    )


def _parsed_receipt(*, merchant: str = "Zovq Supermarket") -> dict[str, object]:
    return {
        "date": "2026-05-20",
        "time": "12:00:00",
        "merchant": merchant,
        "amount": "1234.50",
        "currency": "AMD",
        "category": "Grocery",
        "armenian_text": "",
        "russian_translation": "",
        "english_translation": "",
        "summary_ru": "Покупка",
        "items": [
            {
                "name_original": "տոպրակ",
                "name_ru": "Пакет",
                "name_en": "Bag",
                "unit_price": "20",
                "quantity": "1",
                "unit": "шт",
                "line_total": "20",
            }
        ],
        "possible_errors": ["amount: проверить сумму"],
    }


class _FakeImageStore:
    backend = "s3"

    def __init__(self, *, bucket: str) -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_file(self, source: Path, key: str, *, content_type: str = "") -> StoredObject:
        content = source.read_bytes()
        self.objects[key] = content
        return StoredObject(
            backend=self.backend,
            key=key,
            bucket=self.bucket,
            mime_type=content_type or "application/octet-stream",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            etag=f"etag-{len(self.objects)}",
        )

    def download_to(self, key: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.objects[key])

    def copy(self, source_key: str, target_key: str, *, content_type: str = "") -> StoredObject:
        self.objects[target_key] = self.objects[source_key]
        return StoredObject(
            backend=self.backend,
            key=target_key,
            bucket=self.bucket,
            mime_type=content_type or "application/octet-stream",
            size_bytes=len(self.objects[target_key]),
            sha256=hashlib.sha256(self.objects[target_key]).hexdigest(),
            etag=f"etag-{len(self.objects)}",
        )

    def delete_all_versions(self, key: str) -> bool:
        self.deleted.append(key)
        self.objects.pop(key, None)
        return True


class _FailingImageStore(_FakeImageStore):
    def put_file(self, source: Path, key: str, *, content_type: str = "") -> StoredObject:
        raise OSError("upload failed")
