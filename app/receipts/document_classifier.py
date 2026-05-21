from __future__ import annotations

import re
from dataclasses import dataclass

from app.receipts.document_types import DOCUMENT_TYPE_ORDER, DOCUMENT_TYPE_RECEIPT, normalize_document_type


RECEIPT_MARKERS = (
    "հդմ",
    "հվհհ",
    "կտրոն",
    "գանձապահ",
    "դրամարկղ",
    "ֆիսկալ",
    "ֆիսկալ համար",
    "հարկային",
    "աահ",
    "վճարվել է",
    "кассир",
    "касса",
    "фискал",
    "фискальный",
    "инн",
    "ндс",
    "чек",
    "receipt",
    "cashier",
    "fiscal",
    "tax id",
    "vat",
)

ORDER_MARKERS = (
    "заказ",
    "order",
    "պատվեր",
    "доставка",
    "delivery",
    "առաքում",
    "корзина",
    "cart",
    "basket",
    "checkout",
    "оплата заказа",
    "order total",
    "subtotal",
    "service fee",
    "delivery fee",
    "промокод",
    "promo",
    "menu.am",
    "wolt",
    "glovo",
    "yandex",
    "яндекс",
)

NOISE_ORDER_MARKERS = (
    "ингредиент",
    "ingredients",
    "состав",
    "բաղադրություն",
    "аллерген",
    "allergen",
    "nutrition",
    "калории",
    "ккал",
    "описание",
    "description",
    "добавить",
    "add to cart",
    "рейтинг",
    "rating",
)


@dataclass(frozen=True, slots=True)
class DocumentClassification:
    document_type: str
    confidence: float
    reason: str
    receipt_score: int
    order_score: int


def classify_document_type(ocr_text: str, *, fallback: str = DOCUMENT_TYPE_RECEIPT) -> DocumentClassification:
    text = _compact(ocr_text)
    receipt_score = _marker_score(text, RECEIPT_MARKERS)
    order_score = _marker_score(text, ORDER_MARKERS) + _marker_score(text, NOISE_ORDER_MARKERS)

    if _has_app_quantity_pattern(text):
        order_score += 2
    if _has_receipt_datetime_marker(text):
        receipt_score += 2
    if _has_receipt_tax_number_marker(text):
        receipt_score += 3

    if receipt_score >= 3 and receipt_score >= order_score:
        return DocumentClassification(DOCUMENT_TYPE_RECEIPT, _confidence(receipt_score, order_score), "receipt_markers", receipt_score, order_score)
    if order_score >= 4 and order_score >= receipt_score + 2:
        return DocumentClassification(DOCUMENT_TYPE_ORDER, _confidence(order_score, receipt_score), "order_markers", receipt_score, order_score)
    if order_score >= 3 and receipt_score == 0:
        return DocumentClassification(DOCUMENT_TYPE_ORDER, 0.65, "order_markers_without_fiscal_markers", receipt_score, order_score)

    document_type = normalize_document_type(fallback)
    return DocumentClassification(document_type, 0.5, "fallback", receipt_score, order_score)


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _marker_score(text: str, markers: tuple[str, ...]) -> int:
    score = 0
    for marker in markers:
        pattern = rf"(?<!\w){re.escape(marker)}(?!\w)"
        if re.search(pattern, text):
            score += 1
    return score


def _has_app_quantity_pattern(text: str) -> bool:
    return bool(re.search(r"(?:^|\s)(?:x\s*\d+|\d+\s*x|[+×]\s*\d+)(?:\s|$)", text))


def _has_receipt_datetime_marker(text: str) -> bool:
    return bool(re.search(r"ժամ[:՝]?\s*\d{2}[-./]\d{2}[-./]\d{4}", text))


def _has_receipt_tax_number_marker(text: str) -> bool:
    return bool(re.search(r"(?:հվհհ|инн|tax id)[:\s]*\d{5,}", text))


def _confidence(primary_score: int, secondary_score: int) -> float:
    if primary_score <= 0:
        return 0.5
    margin = max(0, primary_score - secondary_score)
    return min(0.95, 0.55 + primary_score * 0.08 + margin * 0.04)
