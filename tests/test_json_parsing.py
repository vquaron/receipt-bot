import json

import pytest

from app.llm.openai_parser import looks_like_receipt
from app.review.receipt_review import ReviewPayloadError, parse_review_payload


def valid_receipt() -> dict[str, object]:
    return {
        "date": "2026-04-07",
        "time": "20:41:00",
        "merchant": "Zovq Supermarket",
        "amount": "4465.75",
        "currency": "AMD",
        "category": "Grocery",
        "armenian_text": "",
        "russian_translation": "",
        "english_translation": "",
        "summary_ru": "Покупка продуктов",
        "items": [
            {
                "name_original": "մեծ",
                "name_ru": "Пакет большой",
                "name_en": "Large bag",
                "unit_price": "20",
                "quantity": "1",
                "unit": "шт",
                "line_total": "20",
            }
        ],
        "possible_errors": [],
    }


def test_receipt_schema_accepts_expected_shape() -> None:
    assert looks_like_receipt(valid_receipt())


def test_receipt_schema_rejects_extra_key() -> None:
    receipt = valid_receipt()
    receipt["extra"] = "nope"
    assert not looks_like_receipt(receipt)


def test_review_payload_rejects_invalid_json() -> None:
    with pytest.raises(ReviewPayloadError):
        parse_review_payload("{not json")


def test_review_payload_accepts_code_fence() -> None:
    payload = {
        "date": "07-04-2026",
        "time": "20:41",
        "merchant": "Զովք",
        "amount": "4 465,75 AMD",
        "currency": "AMD",
        "category": "grocery",
        "summary_ru": "Покупка",
        "items": [],
    }
    parsed = parse_review_payload("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    assert parsed["date"] == "2026-04-07"
    assert parsed["merchant"] == "Zovq Supermarket"
    assert parsed["amount"] == "4465.75"
    assert parsed["category"] == "Grocery"
