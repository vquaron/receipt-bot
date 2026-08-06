from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings
from app.db.connection import connect_database, database_path_from_url
from app.db import migrations
from app.db.migrations import Migration, initialize_database


def test_initialize_database_creates_schema_and_pragmas(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path, busy_timeout_ms=4321)

    initialize_database(app_settings)

    db_path = tmp_path / "data" / "app.db"
    assert db_path.exists()
    with connect_database(app_settings) as connection:
        assert connection.execute("pragma foreign_keys").fetchone()[0] == 1
        assert connection.execute("pragma busy_timeout").fetchone()[0] == 4321
        assert connection.execute("pragma journal_mode").fetchone()[0] == "wal"
        tables = _table_names(connection)
    assert {
        "users",
        "access_requests",
        "documents",
        "document_items",
        "document_files",
        "processing_sessions",
        "usage_events",
        "correction_rules",
        "magic_links",
        "web_sessions",
        "schema_migrations",
    }.issubset(tables)


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)

    initialize_database(app_settings)
    initialize_database(app_settings)

    with connect_database(app_settings) as connection:
        rows = connection.execute("select version, name from schema_migrations").fetchall()
    assert [(row["version"], row["name"]) for row in rows] == [
        (1, "initial_storage_schema"),
        (2, "access_requests_unique_pending_user"),
        (3, "document_file_storage_refs"),
        (4, "owner_scoped_correction_rules"),
    ]


