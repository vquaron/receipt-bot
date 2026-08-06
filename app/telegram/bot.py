from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.config import Settings, load_settings
from app.db import initialize_database
from app.receipts.repository import ReceiptRepository
from app.security.access_control import AccessControl
from app.storage.corrections import CorrectionStore
from app.storage.retention import cleanup_runtime_storage
from app.storage.sessions import SessionStore
from app.telegram.handlers.access import (
    access_command,
    revoke_command,
    start,
    users_command,
    whoami_command,
)
from app.telegram.handlers.common import init_sessions
from app.telegram.handlers.delete import delete_receipt_command
from app.telegram.handlers.receipt import (
    handle_callback,
    handle_non_photo,
    handle_photo,
    handle_text,
    order_command,
)
from app.telegram.handlers.receipts import (
    export_receipts_command,
    grant_receipt_command,
    my_receipts_command,
    receipt_command,
)
from app.telegram.handlers.storage import purge_legacy_manifests_command, storage_health_command
from app.telegram.handlers.web import web_command
from app.telegram.logging import configure_logging
from app.users.quotas import QuotaService


def main() -> None:
    settings = load_settings()
    configure_logging(settings)
    initialize_database(settings)
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
    session_store = SessionStore(settings)
    cleanup_runtime_storage(settings)
    init_sessions(session_store)

    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["access_control"] = AccessControl(settings)
    application.bot_data["session_store"] = session_store
    application.bot_data["correction_store"] = CorrectionStore(settings)
    application.bot_data["quota_service"] = QuotaService(settings)
    application.bot_data["receipt_repository"] = ReceiptRepository(settings)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("whoami", whoami_command))
    application.add_handler(CommandHandler("access", access_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_handler(CommandHandler("delete_receipt", delete_receipt_command))
    application.add_handler(CommandHandler("my_receipts", my_receipts_command))
    application.add_handler(CommandHandler("receipt", receipt_command))
    application.add_handler(CommandHandler("export_receipts", export_receipts_command))
    application.add_handler(CommandHandler("grant_receipt", grant_receipt_command))
    application.add_handler(CommandHandler("storage_health", storage_health_command))
    application.add_handler(CommandHandler("purge_legacy_manifests", purge_legacy_manifests_command))
    application.add_handler(CommandHandler("web", web_command))
    application.add_handler(CommandHandler("order", order_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(~filters.PHOTO, handle_non_photo))
    return application
