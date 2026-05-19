from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings, load_settings
from app.llm.openai_parser import OpenAIInvalidJSONError, OpenAIQuotaError
from app.obsidian.delete import ReceiptDeleteError, delete_receipt
from app.obsidian.writer import write_openai_debug_file, write_receipt_note
from app.ocr.google_vision import (
    GoogleVisionCredentialsError,
    GoogleVisionError,
    GoogleVisionNetworkError,
)
from app.pipeline.receipt_pipeline import parse_for_review, run_ocr
from app.preprocessing import preprocess_receipt_image
from app.review.models import ReceiptSession, SessionState, review_keyboard
from app.review.receipt_review import (
    ReviewPayloadError,
    merge_review_payload,
    parse_review_payload,
    render_review_text,
    review_payload_json,
)
from app.security.access_control import AccessControl, access_keyboard, access_request_text
from app.storage.corrections import CorrectionStore
from app.storage.paths import dated_relpath, ensure_parent
from app.storage.sessions import SessionStore
from app.telegram.logging import configure_logging


LOGGER = logging.getLogger(__name__)
SESSIONS: dict[int, ReceiptSession] = {}


def main() -> None:
    settings = load_settings()
    configure_logging(settings)
    application = build_application(settings)
    if settings.bot_mode == "webhook":
        if not settings.webhook_url or not settings.webhook_secret_token:
            raise RuntimeError("WEBHOOK_URL and WEBHOOK_SECRET_TOKEN are required for webhook mode.")
        application.run_webhook(
            listen=settings.webhook_listen,
            port=settings.webhook_port,
            url_path="telegram-webhook",
            webhook_url=settings.webhook_url,
            secret_token=settings.webhook_secret_token,
        )
    else:
        application.run_polling()


