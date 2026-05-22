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
    privileged_telegram_user_ids: frozenset[int] = frozenset()
    openai_model: str = "gpt-5.4-mini"
    user_vault_root: str = "Users"
    regular_daily_receipt_limit: int = 10
    regular_monthly_receipt_limit: int = 100
    privileged_daily_receipt_limit: int = 0
    privileged_monthly_receipt_limit: int = 0
    bot_mode: str = "polling"
    webhook_url: str = ""
    webhook_listen: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_secret_token: str = ""
    database_url: str = "sqlite:///data/app.db"
    db_busy_timeout_ms: int = 5000
    app_storage_dir: Path = Path("data/storage")
    tmp_storage_dir: Path = Path("data/tmp")
    export_storage_dir: Path = Path("data/exports")
    debug_storage_dir: Path = Path("data/debug")
    storage_retention_tmp_hours: int = 24

    def __post_init__(self) -> None:
        data_dir = _absolute_path(self.data_dir)
        object.__setattr__(self, "data_dir", data_dir)
        if self.database_url == "sqlite:///data/app.db":
            object.__setattr__(self, "database_url", _sqlite_url(data_dir / "app.db"))
        object.__setattr__(
            self,
            "app_storage_dir",
            _storage_path(self.app_storage_dir, Path("data/storage"), data_dir / "storage"),
        )
        object.__setattr__(
            self,
            "tmp_storage_dir",
            _storage_path(self.tmp_storage_dir, Path("data/tmp"), data_dir / "tmp"),
        )
        object.__setattr__(
            self,
            "export_storage_dir",
            _storage_path(self.export_storage_dir, Path("data/exports"), data_dir / "exports"),
        )
        object.__setattr__(
            self,
            "debug_storage_dir",
            _storage_path(self.debug_storage_dir, Path("data/debug"), data_dir / "debug"),
        )


def load_settings() -> Settings:
    env_file_values = dotenv_values(PROJECT_ROOT / ".env")

    obsidian_vault = Path(_required("OBSIDIAN_VAULT", env_file_values)).expanduser()
    if not obsidian_vault.exists():
        raise RuntimeError("OBSIDIAN_VAULT does not exist.")
    if not obsidian_vault.is_dir():
        raise RuntimeError("OBSIDIAN_VAULT must point to a directory.")

    data_dir = _path("DATA_DIR", env_file_values, PROJECT_ROOT / "data")
    database_url = _get("DATABASE_URL", env_file_values) or _sqlite_url(data_dir / "app.db")
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
        privileged_telegram_user_ids=_int_set("PRIVILEGED_TELEGRAM_USER_IDS", env_file_values),
        openai_model=_get("OPENAI_MODEL", env_file_values) or "gpt-5.4-mini",
        user_vault_root=_get("USER_VAULT_ROOT", env_file_values) or "Users",
        regular_daily_receipt_limit=_int("REGULAR_DAILY_RECEIPT_LIMIT", env_file_values, 10),
        regular_monthly_receipt_limit=_int("REGULAR_MONTHLY_RECEIPT_LIMIT", env_file_values, 100),
        privileged_daily_receipt_limit=_int("PRIVILEGED_DAILY_RECEIPT_LIMIT", env_file_values, 0),
        privileged_monthly_receipt_limit=_int("PRIVILEGED_MONTHLY_RECEIPT_LIMIT", env_file_values, 0),
        bot_mode=bot_mode,
        webhook_url=_get("WEBHOOK_URL", env_file_values) or "",
        webhook_listen=_get("WEBHOOK_LISTEN", env_file_values) or "0.0.0.0",
        webhook_port=_int("WEBHOOK_PORT", env_file_values, 8080),
        webhook_secret_token=_get("WEBHOOK_SECRET_TOKEN", env_file_values) or "",
        database_url=database_url,
        db_busy_timeout_ms=_int("DB_BUSY_TIMEOUT_MS", env_file_values, 5000),
        app_storage_dir=_path("APP_STORAGE_DIR", env_file_values, data_dir / "storage"),
        tmp_storage_dir=_path("TMP_STORAGE_DIR", env_file_values, data_dir / "tmp"),
        export_storage_dir=_path("EXPORT_STORAGE_DIR", env_file_values, data_dir / "exports"),
        debug_storage_dir=_path("DEBUG_STORAGE_DIR", env_file_values, data_dir / "debug"),
        storage_retention_tmp_hours=_int("STORAGE_RETENTION_TMP_HOURS", env_file_values, 24),
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


def _path(name: str, env_file_values: dict[str, str | None], default: Path) -> Path:
    path = Path(_get(name, env_file_values) or default).expanduser()
    return _absolute_path(path)


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _absolute_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        return PROJECT_ROOT / path
    return path


def _storage_path(path: Path, default_value: Path, resolved_default: Path) -> Path:
    path = path.expanduser()
    if path == default_value:
        return resolved_default
    return _absolute_path(path)
