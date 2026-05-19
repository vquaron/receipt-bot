from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    openai_api_key: str
    obsidian_vault: Path
    data_dir: Path
    admin_telegram_user_ids: frozenset[int]
    allowed_telegram_user_ids: frozenset[int]
    openai_model: str = "gpt-5.4-mini"
    bot_mode: str = "polling"
    webhook_url: str = ""
    webhook_listen: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_secret_token: str = ""


def load_settings() -> Settings:
    env_file_values = dotenv_values(PROJECT_ROOT / ".env")

    obsidian_vault = Path(_required("OBSIDIAN_VAULT", env_file_values)).expanduser()
    if not obsidian_vault.exists():
        raise RuntimeError("OBSIDIAN_VAULT does not exist.")
    if not obsidian_vault.is_dir():
        raise RuntimeError("OBSIDIAN_VAULT must point to a directory.")

    data_dir = Path(_get("DATA_DIR", env_file_values) or PROJECT_ROOT / "data").expanduser()
    bot_mode = (_get("BOT_MODE", env_file_values) or "polling").lower()
    if bot_mode not in {"polling", "webhook"}:
        raise RuntimeError("BOT_MODE must be polling or webhook.")

    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN", env_file_values),
        openai_api_key=_required("OPENAI_API_KEY", env_file_values),
        obsidian_vault=obsidian_vault,
        data_dir=data_dir,
        admin_telegram_user_ids=_int_set("ADMIN_TELEGRAM_USER_IDS", env_file_values),
        allowed_telegram_user_ids=_int_set("ALLOWED_TELEGRAM_USER_IDS", env_file_values),
        openai_model=_get("OPENAI_MODEL", env_file_values) or "gpt-5.4-mini",
        bot_mode=bot_mode,
        webhook_url=_get("WEBHOOK_URL", env_file_values) or "",
        webhook_listen=_get("WEBHOOK_LISTEN", env_file_values) or "0.0.0.0",
        webhook_port=_int("WEBHOOK_PORT", env_file_values, 8080),
        webhook_secret_token=_get("WEBHOOK_SECRET_TOKEN", env_file_values) or "",
    )


def _required(name: str, env_file_values: dict[str, str | None]) -> str:
    value = _get(name, env_file_values)
    if not value:
        raise RuntimeError(f"{name} is not set. Create .env from .env.example.")
    return value


def _get(name: str, env_file_values: dict[str, str | None]) -> str | None:
    file_value = os.getenv(f"{name}_FILE") or env_file_values.get(f"{name}_FILE")
    if file_value:
        return Path(file_value).expanduser().read_text(encoding="utf-8").strip()
    return os.getenv(name) or env_file_values.get(name)


def _int(name: str, env_file_values: dict[str, str | None], default: int) -> int:
    raw_value = _get(name, env_file_values)
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc


def _int_set(name: str, env_file_values: dict[str, str | None]) -> frozenset[int]:
    raw_value = _get(name, env_file_values) or ""
    result: set[int] = set()
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError as exc:
            raise RuntimeError(f"{name} must contain comma-separated integer IDs.") from exc
    return frozenset(result)
