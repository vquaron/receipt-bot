from __future__ import annotations

import logging

from app.config import Settings


class SecretRedactionFilter(logging.Filter):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.secrets = [
            value
            for value in (
                settings.telegram_bot_token,
                settings.openai_api_key,
                settings.webhook_secret_token,
            )
            if value
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = logging.getLogger()
    redaction_filter = SecretRedactionFilter(settings)
    root.addFilter(redaction_filter)
    for handler in root.handlers:
        handler.addFilter(redaction_filter)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
