from __future__ import annotations

import json
from copy import deepcopy

from app.storage.normalization import normalize_receipt_properties


REVIEW_ITEM_KEYS = (
    "name_ru",
    "name_en",
    "unit_price",
    "quantity",
    "unit",
    "line_total",
)

REVIEW_KEYS = (
    "date",
    "time",
    "merchant",
    "amount",
    "currency",
    "category",
    "summary_ru",
    "possible_errors",
    "items",
)


class ReviewPayloadError(RuntimeError):
    pass


def build_review_payload(parsed: dict[str, object]) -> dict[str, object]:
    parsed = normalize_receipt_properties(parsed)
    return {
        "date": str(parsed.get("date", "")),
        "time": str(parsed.get("time", "")),
        "merchant": str(parsed.get("merchant", "")),
        "amount": str(parsed.get("amount", "")),
        "currency": "AMD",
        "category": str(parsed.get("category", "")),
        "summary_ru": str(parsed.get("summary_ru", "")),
        "possible_errors": _review_errors(parsed.get("possible_errors", [])),
        "items": [
            {key: str(item.get(key, "")) for key in REVIEW_ITEM_KEYS}
            for item in parsed.get("items", [])
            if isinstance(item, dict)
        ],
    }


def review_payload_json(parsed: dict[str, object]) -> str:
    return json.dumps(build_review_payload(parsed), ensure_ascii=False, indent=2)


def render_review_text(parsed: dict[str, object]) -> str:
    payload = build_review_payload(parsed)
    lines = [
        "Проверьте поля, которые попадут в заметку:",
        "",
        f"Дата: {payload['date']}",
        f"Время: {payload['time'] or 'не указано'}",
        f"Продавец: {payload['merchant']}",
        f"Сумма: {payload['amount']} {payload['currency']}",
        f"Категория: {payload['category'] or 'не указана'}",
        f"Кратко: {payload['summary_ru'] or 'не указано'}",
        "",
        "Возможные ошибки распознавания:",
    ]
    possible_errors = payload.get("possible_errors", [])
    if isinstance(possible_errors, list) and possible_errors:
        lines.extend(f"- {item}" for item in possible_errors)
    else:
        lines.append("- Нет явных предупреждений.")
    lines.extend(
        [
            "",
            "Товары:",
        ]
    )
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        lines.append("Товары не распознаны как отдельные строки.")
        return "\n".join(lines)
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            lines.append(
                f"{index}. {item.get('name_ru', '')} | "
                f"{item.get('unit_price', '')} x {item.get('quantity', '')} "
                f"{item.get('unit', '')} = {item.get('line_total', '')}"
            )
    return "\n".join(lines)


def parse_review_payload(text: str) -> dict[str, object]:
    try:
        payload = json.loads(_strip_code_fence(text))
    except json.JSONDecodeError as exc:
        raise ReviewPayloadError("Исправления должны быть валидным JSON.") from exc
    if not isinstance(payload, dict):
        raise ReviewPayloadError("JSON должен быть объектом.")
    if set(payload) != set(REVIEW_KEYS):
        raise ReviewPayloadError("JSON содержит неправильный набор полей.")
    if payload.get("currency") != "AMD":
        raise ReviewPayloadError('Поле "currency" должно быть "AMD".')
    if not isinstance(payload.get("items"), list):
        raise ReviewPayloadError('Поле "items" должно быть массивом.')
    if not isinstance(payload.get("possible_errors"), list):
        raise ReviewPayloadError('Поле "possible_errors" должно быть массивом строк.')
    for key in REVIEW_KEYS:
        if key not in {"items", "possible_errors"} and not isinstance(payload.get(key), str):
            raise ReviewPayloadError(f'Поле "{key}" должно быть строкой.')
    if not all(isinstance(item, str) for item in payload["possible_errors"]):
        raise ReviewPayloadError('Поле "possible_errors" должно быть массивом строк.')
    for item in payload["items"]:
        if not isinstance(item, dict) or set(item) != set(REVIEW_ITEM_KEYS):
            raise ReviewPayloadError("Каждый товар содержит неправильный набор полей.")
        if not all(isinstance(item.get(key), str) for key in REVIEW_ITEM_KEYS):
            raise ReviewPayloadError("Все поля товара должны быть строками.")
    return normalize_receipt_properties(payload)


def merge_review_payload(
    parsed: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    merged = deepcopy(parsed)
    for key in REVIEW_KEYS:
        if key not in {"items", "possible_errors"}:
            merged[key] = payload[key]
    merged["possible_errors"] = _review_errors(payload["possible_errors"])
    old_items = parsed.get("items", [])
    new_items = []
    for index, item in enumerate(payload["items"]):
        base: dict[str, object] = {}
        if isinstance(old_items, list) and index < len(old_items) and isinstance(old_items[index], dict):
            base = deepcopy(old_items[index])
        base.update(item)
        new_items.append(base)
    merged["items"] = new_items
    return normalize_receipt_properties(merged)


def _review_errors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        compact = " ".join(str(item).split())
        if compact:
            result.append(compact[:180])
    return result[:8]


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped
