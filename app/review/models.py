from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.receipts.document_types import DOCUMENT_TYPE_RECEIPT, normalize_document_type


class SessionState(str, Enum):
    PROCESSING_OCR = "processing_ocr"
    PROCESSING_OPENAI = "processing_openai"
    WAITING_FOR_RUSSIAN_REVIEW = "waiting_for_russian_review"
    WAITING_FOR_CORRECTED_REVIEW = "waiting_for_corrected_review"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(slots=True)
class ReceiptSession:
    user_id: int
    image_path: Path
    clean_ocr_path: Path
    source_ocr_path: Path
    temporary_base_name: str
    created_at: datetime
    document_type: str = DOCUMENT_TYPE_RECEIPT
    parsed_receipt: dict[str, Any] | None = None
    state: SessionState = SessionState.PROCESSING_OPENAI
    session_id: str = field(default_factory=lambda: uuid4().hex)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["image_path"] = str(self.image_path)
        data["clean_ocr_path"] = str(self.clean_ocr_path)
        data["source_ocr_path"] = str(self.source_ocr_path)
        data["created_at"] = self.created_at.isoformat()
        data["document_type"] = normalize_document_type(self.document_type)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ReceiptSession":
        return cls(
            session_id=str(data.get("session_id") or uuid4().hex),
            user_id=int(data["user_id"]),
            image_path=Path(str(data["image_path"])),
            clean_ocr_path=Path(str(data["clean_ocr_path"])),
            source_ocr_path=Path(str(data["source_ocr_path"])),
            temporary_base_name=str(data["temporary_base_name"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            document_type=normalize_document_type(data.get("document_type", DOCUMENT_TYPE_RECEIPT)),
            parsed_receipt=data.get("parsed_receipt"),
            state=SessionState(str(data.get("state", SessionState.PROCESSING_OPENAI))),
        )


def review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Подтвердить заметку", callback_data="review_confirm")],
            [InlineKeyboardButton("✏️ Исправить поля", callback_data="review_edit")],
            [InlineKeyboardButton("❌ Отменить", callback_data="review_cancel")],
        ]
    )
