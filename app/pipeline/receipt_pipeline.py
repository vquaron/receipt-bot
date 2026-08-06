from __future__ import annotations

from app.config import Settings
from app.llm.openai_parser import ParsedReceipt, parse_receipt_text
from app.ocr.google_vision import run_document_ocr
from app.receipts.document_types import DOCUMENT_TYPE_RECEIPT
from app.storage.corrections import CorrectionStore


def run_ocr(image_path) -> tuple[str, str]:
    return run_document_ocr(image_path)


def parse_for_review(
    ocr_text: str,
    *,
    settings: Settings,
    correction_store: CorrectionStore,
    document_type: str = DOCUMENT_TYPE_RECEIPT,
    owner_telegram_user_id: int | None = None,
) -> ParsedReceipt:
    parsed = parse_receipt_text(
        ocr_text,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        document_type=document_type,
    )
    return ParsedReceipt(
        data=correction_store.apply(parsed.data, owner_telegram_user_id=owner_telegram_user_id),
        raw_response=parsed.raw_response,
    )
