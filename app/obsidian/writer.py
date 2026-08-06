from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.config import Settings
from app.receipts.document_types import document_title_ru
from app.review.models import ReceiptSession
from app.storage.normalization import normalize_receipt_properties
from app.storage.paths import ensure_parent, yaml_string
from app.users.paths import user_dated_relpath


@dataclass(frozen=True, slots=True)
class ReceiptArtifact:
    receipt_id: str
    file_name: str
    note_path: Path
    date: str
    merchant: str
    amount: str
    currency: str
    attachment_path: Path | None = None
    note_rel: Path | None = None
    attachment_rel: Path | None = None


def export_receipt_note(
    settings: Settings,
    *,
    user_id: int,
    file_stem: str,
    document_type: str,
    parsed: dict[str, object],
    source_image_path: Path,
) -> ReceiptArtifact:
    normalized = normalize_receipt_properties(parsed)
    note_date, used_fallback_date = _resolve_note_date(str(normalized.get("date", "")))
    merchant = str(normalized.get("merchant", "")) or "unknown_merchant"
    amount = str(normalized.get("amount", ""))
    stamp = _as_datetime(note_date)

    note_rel = user_dated_relpath(settings, user_id, "Receipts", stamp, f"{file_stem}.md")
    attachment_rel = user_dated_relpath(settings, user_id, "Attachments/receipts", stamp, f"{file_stem}.jpg")
    note_path = settings.obsidian_vault / note_rel
    attachment_path = settings.obsidian_vault / attachment_rel
    for path in (note_path, attachment_path):
        ensure_parent(path)

    shutil.copy2(source_image_path, attachment_path)
    possible_errors = _normalized_possible_errors(normalized.get("possible_errors", []))
    if used_fallback_date:
        possible_errors.append("Дата не определена из чека; использована текущая дата.")
    note_path.write_text(
        render_markdown(
            parsed=normalized,
            note_date=note_date.isoformat(),
            attachment_rel=attachment_rel,
            clean_rel=None,
            source_rel=None,
            possible_errors=possible_errors,
            document_type=document_type,
        ),
        encoding="utf-8",
    )
    return ReceiptArtifact(
        receipt_id=file_stem,
        file_name=f"{file_stem}.md",
        note_path=note_path,
        date=note_date.isoformat(),
        merchant=merchant,
        amount=amount or "unknown_amount",
        currency="AMD",
        attachment_path=attachment_path,
        note_rel=note_rel,
        attachment_rel=attachment_rel,
    )


def write_openai_debug_file(settings: Settings, session: ReceiptSession, raw_response: str) -> Path:
    debug_name = f"{_safe_debug_base_name(session.temporary_base_name)}.openai.raw.txt"
    debug_path = (
        settings.debug_storage_dir
        / "openai"
        / str(session.user_id)
        / f"{session.created_at:%Y}"
        / f"{session.created_at:%m}"
        / debug_name
    )
    ensure_parent(debug_path)
    debug_path.write_text(raw_response, encoding="utf-8")
    return debug_path


def render_markdown(
    *,
    parsed: dict[str, object],
    note_date: str,
    attachment_rel: Path,
    clean_rel: Path | None,
    source_rel: Path | None,
    possible_errors: list[str],
    document_type: str = "receipt",
) -> str:
    parsed = normalize_receipt_properties(parsed)
    title = document_title_ru(document_type)
    merchant = str(parsed.get("merchant", ""))
    amount = str(parsed.get("amount", ""))
    time = str(parsed.get("time", ""))
    category = str(parsed.get("category", ""))
    summary_ru = str(parsed.get("summary_ru", ""))
    items = parsed.get("items", [])
    errors_block = "\n".join(f"- {item}" for item in possible_errors) or "- Нет"
    ocr_block = _render_ocr_block(clean_rel=clean_rel, source_rel=source_rel)

    return f"""---
date: {yaml_string(note_date)}
time: {yaml_string(time)}
merchant: {yaml_string(merchant)}
amount: {yaml_string(amount)}
category: {yaml_string(category)}
---

# {title} — {merchant or "unknown_merchant"} — {amount or "unknown_amount"} AMD

## Оригинал

![[{attachment_rel.as_posix()}]]

## {title} на русском

{_render_overview_ru(note_date, time, merchant, amount, category, summary_ru)}

### Товары

{render_items_table(items, language="ru")}

## Translation into English

{_render_overview_en(note_date, time, merchant, amount, category)}

### Items

{render_items_table(items, language="en")}

## Кратко

{summary_ru}

## Возможные ошибки распознавания

{errors_block}
{ocr_block}"""


