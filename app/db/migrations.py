from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app.config import Settings
from app.db.connection import connect_database
from app.db.schema import INITIAL_SCHEMA_SQL


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial_storage_schema", INITIAL_SCHEMA_SQL),
    Migration(
        2,
        "access_requests_unique_pending_user",
        """
        create unique index if not exists idx_access_requests_unique_pending_user
        on access_requests(telegram_user_id)
        where status = 'pending';
        """,
    ),
)


def initialize_database(settings: Settings) -> None:
    with connect_database(settings) as connection:
        apply_migrations(connection)


def apply_migrations(connection: sqlite3.Connection) -> None:
    _ensure_migrations_table(connection)
    applied = _applied_versions(connection)
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        applied_at = datetime.now().isoformat()
        try:
            connection.executescript(
                """
                begin immediate;
                """
                + migration.sql
            )
            connection.execute(
                """
                insert into schema_migrations(version, name, applied_at)
                values (?, ?, ?)
                """,
                (migration.version, migration.name, applied_at),
            )
            connection.commit()
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise


def _ensure_migrations_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists schema_migrations (
            version integer primary key,
            name text not null,
            applied_at text not null
        )
        """
    )
    connection.commit()


def _applied_versions(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("select version from schema_migrations").fetchall()
    return {int(row[0]) for row in rows}
