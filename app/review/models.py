from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.preprocessing.base import PreprocessingResult, disabled_result


class SessionState(str, Enum):
    PROCESSING_OPENAI = "processing_openai"
    WAITING_FOR_RUSSIAN_REVIEW = "waiting_for_russian_review"
    WAITING_FOR_CORRECTED_REVIEW = "waiting_for_corrected_review"
    DONE = "done"


@dataclass(slots=True)
class ReceiptSession:
    user_id: int
    image_path: Path
    clean_ocr_path: Path
    source_ocr_path: Path
    temporary_base_name: str
    created_at: datetime
    parsed_receipt: dict[str, Any] | None = None
    state: SessionState = SessionState.PROCESSING_OPENAI
    preprocessing_result: PreprocessingResult | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["image_path"] = str(self.image_path)
        data["clean_ocr_path"] = str(self.clean_ocr_path)
        data["source_ocr_path"] = str(self.source_ocr_path)
        data["created_at"] = self.created_at.isoformat()
        data["state"] = self.state.value
        data["preprocessing_result"] = (
            self.preprocessing_result.to_json() if self.preprocessing_result else disabled_result(self.image_path).to_json()
        )
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ReceiptSession":
        image_path = Path(str(data["image_path"]))
        return cls(
            user_id=int(data["user_id"]),
            image_path=image_path,
            clean_ocr_path=Path(str(data["clean_ocr_path"])),
            source_ocr_path=Path(str(data["source_ocr_path"])),
            temporary_base_name=str(data["temporary_base_name"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            parsed_receipt=data.get("parsed_receipt"),
            state=SessionState(str(data.get("state", SessionState.PROCESSING_OPENAI))),
            preprocessing_result=PreprocessingResult.from_json(
                data.get("preprocessing_result"),
                fallback_input=image_path,
            ),
        )


def review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Подтвердить заметку", callback_data="review_confirm")],
            [InlineKeyboardButton("✏️ Исправить поля", callback_data="review_edit")],
            [InlineKeyboardButton("❌ Отменить", callback_data="review_cancel")],
        ]
    )