def render_items_table(items: object, *, language: str) -> str:
    if not isinstance(items, list) or not items:
        return "_Товары не распознаны как отдельные строки._"
    if language == "en":
        header = "| # | Product | Unit price | Qty | Unit | Total |\n|---:|---|---:|---:|---|---:|"
        name_key = "name_en"
    else:
        header = "| # | Товар | Цена | Кол-во | Ед. | Сумма |\n|---:|---|---:|---:|---|---:|"
        name_key = "name_ru"
    rows = [header]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        unit = _normalize_unit(str(item.get("unit", "")), language=language)
        rows.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _cell(str(item.get(name_key) or item.get("name_original") or "")),
                    _cell(str(item.get("unit_price", ""))),
                    _cell(str(item.get("quantity", ""))),
                    _cell(unit),
                    _cell(str(item.get("line_total", ""))),
                ]
            )
            + " |"
        )
    return "\n".join(rows) if len(rows) > 1 else "_Товары не распознаны как отдельные строки._"


def _render_ocr_block(*, clean_rel: Path | None, source_rel: Path | None) -> str:
    if clean_rel is None and source_rel is None:
        return ""
    lines = ["", "## Контроль OCR", ""]
    if clean_rel is not None:
        lines.append(f"- Clean OCR: [[{clean_rel.as_posix()}]]")
    if source_rel is not None:
        lines.append(f"- OCR used for OpenAI: [[{source_rel.as_posix()}]]")
    lines.append("")
    return "\n".join(lines)


def _resolve_note_date(value: str) -> tuple[date, bool]:
    if value:
        try:
            return date.fromisoformat(value), False
        except ValueError:
            pass
    return datetime.now().date(), True


def _as_datetime(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def _normalized_possible_errors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        compact = " ".join(str(item).split())
        if compact:
            result.append(compact[:180])
    return result[:8]


def _safe_debug_base_name(value: str) -> str:
    cleaned = value.replace("/", "_").replace("\\", "_").strip(" .")
    return cleaned or "openai"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _normalize_unit(value: str, *, language: str) -> str:
    compact = value.strip().lower()
    if compact in {"wt", "հատ", "hat", "pcs", "pc", "piece", "шт", "շհ", "շտ"}:
        return "pcs" if language == "en" else "шт"
    if compact in {"kr", "kg", "կգ", "кг"}:
        return "kg" if language == "en" else "кг"
    return value


def _render_overview_ru(
    date_value: str,
    time: str,
    merchant: str,
    amount: str,
    category: str,
    summary: str,
) -> str:
    lines = [
        f"- Дата: {date_value}",
        f"- Время: {time or 'не указано'}",
        f"- Продавец: {merchant or 'не указан'}",
        f"- Категория: {category or 'не указана'}",
        f"- Итого: {amount or 'не указано'} AMD",
    ]
    if summary:
        lines.append(f"- Кратко: {summary}")
    return "\n".join(lines)


def _render_overview_en(date_value: str, time: str, merchant: str, amount: str, category: str) -> str:
    return "\n".join(
        [
            f"- Date: {date_value}",
            f"- Time: {time or 'not specified'}",
            f"- Merchant: {merchant or 'not specified'}",
            f"- Category: {category or 'not specified'}",
            f"- Total: {amount or 'not specified'} AMD",
        ]
    )
