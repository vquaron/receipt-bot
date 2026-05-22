from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.repositories.documents import (
    FILE_KIND_OBSIDIAN_NOTE,
    FILE_KIND_ORIGINAL_IMAGE,
    FILE_KIND_OBSIDIAN_ATTACHMENT,
    FILE_KIND_STORED_IMAGE,
    DocumentStorageError,
)
from app.receipts.document_types import document_type_label
from app.receipts.repository import ReceiptCopyError, ReceiptNotFoundError
from app.storage.paths import safe_vault_path
from app.telegram.handlers.access import ensure_access
from app.telegram.handlers.common import access, receipts, send_text_chunks, settings
from app.users.paths import user_root_rel


LOGGER = logging.getLogger(__name__)


async def my_receipts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    records = receipts(context).list_user_receipts(update.effective_user.id)
    if not records:
        await update.message.reply_text("У вас пока нет сохранённых чеков.")
        return
    lines = ["Ваши последние чеки:"]
    for record in records[:20]:
        kind = document_type_label(record.document_type)
        lines.append(
            f"- {record.receipt_id}: {kind} | {record.date or 'no date'} | {record.merchant or 'unknown'} | {record.amount or 'unknown'} {record.currency}"
        )
    if len(records) > 20:
        lines.append(f"...ещё {len(records) - 20}. Используйте /export_receipts для архива.")
    await send_text_chunks(update.message, "\n".join(lines))


async def receipt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Использование: /receipt <receipt_id или file.md>")
        return
    record = receipts(context).find_user_receipt(update.effective_user.id, query)
    if record is None:
        await update.message.reply_text("Чек не найден среди ваших чеков.")
        return
    try:
        note_path, image_path = _record_paths(record, context, update.effective_user.id)
    except (ValueError, DocumentStorageError):
        LOGGER.warning(
            "Invalid receipt paths for user_id=%s receipt_id=%s query=%r",
            update.effective_user.id,
            record.receipt_id,
            query,
            exc_info=True,
        )
        await update.message.reply_text(
            "Чек содержит некорректные пути к файлам и не может быть открыт."
        )
        return
    summary = "\n".join(
        [
            f"receipt_id: {record.receipt_id}",
            f"type: {document_type_label(record.document_type)}",
            f"date: {record.date or 'не указана'}",
            f"merchant: {record.merchant or 'не указан'}",
            f"amount: {record.amount or 'не указана'} {record.currency}",
        ]
    )
    await update.message.reply_text(summary)
    if image_path:
        await update.message.reply_photo(photo=image_path)
    if note_path and note_path.exists():
        await update.message.reply_document(document=note_path)


async def export_receipts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    await update.message.reply_text("Собираю архив ваших чеков...")
    try:
        archive_path = await asyncio.to_thread(receipts(context).export_user_receipts, update.effective_user.id)
    except (OSError, DocumentStorageError):
        LOGGER.exception("Failed to export receipts for user_id=%s", update.effective_user.id)
        await update.message.reply_text("Не удалось создать архив чеков.")
        return
    await update.message.reply_document(document=archive_path)


async def grant_receipt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    if not access(context).is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /grant_receipt <user_id> <receipt_id или file.md>")
        return
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id должен быть числом.")
        return
    query = " ".join(context.args[1:]).strip()
    try:
        record = receipts(context).copy_receipt_to_user(query, target_user_id)
    except ReceiptNotFoundError:
        await update.message.reply_text("Исходный чек не найден.")
        return
    except ReceiptCopyError:
        LOGGER.exception("Failed to copy receipt to user_id=%s", target_user_id)
        await update.message.reply_text("Не удалось скопировать чек пользователю.")
        return
    await update.message.reply_text(
        "\n".join(
            [
                f"Чек скопирован пользователю {target_user_id}.",
                f"receipt_id: {record.receipt_id}",
                f"type: {document_type_label(record.document_type)}",
                f"date: {record.date or 'не указана'}",
                f"merchant: {record.merchant or 'не указан'}",
                f"amount: {record.amount or 'не указана'} {record.currency}",
            ]
        )
    )


def _first_existing_file(vault, rel_paths, *, prefixes: tuple[str, ...], suffixes: tuple[str, ...]):
    for rel_path in rel_paths:
        rel_text = rel_path.as_posix()
        if not rel_text.startswith(prefixes) or not rel_text.lower().endswith(suffixes):
            continue
        try:
            path = safe_vault_path(vault, rel_path)
        except ValueError:
            continue
        if path.exists() and path.is_file():
            return path
    return None


def _record_paths(record, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if record.source == "db":
        image_file = (
            _first_record_file(record, FILE_KIND_STORED_IMAGE)
            or _first_record_file(record, FILE_KIND_ORIGINAL_IMAGE)
            or _first_record_file(record, FILE_KIND_OBSIDIAN_ATTACHMENT)
        )
        note_file = _first_record_file(record, FILE_KIND_OBSIDIAN_NOTE)
        image_path = (
            receipts(context).materialize_file(
                image_file,
                settings(context).tmp_storage_dir / "telegram" / str(record.owner_user_id) / record.receipt_id,
            )
            if image_file is not None
            else None
        )
        note_path = receipts(context).file_path(note_file) if note_file is not None else None
        return note_path, image_path

    vault = settings(context).obsidian_vault
    try:
        user_prefix = f"{user_root_rel(settings(context), user_id).as_posix()}/"
    except ValueError:
        LOGGER.error(
            "Invalid USER_VAULT_ROOT configuration while opening receipt for user_id=%s receipt_id=%s",
            user_id,
            record.receipt_id,
            exc_info=True,
        )
        raise
    note_path = safe_vault_path(vault, record.note_rel)
    image_path = _first_existing_file(
        vault,
        record.files,
        prefixes=(user_prefix,),
        suffixes=(".jpg", ".jpeg", ".png"),
    )
    return note_path, image_path


def _first_record_file(record, kind: str):
    return next((file for file in record.file_records if file.kind == kind), None)
