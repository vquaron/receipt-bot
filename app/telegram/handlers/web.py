from __future__ import annotations

import logging

from telegram import LinkPreviewOptions, Update
from telegram.ext import ContextTypes

from app.telegram.handlers.access import ensure_access
from app.telegram.handlers.common import settings
from app.web.auth import WebAuthRepository


LOGGER = logging.getLogger(__name__)


async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    app_settings = settings(context)
    if not app_settings.web_base_url:
        await update.message.reply_text("WEB_BASE_URL пока не настроен. Веб-вход недоступен.")
        return
    try:
        link = WebAuthRepository(app_settings).create_magic_link(update.effective_user.id)
    except Exception:
        LOGGER.exception("Failed to create web magic link for user_id=%s", update.effective_user.id)
        await update.message.reply_text("Не удалось создать ссылку для входа.")
        return
    login_url = f"{app_settings.web_base_url}/auth/magic?token={link.token}"
    await update.message.reply_text(
        "\n".join(
            [
                "Ссылка для входа в Web MVP:",
                login_url,
                f"Ссылка одноразовая и действует {app_settings.web_magic_link_ttl_minutes} минут.",
            ]
        ),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
