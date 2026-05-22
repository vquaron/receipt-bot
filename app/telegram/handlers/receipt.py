from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from telegram import Update
from telegram.ext import ContextTypes

from app.llm.openai_parser import OpenAIInvalidJSONError, OpenAIQuotaError
from app.obsidian.writer import write_openai_debug_file
from app.ocr.google_vision import (
    GoogleVisionCredentialsError,
    GoogleVisionError,
    GoogleVisionNetworkError,
)
from app.pipeline.receipt_pipeline import parse_for_review, run_ocr
from app.receipts.document_classifier import classify_document_type
from app.receipts.document_types import (
    DOCUMENT_TYPE_ORDER,
    DOCUMENT_TYPE_RECEIPT,
    document_genitive_ru,
    document_prepositional_ru,
    normalize_document_type,
)
from app.review.models import ReceiptSession, SessionState, review_keyboard
from app.review.receipt_review import (
    ReviewPayloadError,
    merge_review_payload,
    parse_review_payload,
    render_review_text,
    review_payload_json,
)
from app.telegram.handlers.access import ensure_access, handle_access_callback
from app.telegram.handlers.common import (
    SESSIONS,
    access,
    corrections,
    delete_session,
    quotas,
    receipts,
    save_session,
    send_text_chunks,
    sessions,
    settings,
)
from app.storage.sessions import SessionStorageError, session_temp_dir
from app.users.quotas import QuotaStorageError


LOGGER = logging.getLogger(__name__)
NEXT_DOCUMENT_TYPE_KEY = "next_document_type"


