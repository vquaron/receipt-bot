import json
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.llm.openai_parser import ParsedReceipt
from app.obsidian.writer import write_receipt_note
from app.pipeline import receipt_pipeline
from app.receipts.document_classifier import classify_document_type
from app.receipts.document_types import DOCUMENT_TYPE_ORDER, DOCUMENT_TYPE_RECEIPT
from app.review.models import ReceiptSession
from app.storage.corrections import CorrectionStore


def test_parse_for_review_passes_order_document_type(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_parse_receipt_text(ocr_text: str, *, api_key: str, model: str, document_type: str) -> ParsedReceipt:
        captured["ocr_text"] = ocr_text
        captured["document_type"] = document_type
        return ParsedReceipt(
            data={
                "date": "",
                "time": "",
                "merchant": "",
                "amount": "",
                "currency": "AMD",
                "category": "",
                "armenian_text": "",
                "russian_translation": "",
                "english_translation": "",
                "summary_ru": "",
                "items": [],
                "possible_errors": [],
            },
            raw_response="{}",
        )

    monkeypatch.setattr(receipt_pipeline, "parse_receipt_text", fake_parse_receipt_text)
    result = receipt_pipeline.parse_for_review(
        "order screenshot OCR",
        settings=_settings(tmp_path),
        correction_store=CorrectionStore(_settings(tmp_path)),
        document_type=DOCUMENT_TYPE_ORDER,
    )

    assert captured == {"ocr_text": "order screenshot OCR", "document_type": DOCUMENT_TYPE_ORDER}
    assert result.data["currency"] == "AMD"


def test_receipt_session_document_type_persists_and_defaults(tmp_path: Path) -> None:
    session = _session(tmp_path, document_type=DOCUMENT_TYPE_ORDER)
    restored = ReceiptSession.from_json(session.to_json())
    assert restored.document_type == DOCUMENT_TYPE_ORDER

    data = session.to_json()
    data.pop("document_type")
    legacy = ReceiptSession.from_json(data)
    assert legacy.document_type == DOCUMENT_TYPE_RECEIPT


def test_write_order_note_persists_document_type_in_manifest(tmp_path: Path) -> None:
    artifact = write_receipt_note(
        _settings(tmp_path),
        _session(tmp_path, document_type=DOCUMENT_TYPE_ORDER),
        _parsed_order(),
    )
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    note_text = artifact.note_path.read_text(encoding="utf-8")

    assert manifest["document_type"] == DOCUMENT_TYPE_ORDER
    assert "# Заказ — Delivery App — 5000 AMD" in note_text


def test_classifier_detects_order_screenshot_noise() -> None:
    classification = classify_document_type(
        """
        Заказ #12345
        Burger Classic x2 2500 AMD
        Состав: булочка, котлета, соус, сыр
        Доставка 500 AMD
        Order total 5500 AMD
        """
    )

    assert classification.document_type == DOCUMENT_TYPE_ORDER
    assert classification.order_score > classification.receipt_score


def test_classifier_keeps_fiscal_receipts_as_receipts() -> None:
    classification = classify_document_type(
        """
        Վաճառք
        ժամ: 24-11-2025 15:54:39
        Կտրոն: #7740010
        Գանձապահ: Արամ
        ՀՎՀՀ: 01352303
        Ֆիսկալ համար: 53580329
        Ընդհանուր 1318
        """
    )

    assert classification.document_type == DOCUMENT_TYPE_RECEIPT
    assert classification.receipt_score > classification.order_score


def test_classifier_defaults_to_receipt_for_weak_text() -> None:
    classification = classify_document_type("Milk 1 500 AMD Total 500 AMD")
    assert classification.document_type == DOCUMENT_TYPE_RECEIPT
    assert classification.reason == "fallback"


def test_classifier_does_not_treat_substrings_as_markers() -> None:
    classification = classify_document_type("Kitchen border color: blue")
    assert classification.document_type == DOCUMENT_TYPE_RECEIPT
    assert classification.order_score == 0


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="token",
        openai_api_key="key",
        obsidian_vault=tmp_path,
        data_dir=tmp_path / "data",
        admin_telegram_user_ids=frozenset(),
        allowed_telegram_user_ids=frozenset(),
    )


def _session(tmp_path: Path, *, document_type: str) -> ReceiptSession:
    image = tmp_path / "image.jpg"
    clean = tmp_path / "clean.txt"
    source = tmp_path / "source.txt"
    for path in (image, clean, source):
        path.write_text("x", encoding="utf-8")
    return ReceiptSession(
        user_id=1,
        image_path=image,
        clean_ocr_path=clean,
        source_ocr_path=source,
        temporary_base_name="tmp",
        created_at=datetime(2026, 5, 20, 12, 0, 0),
        document_type=document_type,
    )


def _parsed_order() -> dict[str, object]:
    return {
        "date": "2026-05-20",
        "time": "12:00:00",
        "merchant": "Delivery App",
        "amount": "5000",
        "currency": "AMD",
        "category": "Food Delivery",
        "armenian_text": "",
        "russian_translation": "",
        "english_translation": "",
        "summary_ru": "Заказ еды",
        "items": [],
        "possible_errors": [],
    }