def test_pending_access_request_is_unique_per_user(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    initialize_database(app_settings)

    with connect_database(app_settings) as connection:
        connection.execute(
            """
            insert into access_requests(id, telegram_user_id, status, created_at)
            values (?, ?, ?, ?)
            """,
            ("one", 777, "pending", "now"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into access_requests(id, telegram_user_id, status, created_at)
                values (?, ?, ?, ?)
                """,
                ("two", 777, "pending", "now"),
            )
        connection.execute(
            """
            insert into access_requests(id, telegram_user_id, status, created_at)
            values (?, ?, ?, ?)
            """,
            ("three", 777, "rejected", "now"),
        )


def test_failed_migration_rolls_back_partial_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_settings = _settings(tmp_path)
    bad_migration = Migration(
        99,
        "bad_migration",
        """
        create table should_be_rolled_back(id integer primary key);
        insert into missing_table values (1);
        """,
    )
    monkeypatch.setattr(migrations, "MIGRATIONS", (bad_migration,))

    with pytest.raises(sqlite3.Error):
        initialize_database(app_settings)

    with connect_database(app_settings) as connection:
        tables = _table_names(connection)
        rows = connection.execute("select version from schema_migrations").fetchall()
    assert "should_be_rolled_back" not in tables
    assert [row["version"] for row in rows] == []


def test_document_file_stem_is_unique_per_user_only(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    initialize_database(app_settings)

    with connect_database(app_settings) as connection:
        _insert_document(connection, "doc-1", owner=100, file_stem="same_stem")
        _insert_document(connection, "doc-2", owner=200, file_stem="same_stem")
        _insert_document(connection, "doc-3", owner=100, file_stem=None)
        _insert_document(connection, "doc-4", owner=100, file_stem=None)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_document(connection, "doc-5", owner=100, file_stem="same_stem")


def test_correction_rules_unique_scope_uses_empty_strings(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    initialize_database(app_settings)

    with connect_database(app_settings) as connection:
        connection.execute(
            """
            insert into correction_rules(scope, source, target, language, document_type, merchant, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("unit", "WT", "шт", "", "", "", "now", "now"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                insert into correction_rules(scope, source, target, language, document_type, merchant, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("unit", "WT", "шт", "", "", "", "now", "now"),
            )

        connection.execute(
            """
            insert into correction_rules(owner_telegram_user_id, scope, source, target, language, document_type, merchant, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (222, "unit", "WT", "шт", "", "", "", "now", "now"),
        )


def test_correction_rules_owner_scope_migration_preserves_ownerless_rows(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    app_settings.data_dir.mkdir(parents=True)
    db_path = app_settings.data_dir / "app.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            create table schema_migrations (
                version integer primary key,
                name text not null,
                applied_at text not null
            );
            insert into schema_migrations(version, name, applied_at)
            values (1, 'initial_storage_schema', 'now'),
                   (2, 'access_requests_unique_pending_user', 'now'),
                   (3, 'document_file_storage_refs', 'now');
            create table correction_rules (
                id integer primary key,
                scope text not null,
                source text not null,
                target text not null,
                language text not null default '',
                document_type text not null default '',
                merchant text not null default '',
                usage_count integer not null default 0,
                last_used_at text,
                created_at text not null,
                updated_at text not null,
                created_by_telegram_user_id integer
            );
            create unique index idx_correction_rules_unique
            on correction_rules(scope, source, language, document_type, merchant);
            create index idx_correction_rules_lookup
            on correction_rules(scope, source, language, document_type, merchant);
            insert into correction_rules(scope, source, target, language, document_type, merchant, created_at, updated_at)
            values ('merchant', 'Զովք', 'Legacy', '', '', '', 'now', 'now');
            """
        )

    with connect_database(app_settings) as connection:
        migrations.apply_migrations(connection)
        row = connection.execute(
            """
            select owner_telegram_user_id, source, target
            from correction_rules
            where scope = 'merchant'
            """
        ).fetchone()
        assert int(row["owner_telegram_user_id"]) == 0
        assert row["target"] == "Legacy"
        connection.execute(
            """
            insert into correction_rules(owner_telegram_user_id, scope, source, target, language, document_type, merchant, created_at, updated_at)
            values (?, ?, ?, ?, '', '', '', ?, ?)
            """,
            (222, "merchant", "Զովք", "User scoped", "now", "now"),
        )


def test_database_url_paths_are_project_relative() -> None:
    assert database_path_from_url("sqlite:///data/app.db") == str(PROJECT_ROOT / "data" / "app.db")
    assert database_path_from_url("sqlite:///:memory:") == ":memory:"
    with pytest.raises(ValueError):
        database_path_from_url("postgresql:///data/app.db")


def test_direct_settings_constructor_uses_data_dir_for_default_database_url(tmp_path: Path) -> None:
    app_settings = Settings(
        telegram_bot_token="telegram",
        openai_api_key="openai",
        obsidian_vault=tmp_path,
        data_dir=tmp_path / "custom_data",
        admin_telegram_user_ids=frozenset(),
        allowed_telegram_user_ids=frozenset(),
    )

    assert app_settings.database_url == f"sqlite:///{(tmp_path / 'custom_data' / 'app.db').as_posix()}"
    assert app_settings.app_storage_dir == tmp_path / "custom_data" / "storage"
    assert app_settings.tmp_storage_dir == tmp_path / "custom_data" / "tmp"


def _settings(tmp_path: Path, *, busy_timeout_ms: int = 5000) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        telegram_bot_token="telegram",
        openai_api_key="openai",
        obsidian_vault=tmp_path,
        data_dir=data_dir,
        admin_telegram_user_ids=frozenset(),
        allowed_telegram_user_ids=frozenset(),
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        db_busy_timeout_ms=busy_timeout_ms,
        app_storage_dir=data_dir / "storage",
        tmp_storage_dir=data_dir / "tmp",
        export_storage_dir=data_dir / "exports",
        debug_storage_dir=data_dir / "debug",
    )


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _insert_document(
    connection: sqlite3.Connection,
    document_id: str,
    *,
    owner: int,
    file_stem: str | None,
) -> None:
    connection.execute(
        """
        insert into documents(
            id,
            owner_telegram_user_id,
            status,
            file_stem,
            created_at,
            updated_at
        )
        values (?, ?, ?, ?, ?, ?)
        """,
        (document_id, owner, "processing", file_stem, "now", "now"),
    )
