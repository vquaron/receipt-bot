from __future__ import annotations


DOCUMENT_TYPE_RECEIPT = "receipt"
DOCUMENT_TYPE_ORDER = "order"
DOCUMENT_TYPES = {DOCUMENT_TYPE_RECEIPT, DOCUMENT_TYPE_ORDER}


def normalize_document_type(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in DOCUMENT_TYPES else DOCUMENT_TYPE_RECEIPT


def document_type_label(document_type: str) -> str:
    return "заказ" if normalize_document_type(document_type) == DOCUMENT_TYPE_ORDER else "чек"


def document_genitive_ru(document_type: str) -> str:
    return "заказа" if normalize_document_type(document_type) == DOCUMENT_TYPE_ORDER else "чека"


def document_prepositional_ru(document_type: str) -> str:
    return "заказе" if normalize_document_type(document_type) == DOCUMENT_TYPE_ORDER else "чеке"


def document_title_ru(document_type: str) -> str:
    return "Заказ" if normalize_document_type(document_type) == DOCUMENT_TYPE_ORDER else "Чек"
