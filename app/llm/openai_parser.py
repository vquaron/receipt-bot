from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI, RateLimitError

from app.receipts.document_types import (
    DOCUMENT_TYPE_ORDER,
    DOCUMENT_TYPE_RECEIPT,
    normalize_document_type,
)
from app.storage.normalization import normalize_receipt_properties


REQUIRED_KEYS = {
    "date",
    "time",
    "merchant",
    "amount",
    "currency",
    "category",
    "armenian_text",
    "russian_translation",
    "english_translation",
    "summary_ru",
    "items",
    "possible_errors",
}

ITEM_KEYS = {
    "name_original",
    "name_ru",
    "name_en",
    "unit_price",
    "quantity",
    "unit",
    "line_total",
}

RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        "time": {"type": "string"},
        "merchant": {"type": "string"},
        "amount": {"type": "string"},
        "currency": {"type": "string", "enum": ["AMD"]},
        "category": {"type": "string"},
        "armenian_text": {"type": "string"},
        "russian_translation": {"type": "string"},
        "english_translation": {"type": "string"},
        "summary_ru": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name_original": {"type": "string"},
                    "name_ru": {"type": "string"},
                    "name_en": {"type": "string"},
                    "unit_price": {"type": "string"},
                    "quantity": {"type": "string"},
                    "unit": {"type": "string"},
                    "line_total": {"type": "string"},
                },
                "required": sorted(ITEM_KEYS),
                "additionalProperties": False,
            },
        },
        "possible_errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": sorted(REQUIRED_KEYS),
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You extract structured data from receipt OCR. The receipt is usually Armenian, but it can contain Armenian, Russian, and English text.

Use SOURCE OCR as the only primary source.
Rules:
- Do not invent facts that are absent from SOURCE OCR.
- Extract date and time from the receipt itself. Armenian "ժամ:" followed by a value such as "24-11-2025 15:54:39" is the receipt date/time. Return date as YYYY-MM-DD and time as HH:MM:SS.
- Extract merchant from the receipt itself. Prefer the quoted legal/store name before "ՍՊԸ", "ООО", or similar legal suffix. Remove surrounding quotation marks. Normalize known store descriptors to a stable readable name when obvious, e.g. "Զովք Սուպերմարկետ" -> "Zovq Supermarket".
- Extract amount as the final paid/total amount, not a line-item price and not VAT.
- If date, time, amount, or merchant are not confidently available, return an empty string.
- Put uncertainty notes into possible_errors.
- Correct only obvious OCR mistakes.
- Russian unit words can be misrecognized with visually similar Armenian letters. For example, "շտ" or "ՇՏ" next to a quantity should usually be read as Russian "шт" when the surrounding product text is Russian.
- Extract product rows into items. Each item must include original product name, Russian name, English name, unit price, quantity, unit, and line total. Translate Armenian product words in name_ru and name_en; do not leave Armenian words in name_ru/name_en unless they are clearly brand names or merchant names. If a product word is corrupted by OCR and cannot be confidently translated, use the closest obvious correction only when safe, otherwise keep the uncertain fragment and add a note to possible_errors.
- Normalize units: piece/count units such as "հատ", "шт", "WT" should become "шт" in Russian context and "pcs" in English context; kilogram units such as "կգ", "кг", "kr" should become "кг" in Russian context and "kg" in English context.
- russian_translation and english_translation must translate only meaningful receipt fields, not every noisy OCR line. Preserve merchant names and product brands when translation would be unnatural.
- Determine an expense category only when it is reasonably supported by the text.
- Return JSON that matches the supplied schema exactly.
"""

ORDER_SYSTEM_PROMPT = """You extract structured purchase data from OCR of a non-fiscal order screenshot. It can be a delivery app order, online store order, cart, confirmation screen, or restaurant/shop screenshot. The text can contain Armenian, Russian, and English.

