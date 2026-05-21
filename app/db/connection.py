from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import PROJECT_ROOT, Settings


SQLITE_MEMORY_URL = "sqlite:///:memory:"
SQLITE_URL_PREFIX = "sqlite:///"


def connect_database(settings: Settings) -> sqlite3.Connection:
    database_path = database_path_from_url(settings.database_url)
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    configure_connection(connection, settings.db_busy_timeout_ms)
    return connection


def configure_connection(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
    timeout = max(0, int(busy_timeout_ms))
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {timeout}")
    connection.execute("PRAGMA journal_mode = WAL")


def database_path_from_url(database_url: str) -> str:
    if database_url == SQLITE_MEMORY_URL:
        return ":memory:"
    if not database_url.startswith(SQLITE_URL_PREFIX):
        raise ValueError("Only sqlite:/// DATABASE_URL values are supported.")
    raw_path = database_url[len(SQLITE_URL_PREFIX) :]
    if not raw_path:
        raise ValueError("DATABASE_URL must include a SQLite database path.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)
