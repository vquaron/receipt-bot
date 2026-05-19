from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.config import Settings
from app.review.models import ReceiptSession
from app.storage.normalization import (
    amount_for_filename,
    normalize_receipt_properties,
    slugify_merchant,
)
from app.storage.paths import dated_relpath, ensure_parent, next_available_stem, yaml_string


@dataclass(frozen=True, slots=True)
class ReceiptArtifact:
    file_name: str
    note_path: Path
    manifest_path: Path
    date: str
    merchant: str
    amount: str
    currency: str


def write_receipt_note(
    settings: Settings,
    session: ReceiptSession,
    parsed: dict[str, object],
) -> ReceiptArtifact:
    normalized = normalize_receipt_properties(parsed)
    note_date, used_fallback_date = _resolve_note_date(str(normalized.get("date", "")))
    merchant = str(normalized.get("merchant", "")) or "unknown_merchant"
    amount = str(normalized.get("amount", ""))
    amount_for_name = amount_for_filename(amount)
    merchant_slug = slugify_merchant(merchant)

    base_stem = f"{note_date.isoformat()}_{merchant_slug}_{amount_for_name}AMD"
    receipt_dir = settings.obsidian_vault / dated_relpath("Receipts", _as_datetime(note_date), "")
    stem = next_available_stem(receipt_dir, base_stem, ".md")

    note_rel = dated_relpath("Receipts", _as_datetime(note_date), f"{stem}.md")
    attachment_rel = dated_relpath("Attachments/receipts", _as_datetime(note_date), f"{stem}.jpg")
    clean_rel = dated_relpath("OCR", _as_datetime(note_date), f"{stem}.clean.hy.txt")
    source_rel = dated_relpath("OCR_VERIFIED", _as_datetime(note_date), f"{stem}.verified.hy.txt")
    manifest_rel = dated_relpath("MANIFEST/receipts", _as_datetime(note_date), f"{stem}.manifest.json")

    note_path = settings.obsidian_vault / note_rel
    attachment_path = settings.obsidian_vault / attachment_rel
    clean_path = settings.obsidian_vault / clean_rel
    source_path = settings.obsidian_vault / source_rel
    manifest_path = settings.obsidian_vault / manifest_rel

    for path in (note_path, attachment_path, clean_path, source_path, manifest_path):
        ensure_parent(path)

    shutil.move(str(session.image_path), attachment_path)
    shutil.move(str(session.clean_ocr_path), clean_path)
    shutil.move(str(session.source_ocr_path), source_path)

    possible_errors = [
        str(item)
        for item in normalized.get("possible_errors", [])
        if str(item).strip()
    ]
    if used_fallback_date:
        possible_errors.append("Дата не определена из чека; использована текущая дата.")

    note_path.write_text(
        render_markdown(
            parsed=normalized,
            note_date=note_date.isoformat(),
            attachment_rel=attachment_rel,
            clean_rel=clean_rel,
            source_rel=source_rel,
            possible_errors=possible_errors,
        ),
        encoding="utf-8",
    )
    _write_manifest(
        manifest_path,
        {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "note": note_rel.as_posix(),
            "files": [
                note_rel.as_posix(),
                attachment_rel.as_posix(),
                clean_rel.as_posix(),
                source_rel.as_posix(),
            ],
        },
    )

    return ReceiptArtifact(
        file_name=f"{stem}.md",
        note_path=note_path,
        manifest_path=manifest_path,
        date=note_date.isoformat(),
        merchant=merchant,
        amount=amount or "unknown_amount",
        currency="AMD",
    )


def write_openai_debug_file(settings: Settings, session: ReceiptSession, raw_response: str) -> Path:
    debug_rel = dated_relpath(
        "DEBUG/openai",
        session.created_at,
        f"{session.temporary_base_name}.openai.raw.txt",
    )
    debug_path = settings.obsidian_vault / debug_rel
    ensure_parent(debug_path)
    debug_path.write_text(raw_response, encoding="utf-8")
    return debug_path


def render_markdown(
    *,
    parsed: dict[str, object],
    note_date: str,
    attachment_rel: Path,
    clean_rel: Path,
    source_rel: Path,
    possible_errors: list[str],
) -> str:
    parsed = normalize_receipt_properties(parsed)
    merchant = str(parsed.get("merchant", ""))
    amount = str(parsed.get("amount", ""))
    time = str(parsed.get("time", ""))
    category = str(parsed.get("category", ""))
    summary_ru = str(parsed.get("summary_ru", ""))
    items = parsed.get("items", [])
    errors_block = "\n".join(f"- {item}" for item in possible_errors) or "- Нет"

    return f"""---
date: {yaml_string(note_date)}
time: {yaml_string(time)}
merchant: {yaml_string(merchant)}
amount: {yaml_string(amount)}
category: {yaml_string(category)}
---

# Чек — {merchant or "unknown_merchant"} — {amount or "unknown_amount"} AMD

## Оригинал

![[{attachment_rel.as_posix()}]]

## Чек на русском

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

## Контроль OCR

- Clean OCR: [[{clean_rel.as_posix()}]]
- OCR used for OpenAI: [[{source_rel.as_posix()}]]
"""


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


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_note_date(value: str) -> tuple[date, bool]:
    if value:
        try:
            return date.fromisoformat(value), False
        except ValueError:
            pass
    return datetime.now().date(), True


def _as_datetime(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


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
