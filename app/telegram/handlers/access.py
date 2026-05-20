from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.telegram.handlers.common import access, delete_session, safe_send, settings
from app.users.onboarding import access_keyboard, access_request_text


LOGGER = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    delete_session(update.effective_user.id, context)
    if not await ensure_access(update, context):
        return
    await update.message.reply_text("Отправьте фото чека.")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        await update.message.reply_text(f"Ваш Telegram user_id: {update.effective_user.id}")


async def access_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        await request_access(update, context)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    access_control = access(context)
    if not access_control.is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    await update.message.reply_text(access_control.allowed_users_text())


async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    access_control = access(context)
    if not access_control.is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /revoke <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id должен быть числом.")
        return
    if access_control.revoke(user_id):
        await update.message.reply_text(f"Доступ отозван: {user_id}")
        await safe_send(context, user_id, "Доступ отозван.")
    else:
        await update.message.reply_text("Пользователь не был в allowlist или является админом.")


async def handle_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None or query.data is None:
        return
    access_control = access(context)
    if not access_control.is_admin(update.effective_user.id):
        await query.message.reply_text("Только администратор может управлять доступом.")
        return
    action, raw_user_id = query.data.split(":", 1)
    try:
        user_id = int(raw_user_id)
    except ValueError:
        await query.message.reply_text("Некорректный user_id в заявке.")
        return
    if action == "access_approve":
        access_control.approve(user_id, approved_by=update.effective_user.id)
        await query.edit_message_text(f"Доступ одобрен для user_id {user_id}.")
        await safe_send(context, user_id, "Доступ одобрен. Теперь можно отправлять чеки.")
    elif action == "access_reject":
        access_control.reject(user_id)
        await query.edit_message_text(f"Доступ отклонён для user_id {user_id}.")
        await safe_send(context, user_id, "Доступ отклонён.")


async def ensure_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user is None:
        return False
    if access(context).is_allowed(update.effective_user.id):
        return True
    await request_access(update, context)
    return False


async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message is None:
        return
    access_control = access(context)
    if access_control.is_allowed(update.effective_user.id):
        await message.reply_text("Доступ уже одобрен. Можно отправлять чеки.")
        return
    request, created = access_control.create_request(update.effective_user)
    if created:
        admin_ids = settings(context).admin_telegram_user_ids
        for admin_id in admin_ids:
            await safe_send(context, admin_id, access_request_text(request), access_keyboard(request.user_id))
        if admin_ids:
            await message.reply_text("Заявка на доступ отправлена администратору.")
        else:
            await message.reply_text("Заявка на доступ создана, но ADMIN_TELEGRAM_USER_IDS пока не настроен.")
        return
    if access_control.is_pending(update.effective_user.id):
        await message.reply_text("Заявка на доступ уже ожидает решения администратора.")
    else:
        await message.reply_text("Доступ отклонён.")

