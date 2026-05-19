from __future__ import annotations

from app.config import Settings
from app.llm.openai_parser import ParsedReceipt, parse_receipt_text
from app.ocr.google_vision import run_document_ocr
from app.storage.corrections import CorrectionStore


def run_ocr(image_path) -> tuple[str, str]:
    return run_document_ocr(image_path)


def parse_for_review(
    ocr_text: str,
    *,
    settings: Settings,
    correction_store: CorrectionStore,
) -> ParsedReceipt:
    parsed = parse_receipt_text(
        ocr_text,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    return ParsedReceipt(
        data=correction_store.apply(parsed.data),
        raw_response=parsed.raw_response,
    )
