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
    Migration(
        3,
        "document_file_storage_refs",
        """
        alter table document_files add column storage_backend text not null default '';
        alter table document_files add column storage_key text;
        alter table document_files add column bucket text;
        alter table document_files add column is_canonical integer not null default 0;
        alter table document_files add column etag text;

        update document_files
        set storage_backend = case
                when kind in ('original_image', 'clean_ocr', 'source_ocr') then 'local'
                when kind in ('obsidian_note', 'obsidian_attachment') then 'obsidian'
                else 'local'
            end,
            storage_key = path,
            is_canonical = case
                when kind in ('original_image', 'clean_ocr', 'source_ocr') then 1
                else 0
            end
        where storage_backend = '';

        create index if not exists idx_document_files_storage
        on document_files(storage_backend, bucket, storage_key);
        """,
    ),
    Migration(
        4,
        "owner_scoped_correction_rules",
        """
        drop index if exists idx_correction_rules_unique;
        drop index if exists idx_correction_rules_lookup;

        create unique index if not exists idx_correction_rules_unique
        on correction_rules(owner_telegram_user_id, scope, source, language, document_type, merchant);

        create index if not exists idx_correction_rules_lookup
        on correction_rules(owner_telegram_user_id, scope, source, language, document_type, merchant);
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
            if migration.version == 4:
                _ensure_correction_rules_owner_column(connection)
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


def _ensure_correction_rules_owner_column(connection: sqlite3.Connection) -> None:
    rows = connection.execute("pragma table_info(correction_rules)").fetchall()
    columns = {str(row[1]) for row in rows}
    if "owner_telegram_user_id" in columns:
        return
    connection.execute(
        "alter table correction_rules add column owner_telegram_user_id integer not null default 0"
    )
    connection.commit()
