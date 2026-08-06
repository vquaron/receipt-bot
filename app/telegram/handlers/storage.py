from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.obsidian.purge import LegacyManifestPurgeResult, purge_legacy_manifest_receipts
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


async def purge_legacy_manifests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not access(context).is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    apply_changes = "--apply" in context.args
    app_settings = settings(context)
    try:
        result = await asyncio.to_thread(
            purge_legacy_manifest_receipts,
            app_settings.obsidian_vault,
            user_vault_root=app_settings.user_vault_root,
            apply=apply_changes,
        )
    except Exception:
        LOGGER.exception("Legacy manifest purge failed.")
        await update.message.reply_text("Не удалось проверить legacy manifest-backed файлы.")
        return
    await update.message.reply_text(_render_legacy_purge_report(result))


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


def _render_legacy_purge_report(result: LegacyManifestPurgeResult) -> str:
    mode = "apply" if result.applied else "dry-run"
    lines = [
        f"Legacy manifest purge ({mode}):",
        f"manifests seen: {result.manifests_seen}",
        f"planned paths: {len(result.planned_paths)}",
        f"files deleted: {result.files_deleted}",
        f"manifests deleted: {result.manifests_deleted}",
        f"missing files: {result.files_missing}",
        f"skipped manifests: {result.skipped_manifests}",
    ]
    if result.errors:
        lines.append("errors:")
        lines.extend(f"- {error}" for error in result.errors[:MAX_ISSUES_IN_MESSAGE])
        if len(result.errors) > MAX_ISSUES_IN_MESSAGE:
            lines.append(f"...and {len(result.errors) - MAX_ISSUES_IN_MESSAGE} more")
    if not result.applied:
        lines.append("Run /purge_legacy_manifests --apply to delete the planned files.")
    return "\n".join(lines)