def build_application(settings: Settings) -> Application:
    session_store = SessionStore(settings.data_dir)
    SESSIONS.clear()
    SESSIONS.update(session_store.load_all())

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["access_control"] = AccessControl(settings)
    application.bot_data["session_store"] = session_store
    application.bot_data["correction_store"] = CorrectionStore(settings.data_dir)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("whoami", whoami_command))
    application.add_handler(CommandHandler("access", access_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_handler(CommandHandler("delete_receipt", delete_receipt_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(~filters.PHOTO, handle_non_photo))
    return application


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    _delete_session(update.effective_user.id, context)
    if not await _ensure_access(update, context):
        return
    await update.message.reply_text("Отправьте фото чека.")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        await update.message.reply_text(f"Ваш Telegram user_id: {update.effective_user.id}")


async def access_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.message:
        await _request_access(update, context)


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    access = _access(context)
    if not access.is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администратору.")
        return
    await update.message.reply_text(access.allowed_users_text())


async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    access = _access(context)
    if not access.is_admin(update.effective_user.id):
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
    if access.revoke(user_id):
        await update.message.reply_text(f"Доступ отозван: {user_id}")
        try:
            await context.bot.send_message(user_id, "Доступ отозван.")
        except Exception:
            LOGGER.warning("Failed to notify revoked user_id=%s", user_id)
    else:
        await update.message.reply_text("Пользователь не был в allowlist или является админом.")


async def delete_receipt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await _ensure_access(update, context):
        return
    note_name = " ".join(context.args).strip()
    if not note_name:
        await update.message.reply_text("Укажите заметку: /delete_receipt Receipts/YYYY/MM/file.md")
        return
    try:
        result = await asyncio.to_thread(delete_receipt, _settings(context).obsidian_vault, note_name)
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None or not update.message.photo:
        return
    if not await _ensure_access(update, context):
        return

    settings = _settings(context)
    created_at = datetime.now()
    temporary_base_name = f"{created_at:%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    image_path = settings.obsidian_vault / dated_relpath(
        "Attachments/receipts/_tmp",
        created_at,
        f"{temporary_base_name}.jpg",
    )
    ensure_parent(image_path)

    try:
        telegram_file = await update.message.photo[-1].get_file()
        await telegram_file.download_to_drive(custom_path=image_path)
    except OSError:
        LOGGER.exception("Failed to save Telegram photo for user_id=%s", update.effective_user.id)
        await update.message.reply_text("Не удалось сохранить изображение чека.")
        return

    await update.message.reply_text("Фото получено. Распознаю текст чека...")
    preprocessing_temp_dir = settings.obsidian_vault / dated_relpath(
        "Attachments/receipts_preprocessed/_tmp",
        created_at,
        "",
    )
    preprocessing_result = await asyncio.to_thread(
        preprocess_receipt_image,
        image_path,
        preprocessing_temp_dir,
        settings,
    )
    image_for_ocr = preprocessing_result.used_for_ocr or image_path

    try:
        _raw_ocr, clean_ocr = await asyncio.to_thread(run_ocr, image_for_ocr)
    except GoogleVisionCredentialsError:
        LOGGER.exception("Google Vision ADC credentials are missing.")
        await update.message.reply_text("Не найдены Google ADC credentials.")
        return
    except GoogleVisionNetworkError:
        LOGGER.exception("Google Vision network error.")
        await update.message.reply_text("Не удалось подключиться к Google Vision API.")
        return
    except GoogleVisionError:
        LOGGER.exception("Google Vision returned an error.")
        await update.message.reply_text("Google Vision не смог обработать изображение.")
        return
    except Exception:
        LOGGER.exception("Unexpected OCR failure.")
        await update.message.reply_text("Не удалось выполнить OCR.")
        return

    if not clean_ocr:
        await update.message.reply_text("Не удалось распознать текст на чеке. OpenAI не вызывался.")
        return

    clean_ocr_path = settings.obsidian_vault / dated_relpath("OCR", created_at, f"{temporary_base_name}.clean.hy.txt")
    source_ocr_path = settings.obsidian_vault / dated_relpath(
        "OCR_VERIFIED",
        created_at,
        f"{temporary_base_name}.verified.hy.txt",
    )
    try:
        ensure_parent(clean_ocr_path)
        ensure_parent(source_ocr_path)
        clean_ocr_path.write_text(clean_ocr, encoding="utf-8")
        source_ocr_path.write_text(clean_ocr, encoding="utf-8")
    except OSError:
        LOGGER.exception("Failed to write OCR files for user_id=%s", update.effective_user.id)
        await update.message.reply_text("Не удалось сохранить OCR для обработки.")
        return

    session = ReceiptSession(
        user_id=update.effective_user.id,
        image_path=image_path,
        clean_ocr_path=clean_ocr_path,
        source_ocr_path=source_ocr_path,
        temporary_base_name=temporary_base_name,
        created_at=created_at,
        preprocessing_result=preprocessing_result,
    )
    _save_session(session, context)

    await update.message.reply_photo(photo=image_path)
    await update.message.reply_text("OCR готов. Извлекаю поля заметки через OpenAI...")
    await _process_openai_for_review(session, update.message, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    if query.data and query.data.startswith("access_"):
        await _handle_access_callback(update, context)
        return
    session = SESSIONS.get(update.effective_user.id)
    if session is None:
        await query.message.reply_text("Нет активной обработки чека.")
        return
    if query.data == "review_cancel":
        _delete_session(session.user_id, context)
        await query.message.reply_text("Обработка отменена")
        return
    if session.state != SessionState.WAITING_FOR_RUSSIAN_REVIEW:
        await query.message.reply_text("Этот шаг уже завершён.")
        return
    if query.data == "review_edit":
        if session.parsed_receipt is None:
            await query.message.reply_text("Нет полей заметки для исправления.")
            return
        session.state = SessionState.WAITING_FOR_CORRECTED_REVIEW
        _save_session(session, context)
        await query.message.reply_text("Отправьте исправленный JSON. Меняйте только значения.")
        await _send_text_chunks(query.message, review_payload_json(session.parsed_receipt))
        return
    if query.data == "review_confirm":
        await _create_note_from_review(session, query.message, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not await _ensure_access(update, context):
        return
    session = SESSIONS.get(update.effective_user.id)
    if session is None:
        await update.message.reply_text("Сначала отправьте фото чека.")
        return
    if session.state == SessionState.WAITING_FOR_CORRECTED_REVIEW:
        if session.parsed_receipt is None:
            await update.message.reply_text("Нет активных полей заметки для исправления.")
            return
        try:
            payload = parse_review_payload(update.message.text)
        except ReviewPayloadError as exc:
            await update.message.reply_text(f"Не удалось принять исправления: {exc}")
            return
        corrected = merge_review_payload(session.parsed_receipt, payload)
        learned_count = _corrections(context).learn(session.parsed_receipt, corrected)
        session.parsed_receipt = corrected
        _save_session(session, context)
        await update.message.reply_text(f"Исправления приняты. Новых правил замен: {learned_count}.")
        await _create_note_from_review(session, update.message, context)
        return
    if session.state == SessionState.WAITING_FOR_RUSSIAN_REVIEW:
        await update.message.reply_text("Используйте кнопки под полями заметки.")
        return
    await update.message.reply_text("Отправьте новое фото чека.")


async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await _ensure_access(update, context):
        return
    if update.message.text:
        return
    await update.message.reply_text("Пожалуйста, отправьте фото чека.")


async def _process_openai_for_review(session: ReceiptSession, reply_target, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(context)
    try:
        parsed = await asyncio.to_thread(
            parse_for_review,
            session.source_ocr_path.read_text(encoding="utf-8"),
            settings=settings,
            correction_store=_corrections(context),
        )
    except OpenAIInvalidJSONError as exc:
        LOGGER.exception("OpenAI returned invalid JSON.")
        await asyncio.to_thread(write_openai_debug_file, settings, session, exc.raw_response)
        session.state = SessionState.DONE
        _save_session(session, context)
        await reply_target.reply_text("OpenAI вернул невалидный JSON. Markdown-заметка не создана.")
        return
    except OpenAIQuotaError:
        LOGGER.exception("OpenAI quota is exhausted.")
        session.state = SessionState.DONE
        _save_session(session, context)
        await reply_target.reply_text("OpenAI не обработал чек: закончилась квота или не настроен billing.")
        return
    except Exception:
        LOGGER.exception("OpenAI request failed.")
        session.state = SessionState.DONE
        _save_session(session, context)
        await reply_target.reply_text("Не удалось обработать OCR через OpenAI.")
        return
    session.parsed_receipt = parsed.data
    session.state = SessionState.WAITING_FOR_RUSSIAN_REVIEW
    _save_session(session, context)
    await _send_text_chunks(reply_target, render_review_text(parsed.data), reply_markup=review_keyboard())


async def _create_note_from_review(session: ReceiptSession, reply_target, context: ContextTypes.DEFAULT_TYPE) -> None:
    if session.parsed_receipt is None:
        await reply_target.reply_text("Нет проверенных полей заметки.")
        return
    try:
        artifact = await asyncio.to_thread(write_receipt_note, _settings(context), session, session.parsed_receipt)
    except Exception:
        LOGGER.exception("Unexpected note generation failure.")
        session.state = SessionState.DONE
        _save_session(session, context)
        await reply_target.reply_text("Не удалось создать Markdown-заметку.")
        return
    session.state = SessionState.DONE
    _delete_session(session.user_id, context)
    await reply_target.reply_text(
        "\n".join(
            [
                f"Готово: создана заметка {artifact.file_name}",
                f"merchant: {artifact.merchant}",
                f"date: {artifact.date}",
                f"amount: {artifact.amount}",
                f"currency: {artifact.currency}",
            ]
        )
    )


async def _handle_access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None or query.data is None:
        return
    access = _access(context)
    if not access.is_admin(update.effective_user.id):
        await query.message.reply_text("Только администратор может управлять доступом.")
        return
    action, raw_user_id = query.data.split(":", 1)
    try:
        user_id = int(raw_user_id)
    except ValueError:
        await query.message.reply_text("Некорректный user_id в заявке.")
        return
    if action == "access_approve":
        access.approve(user_id)
        await query.edit_message_text(f"Доступ одобрен для user_id {user_id}.")
        await _safe_send(context, user_id, "Доступ одобрен. Теперь можно отправлять чеки.")
    elif action == "access_reject":
        access.reject(user_id)
        await query.edit_message_text(f"Доступ отклонён для user_id {user_id}.")
        await _safe_send(context, user_id, "Доступ отклонён.")


async def _ensure_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user is None:
        return False
    if _access(context).is_allowed(update.effective_user.id):
        return True
    await _request_access(update, context)
    return False


async def _request_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message is None:
        return
    access = _access(context)
    if access.is_allowed(update.effective_user.id):
        await message.reply_text("Доступ уже одобрен. Можно отправлять чеки.")
        return
    request, created = access.create_request(update.effective_user)
    if created:
        admin_ids = _settings(context).admin_telegram_user_ids
        for admin_id in admin_ids:
            await _safe_send(context, admin_id, access_request_text(request), access_keyboard(request.user_id))
        if admin_ids:
            await message.reply_text("Заявка на доступ отправлена администратору.")
        else:
            await message.reply_text("Заявка на доступ создана, но ADMIN_TELEGRAM_USER_IDS пока не настроен.")
        return
    if access.is_pending(update.effective_user.id):
        await message.reply_text("Заявка на доступ уже ожидает решения администратора.")
    else:
        await message.reply_text("Доступ отклонён.")


async def _safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None) -> None:
    try:
        await context.bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        LOGGER.warning("Failed to send Telegram message to chat_id=%s", chat_id)


async def _send_text_chunks(reply_target, text: str, reply_markup=None) -> None:
    chunks = _split_text(text)
    for index, chunk in enumerate(chunks):
        await reply_target.reply_text(chunk, reply_markup=reply_markup if index == len(chunks) - 1 else None)


def _split_text(text: str, limit: int = 3500) -> list[str]:
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


def _save_session(session: ReceiptSession, context: ContextTypes.DEFAULT_TYPE) -> None:
    SESSIONS[session.user_id] = session
    _sessions(context).save(session)


def _delete_session(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    SESSIONS.pop(user_id, None)
    _sessions(context).delete(user_id)


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _access(context: ContextTypes.DEFAULT_TYPE) -> AccessControl:
    return context.application.bot_data["access_control"]


def _sessions(context: ContextTypes.DEFAULT_TYPE) -> SessionStore:
    return context.application.bot_data["session_store"]


def _corrections(context: ContextTypes.DEFAULT_TYPE) -> CorrectionStore:
    return context.application.bot_data["correction_store"]
