from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.db.connection import connect_database
from app.receipts.repository import ReceiptRepository
from app.repositories.documents import (
    FILE_KIND_CLEAN_OCR,
    FILE_KIND_OBSIDIAN_ATTACHMENT,
    FILE_KIND_OBSIDIAN_NOTE,
    FILE_KIND_ORIGINAL_IMAGE,
    FILE_KIND_SOURCE_OCR,
    PARSED_SCHEMA_VERSION,
    PARSER_VERSION,
    PROMPT_VERSION,
    DocumentRepository,
)
from app.review.models import ReceiptSession, SessionState
from app.storage.sessions import SessionStore
from app.telegram.handlers import receipt as receipt_handler


def test_confirmed_document_creates_db_rows_files_and_obsidian_export(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    session = _session(app_settings, user_id=222)
    result = DocumentRepository(app_settings).create_confirmed_from_session(session, _parsed_receipt())

    assert result.record.source == "db"
    assert result.record.receipt_id == "2026-05-20_zovq_supermarket_1234.5AMD"
    assert result.record.document_id
    assert not session.image_path.exists()
    assert not session.clean_ocr_path.exists()
    assert not session.source_ocr_path.exists()

    document_root = app_settings.app_storage_dir / "documents" / result.record.document_id
    assert (document_root / "original.jpg").read_text(encoding="utf-8") == "image"
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
        files = connection.execute("select kind, path, size_bytes, sha256 from document_files where document_id = ?", (result.record.document_id,)).fetchall()

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
        FILE_KIND_CLEAN_OCR,
        FILE_KIND_SOURCE_OCR,
        FILE_KIND_OBSIDIAN_NOTE,
        FILE_KIND_OBSIDIAN_ATTACHMENT,
    }
    assert all(file["size_bytes"] > 0 and file["sha256"] for file in files)


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


def test_telegram_finalize_persists_corrected_review_before_finishing_session(tmp_path: Path) -> None:
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

    asyncio.run(receipt_handler.create_note_from_review(session, reply_target, context))

    assert any("receipt_id:" in message for message in reply_target.messages)
    assert 222 not in receipt_handler.SESSIONS
    record = repository.list_user_receipts(222)[0]
    with connect_database(app_settings) as connection:
        document = connection.execute("select parsed_json from documents where id = ?", (record.document_id,)).fetchone()
        state = connection.execute("select state from processing_sessions where id = ?", (session.session_id,)).fetchone()
    assert json.loads(document["parsed_json"])["merchant"] == "Corrected Merchant"
    assert state["state"] == SessionState.DONE.value


class _ReplyTarget:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.messages.append(text)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="token",
        openai_api_key="key",
        obsidian_vault=tmp_path,
        data_dir=tmp_path / "data",
        admin_telegram_user_ids=frozenset(),
        allowed_telegram_user_ids=frozenset({222, 333}),
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