Use SOURCE OCR as the only primary source.
Rules:
- Do not invent facts that are absent from SOURCE OCR.
- This is not necessarily a fiscal receipt. Extract order date/time, merchant, final paid/total amount, category, and purchased items only when they are visible.
- The main goal is a clean product table: product name, quantity, unit price, and line total.
- Ignore irrelevant OCR text: ingredients, composition, allergens, nutrition facts, descriptions, UI labels, app navigation, delivery address, courier/support text, ads, recommendations, ratings, comments, buttons, promo banners, and repeated screen chrome.
- Do not create separate items from ingredients or product descriptions. If OCR shows "Ingredients:", "Բաղադրություն", "Состав", or similar text under a product, skip that text unless it is clearly part of the product name.
- For restaurant or grocery orders, keep the ordered item name concise. Keep modifiers/options only if they change the purchased item or price. Do not list unpaid options as separate products.
- If a product has quantity markers such as "x2", "2 шт", "+ 2", or app quantity controls, put the quantity into quantity. If only one item is implied, use "1".
- Extract unit_price and line_total only when visible or directly implied by a visible quantity and line total. If uncertain, leave the uncertain field empty and add a note to possible_errors.
- Extract amount as the final paid/order total, not delivery fee, service fee, discount, subtotal, or a single line item.
- If date, time, amount, merchant, quantity, price, or line total are not confidently available, return an empty string for the field.
- Put concise Russian uncertainty notes into possible_errors, mentioning the affected field or item when useful.
- Translate product names into Russian and English. Do not leave Armenian text in name_ru/name_en unless it is clearly a brand or merchant name.
- Normalize units: piece/count units such as "հատ", "шт", "pcs", "x" should become "шт" in Russian context and "pcs" in English context; kilogram units such as "կգ", "кг", "kg" should become "кг" in Russian context and "kg" in English context.
- russian_translation and english_translation must summarize only meaningful order fields and ordered products, not every noisy OCR line.
- Determine an expense category only when it is reasonably supported by the text, for example Food Delivery, Grocery, Pharmacy, Restaurant, or Online Order.
- Return JSON that matches the supplied schema exactly.
"""


class OpenAIInvalidJSONError(RuntimeError):
    def __init__(self, raw_response: str) -> None:
        super().__init__("OpenAI returned invalid JSON.")
        self.raw_response = raw_response


class OpenAIQuotaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedReceipt:
    data: dict[str, Any]
    raw_response: str


def parse_receipt_text(
    ocr_text: str,
    *,
    api_key: str,
    model: str,
    document_type: str = DOCUMENT_TYPE_RECEIPT,
) -> ParsedReceipt:
    client = OpenAI(api_key=api_key)
    prompt = prompt_for_document_type(document_type)
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"SOURCE OCR:\n{ocr_text}"},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "receipt",
                    "strict": True,
                    "schema": RECEIPT_SCHEMA,
                }
            },
        )
    except RateLimitError as exc:
        body = getattr(exc, "body", {}) or {}
        if isinstance(body, dict) and body.get("code") == "insufficient_quota":
            raise OpenAIQuotaError(str(exc)) from exc
        raise

    raw_response = response.output_text or ""
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise OpenAIInvalidJSONError(raw_response) from exc

    if not looks_like_receipt(parsed):
        raise OpenAIInvalidJSONError(raw_response)

    _fill_receipt_fallbacks(parsed, ocr_text)
    return ParsedReceipt(data=normalize_receipt_properties(parsed), raw_response=raw_response)


def prompt_for_document_type(document_type: str) -> str:
    if normalize_document_type(document_type) == DOCUMENT_TYPE_ORDER:
        return ORDER_SYSTEM_PROMPT
    return SYSTEM_PROMPT


def looks_like_receipt(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != REQUIRED_KEYS:
        return False
    if value.get("currency") != "AMD":
        return False
    if not isinstance(value.get("possible_errors"), list):
        return False
    if not isinstance(value.get("items"), list):
        return False
    scalar_keys = REQUIRED_KEYS - {"possible_errors", "items"}
    if not all(isinstance(value.get(key), str) for key in scalar_keys):
        return False
    return all(_looks_like_item(item) for item in value.get("items", []))


def _looks_like_item(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == ITEM_KEYS
        and all(isinstance(value.get(key), str) for key in ITEM_KEYS)
    )


def _fill_receipt_fallbacks(parsed: dict[str, Any], ocr_text: str) -> None:
    if not parsed.get("date") or not parsed.get("time"):
        date_value, time_value = _extract_receipt_datetime(ocr_text)
        parsed["date"] = parsed.get("date") or date_value
        parsed["time"] = parsed.get("time") or time_value
    merchant = str(parsed.get("merchant", "")).strip()
    if not merchant or merchant.lower() in {"unknown", "unknown_merchant"}:
        parsed["merchant"] = _extract_merchant(ocr_text) or merchant


def _extract_receipt_datetime(text: str) -> tuple[str, str]:
    patterns = [
        r"ժամ[:՝]?\s*(\d{2})[-./](\d{2})[-./](\d{4})\s+(\d{2}:\d{2}(?::\d{2})?)",
        r"\b(\d{2})[-./](\d{2})[-./](\d{4})\s+(\d{2}:\d{2}(?::\d{2})?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            day, month, year, time_value = match.groups()
            if len(time_value) == 5:
                time_value = f"{time_value}:00"
            return f"{year}-{month}-{day}", time_value
    return "", ""


def _extract_merchant(text: str) -> str:
    patterns = [
        r"[«\"]\s*([^»\"\n]{2,80}?)\s*[»\"]\s*(?:ՍՊԸ|ООО|LLC|ԼԼԸ)",
        r"^(.{2,80}?)\s+(?:ՍՊԸ|ООО|LLC|ԼԼԸ)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            merchant = re.sub(r"\s+", " ", match.group(1)).strip(" ,.:;")
            if merchant:
                return merchant
    return ""
