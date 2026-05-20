from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.users.models import AccessRequest


def access_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"access_approve:{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"access_reject:{user_id}"),
            ]
        ]
    )


def access_request_text(request: AccessRequest) -> str:
    username = f"@{request.username}" if request.username else "не указан"
    return "\n".join(
        [
            "Новая заявка на доступ:",
            f"Name: {request.full_name}",
            f"Username: {username}",
            f"User ID: {request.user_id}",
        ]
    )

