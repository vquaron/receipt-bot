from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from app.config import Settings
from app.receipts.repository import ReceiptRepository
from app.review.models import ReceiptSession
from app.security.access_control import AccessControl
from app.storage.corrections import CorrectionStore
from app.storage.sessions import SessionStore
from app.users.quotas import QuotaService


LOGGER = logging.getLogger(__name__)
SESSIONS: dict[int, ReceiptSession] = {}


def init_sessions(session_store: SessionStore) -> None:
    SESSIONS.clear()
    SESSIONS.update(session_store.load_all())


async def safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None) -> None:
    try:
        await context.bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        LOGGER.warning("Failed to send Telegram message to chat_id=%s", chat_id)


async def send_text_chunks(reply_target, text: str, reply_markup=None) -> None:
    chunks = split_text(text)
    for index, chunk in enumerate(chunks):
        await reply_target.reply_text(chunk, reply_markup=reply_markup if index == len(chunks) - 1 else None)


def split_text(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        if current and current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


def save_session(session: ReceiptSession, context: ContextTypes.DEFAULT_TYPE) -> None:
    SESSIONS[session.user_id] = session
    sessions(context).save(session)


def delete_session(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    SESSIONS.pop(user_id, None)
    sessions(context).delete(user_id)


def settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def access(context: ContextTypes.DEFAULT_TYPE) -> AccessControl:
    return context.application.bot_data["access_control"]


def sessions(context: ContextTypes.DEFAULT_TYPE) -> SessionStore:
    return context.application.bot_data["session_store"]


def corrections(context: ContextTypes.DEFAULT_TYPE) -> CorrectionStore:
    return context.application.bot_data["correction_store"]


def quotas(context: ContextTypes.DEFAULT_TYPE) -> QuotaService:
    return context.application.bot_data["quota_service"]


def receipts(context: ContextTypes.DEFAULT_TYPE) -> ReceiptRepository:
    return context.application.bot_data["receipt_repository"]