async def order_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not await ensure_access(update, context):
        return
    context.user_data[NEXT_DOCUMENT_TYPE_KEY] = DOCUMENT_TYPE_ORDER
    await update.message.reply_text(
        "Отправьте скриншот заказа. Я пропущу ингредиенты, описания и UI-шум, "
        "а в заметку оставлю товары, количество, цену и сумму."
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None or not update.message.photo:
        return
    if not await ensure_access(update, context):
        return

    user_id = update.effective_user.id
    if _has_active_session(user_id):
        await update.message.reply_text("У вас уже есть активная обработка. Подтвердите или отмените текущую сессию.")
        return

    document_type, explicit_document_type = _consume_document_type(update, context)
    document_genitive = document_genitive_ru(document_type)
    document_prepositional = document_prepositional_ru(document_type)
    role = access(context).role_for(user_id)
    try:
        quota = quotas(context).check_and_record_attempt(user_id, role, document_type=document_type)
    except QuotaStorageError:
        LOGGER.exception("Quota check failed for user_id=%s", user_id)
        await update.message.reply_text("Не удалось проверить лимит обработки. Попробуйте позже.")
        return
    if not quota.allowed:
        await update.message.reply_text(_quota_message(quota.reason, quota.daily_used, quota.daily_limit, quota.monthly_used, quota.monthly_limit))
        return

    app_settings = settings(context)
    created_at = datetime.now()
    session_id = uuid4().hex
    temporary_base_name = f"{created_at:%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    temp_dir = session_temp_dir(app_settings, session_id)
    image_path = temp_dir / "original.jpg"
    clean_ocr_path = temp_dir / "clean.hy.txt"
    source_ocr_path = temp_dir / "source.hy.txt"
    session = ReceiptSession(
        session_id=session_id,
        user_id=user_id,
        image_path=image_path,
        clean_ocr_path=clean_ocr_path,
        source_ocr_path=source_ocr_path,
        temporary_base_name=temporary_base_name,
        created_at=created_at,
        document_type=document_type,
        state=SessionState.PROCESSING_OCR,
    )
    try:
        save_session(session, context)
    except SessionStorageError:
        sessions(context).cleanup_session_temp(session_id)
        LOGGER.exception("Failed to persist processing session before download user_id=%s", user_id)
        await update.message.reply_text("Не удалось создать сессию обработки. Попробуйте позже.")
        return

    try:
        telegram_file = await update.message.photo[-1].get_file()
        await telegram_file.download_to_drive(custom_path=image_path)
    except OSError:
        LOGGER.exception("Failed to save Telegram photo for user_id=%s", user_id)
        delete_session(user_id, context, final_state=SessionState.FAILED)
        if explicit_document_type:
            await update.message.reply_text(f"Не удалось сохранить изображение {document_genitive}.")
        else:
            await update.message.reply_text("Не удалось сохранить изображение.")
        return

    if explicit_document_type:
        await update.message.reply_text(f"Фото получено. Распознаю текст {document_genitive}...")
    else:
        await update.message.reply_text("Фото получено. Распознаю текст изображения...")
    try:
        _raw_ocr, clean_ocr = await asyncio.to_thread(run_ocr, image_path)
    except GoogleVisionCredentialsError:
        LOGGER.exception("Google Vision ADC credentials are missing.")
        delete_session(user_id, context, final_state=SessionState.FAILED)
        await update.message.reply_text("Не найдены Google ADC credentials.")
        return
    except GoogleVisionNetworkError:
        LOGGER.exception("Google Vision network error.")
        delete_session(user_id, context, final_state=SessionState.FAILED)
        await update.message.reply_text("Не удалось подключиться к Google Vision API.")
        return
    except GoogleVisionError:
        LOGGER.exception("Google Vision returned an error.")
        delete_session(user_id, context, final_state=SessionState.FAILED)
        await update.message.reply_text("Google Vision не смог обработать изображение.")
        return
    except Exception:
        LOGGER.exception("Unexpected OCR failure.")
        delete_session(user_id, context, final_state=SessionState.FAILED)
        await update.message.reply_text("Не удалось выполнить OCR.")
        return

    if not clean_ocr:
        delete_session(user_id, context, final_state=SessionState.FAILED)
        if explicit_document_type:
            await update.message.reply_text(f"Не удалось распознать текст на {document_prepositional}. OpenAI не вызывался.")
        else:
            await update.message.reply_text("Не удалось распознать текст на изображении. OpenAI не вызывался.")
        return
    if not explicit_document_type:
        classification = classify_document_type(clean_ocr)
        document_type = classification.document_type
        document_genitive = document_genitive_ru(document_type)
        if quota.event_id is not None:
            try:
                quotas(context).update_attempt_document_type(quota.event_id, document_type)
            except QuotaStorageError:
                LOGGER.warning(
                    "Failed to update quota event document_type user_id=%s event_id=%s document_type=%s",
                    user_id,
                    quota.event_id,
                    document_type,
                    exc_info=True,
                )
        LOGGER.info(
            "Detected document type for user_id=%s type=%s confidence=%.2f reason=%s receipt_score=%s order_score=%s",
            user_id,
            document_type,
            classification.confidence,
            classification.reason,
            classification.receipt_score,
            classification.order_score,
        )

    try:
        clean_ocr_path.write_text(clean_ocr, encoding="utf-8")
        source_ocr_path.write_text(clean_ocr, encoding="utf-8")
    except OSError:
        LOGGER.exception("Failed to write OCR files for user_id=%s", user_id)
        delete_session(user_id, context, final_state=SessionState.FAILED)
        await update.message.reply_text("Не удалось сохранить OCR для обработки.")
        return

    session.document_type = document_type
    session.state = SessionState.PROCESSING_OPENAI
    try:
        save_session(session, context)
    except SessionStorageError:
        LOGGER.exception("Failed to persist processing session after OCR user_id=%s", user_id)
        delete_session(user_id, context, final_state=SessionState.FAILED)
        await update.message.reply_text("Не удалось сохранить сессию обработки.")
        return

    await update.message.reply_photo(photo=image_path)
    await update.message.reply_text(f"OCR готов. Извлекаю поля {document_genitive} через OpenAI...")
    await process_openai_for_review(session, update.message, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    if query.data and query.data.startswith("access_"):
        await handle_access_callback(update, context)
        return
    session = SESSIONS.get(update.effective_user.id)
    if session is None:
        await query.message.reply_text("Нет активной обработки чека.")
        return
    if query.data == "review_cancel":
        delete_session(session.user_id, context, final_state=SessionState.CANCELLED)
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
        try:
            save_session(session, context)
        except SessionStorageError:
            LOGGER.exception("Failed to persist corrected-review state user_id=%s", session.user_id)
            await query.message.reply_text("Не удалось сохранить состояние проверки. Попробуйте позже.")
            return
        await query.message.reply_text("Отправьте исправленный JSON. Меняйте только значения.")
        await send_text_chunks(query.message, review_payload_json(session.parsed_receipt))
        return
    if query.data == "review_confirm":
        await create_note_from_review(session, query.message, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not await ensure_access(update, context):
        return
    session = SESSIONS.get(update.effective_user.id)
    if session is None:
        if normalize_document_type(context.user_data.get(NEXT_DOCUMENT_TYPE_KEY)) == DOCUMENT_TYPE_ORDER:
            await update.message.reply_text("Отправьте скриншот заказа.")
        else:
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
        learned_count = corrections(context).learn(session.parsed_receipt, corrected)
        session.parsed_receipt = corrected
        try:
            save_session(session, context)
        except SessionStorageError:
            LOGGER.exception("Failed to persist corrected review payload user_id=%s", session.user_id)
            await update.message.reply_text("Не удалось сохранить исправления. Попробуйте позже.")
            return
        await update.message.reply_text(f"Исправления приняты. Новых правил замен: {learned_count}.")
        await create_note_from_review(session, update.message, context)
        return
    if session.state == SessionState.WAITING_FOR_RUSSIAN_REVIEW:
        await update.message.reply_text("Используйте кнопки под полями заметки.")
        return
    await update.message.reply_text("Отправьте новое фото чека или используйте /order для скриншота заказа.")


async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if update.effective_user is None or not await ensure_access(update, context):
        return
    if update.message.text:
        return
    if normalize_document_type(context.user_data.get(NEXT_DOCUMENT_TYPE_KEY)) == DOCUMENT_TYPE_ORDER:
        await update.message.reply_text("Пожалуйста, отправьте скриншот заказа.")
    else:
        await update.message.reply_text("Пожалуйста, отправьте фото чека.")


async def process_openai_for_review(session: ReceiptSession, reply_target, context: ContextTypes.DEFAULT_TYPE) -> None:
    app_settings = settings(context)
    try:
        parsed = await asyncio.to_thread(
            parse_for_review,
            session.source_ocr_path.read_text(encoding="utf-8"),
            settings=app_settings,
            correction_store=corrections(context),
            document_type=session.document_type,
        )
    except OpenAIInvalidJSONError as exc:
        LOGGER.exception("OpenAI returned invalid JSON.")
        await asyncio.to_thread(write_openai_debug_file, app_settings, session, exc.raw_response)
        delete_session(session.user_id, context, final_state=SessionState.FAILED)
        await reply_target.reply_text("OpenAI вернул невалидный JSON. Markdown-заметка не создана.")
        return
    except OpenAIQuotaError:
        LOGGER.exception("OpenAI quota is exhausted.")
        delete_session(session.user_id, context, final_state=SessionState.FAILED)
        await reply_target.reply_text("OpenAI не обработал чек: закончилась квота или не настроен billing.")
        return
    except Exception:
        LOGGER.exception("OpenAI request failed.")
        delete_session(session.user_id, context, final_state=SessionState.FAILED)
        await reply_target.reply_text("Не удалось обработать OCR через OpenAI.")
        return
    session.parsed_receipt = parsed.data
    session.state = SessionState.WAITING_FOR_RUSSIAN_REVIEW
    try:
        save_session(session, context)
    except SessionStorageError:
        LOGGER.exception("Failed to persist review session user_id=%s", session.user_id)
        delete_session(session.user_id, context, final_state=SessionState.FAILED)
        await reply_target.reply_text("Не удалось сохранить сессию проверки.")
        return
    await send_text_chunks(reply_target, render_review_text(parsed.data), reply_markup=review_keyboard())


async def create_note_from_review(session: ReceiptSession, reply_target, context: ContextTypes.DEFAULT_TYPE) -> None:
    if session.parsed_receipt is None:
        await reply_target.reply_text("Нет проверенных полей заметки.")
        return
    try:
        result = await asyncio.to_thread(receipts(context).documents.create_confirmed_from_session, session, session.parsed_receipt)
    except Exception:
        LOGGER.exception("Unexpected DB-first document finalization failure.")
        delete_session(session.user_id, context, final_state=SessionState.FAILED)
        await reply_target.reply_text("Не удалось сохранить документ.")
        return
    delete_session(session.user_id, context, final_state=SessionState.DONE)
    record = result.record
    await reply_target.reply_text(
        "\n".join(
            [
                f"Готово: создана заметка {record.receipt_id}.md",
                f"receipt_id: {record.receipt_id}",
                f"merchant: {record.merchant}",
                f"date: {record.date}",
                f"amount: {record.amount or 'unknown_amount'}",
                f"currency: {record.currency}",
            ]
        )
    )


def _quota_message(reason: str, daily_used: int, daily_limit: int, monthly_used: int, monthly_limit: int) -> str:
    if reason == "daily_limit":
        return f"Дневной лимит попыток обработки чеков исчерпан: {daily_used}/{daily_limit}."
    if reason == "monthly_limit":
        return f"Месячный лимит попыток обработки чеков исчерпан: {monthly_used}/{monthly_limit}."
    return "Лимит попыток обработки чеков исчерпан."


def _consume_document_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[str, bool]:
    caption = update.message.caption if update.message else ""
    caption_command = caption.strip().split(maxsplit=1)[0].lower() if caption else ""
    if caption_command == "/order" or caption_command.startswith("/order@"):
        context.user_data.pop(NEXT_DOCUMENT_TYPE_KEY, None)
        return DOCUMENT_TYPE_ORDER, True
    if NEXT_DOCUMENT_TYPE_KEY in context.user_data:
        queued = context.user_data.pop(NEXT_DOCUMENT_TYPE_KEY)
        return normalize_document_type(queued), True
    return DOCUMENT_TYPE_RECEIPT, False


def _has_active_session(user_id: int) -> bool:
    session = SESSIONS.get(user_id)
    if session is None:
        return False
    return session.state not in {SessionState.DONE, SessionState.CANCELLED, SessionState.FAILED}
