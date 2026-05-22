from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.storage.health import StorageHealthService
from app.telegram.handlers.common import access, settings


LOGGER = logging.getLogger(__name__)
MAX_ISSUES_IN_MESSAGE = 10


async def storage_health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not access(context).is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    await update.message.reply_text("Проверяю storage health...")
    try:
        report = await asyncio.to_thread(StorageHealthService(settings(context)).check)
    except Exception:
        LOGGER.exception("Storage health check failed.")
        await update.message.reply_text("Не удалось выполнить storage health check.")
        return
    await update.message.reply_text(_render_storage_health_report(report))


def _render_storage_health_report(report) -> str:
    lines = [
        "Storage health:",
        f"errors: {report.error_count}",
        f"warnings: {report.warning_count}",
    ]
    if not report.issues:
        lines.append("issues: none")
        return "\n".join(lines)
    lines.append("issues:")
    for issue in report.issues[:MAX_ISSUES_IN_MESSAGE]:
        document = f" doc={issue.document_id}" if issue.document_id else ""
        kind = f" kind={issue.file_kind}" if issue.file_kind else ""
        backend = f" backend={issue.storage_backend}" if issue.storage_backend else ""
        target = f" target={issue.path_or_key}" if issue.path_or_key else ""
        lines.append(f"- [{issue.severity}] {issue.code}{document}{kind}{backend}{target}: {issue.message}")
    if len(report.issues) > MAX_ISSUES_IN_MESSAGE:
        lines.append(f"...and {len(report.issues) - MAX_ISSUES_IN_MESSAGE} more")
    return "\n".join(lines)
