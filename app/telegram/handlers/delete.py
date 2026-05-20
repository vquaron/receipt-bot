from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.obsidian.delete import ReceiptDeleteError, delete_receipt
from app.telegram.handlers.access import ensure_access
from app.telegram.handlers.common import access, settings


LOGGER = logging.getLogger(__name__)


async def delete_receipt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    note_name = " ".join(context.args).strip()
    if not note_name:
        await update.message.reply_text("Укажите заметку: /delete_receipt <receipt_id или file.md>")
        return
    access_control = access(context)
    allow_all = access_control.is_admin(update.effective_user.id)
    try:
        result = await asyncio.to_thread(
            delete_receipt,
            settings(context).obsidian_vault,
            note_name,
            owner_user_id=update.effective_user.id,
            allow_all_users=allow_all,
        )
    except ReceiptDeleteError as exc:
        await update.message.reply_text(f"Не удалось удалить чек: {exc}")
        return
    except OSError:
        LOGGER.exception("Failed to delete receipt files.")
        await update.message.reply_text("Ошибка удаления файлов чека.")
        return
    await update.message.reply_text(
        "\n".join(
            [
                f"Удалено файлов: {len(result.deleted)}",
                f"Заметка: {result.note_path.name}",
                f"Не найдено связанных файлов: {len(result.missing)}",
            ]
        )
    )

