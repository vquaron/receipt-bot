from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.obsidian.writer import write_openai_debug_file
from app.review.models import ReceiptSession
from app.storage.retention import cleanup_runtime_storage


def test_runtime_cleanup_removes_old_exports_debug_and_materialized_tmp(tmp_path: Path) -> None:
    now = datetime(2026, 5, 22, 12, 0, 0)
    app_settings = _settings(
        tmp_path,
        storage_retention_tmp_hours=24,
        storage_retention_export_days=30,
        storage_retention_debug_days=14,
    )
    old_export = app_settings.export_storage_dir / "222" / "old.zip"
    recent_export = app_settings.export_storage_dir / "222" / "recent.zip"
    old_debug = app_settings.debug_storage_dir / "openai" / "222" / "old.raw.txt"
    old_materialized = app_settings.tmp_storage_dir / "materialized" / "doc" / "stored.jpg"
    old_tmp_export = app_settings.tmp_storage_dir / "exports" / "job" / "file.jpg"
    old_telegram_tmp = app_settings.tmp_storage_dir / "telegram" / "222" / "receipt" / "stored.jpg"
    old_processing = app_settings.tmp_storage_dir / "processing" / "session" / "original.jpg"

    for path in (old_export, recent_export, old_debug, old_materialized, old_tmp_export, old_telegram_tmp, old_processing):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    _touch(old_export, now - timedelta(days=31))
    _touch(recent_export, now - timedelta(days=1))
    _touch(old_debug, now - timedelta(days=15))
    _touch(old_materialized, now - timedelta(hours=25))
    _touch(old_tmp_export, now - timedelta(hours=25))
    _touch(old_telegram_tmp, now - timedelta(hours=25))
    _touch(old_processing, now - timedelta(hours=25))

    result = cleanup_runtime_storage(app_settings, now=now)

    assert result.deleted_files == 5
    assert not old_export.exists()
    assert recent_export.exists()
    assert not old_debug.exists()
    assert not old_materialized.exists()
    assert not old_tmp_export.exists()
    assert not old_telegram_tmp.exists()
    assert old_processing.exists()


def test_runtime_cleanup_unlinks_old_symlink_without_removing_target(tmp_path: Path) -> None:
    now = datetime(2026, 5, 22, 12, 0, 0)
    app_settings = _settings(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("keep", encoding="utf-8")
    link = app_settings.tmp_storage_dir / "materialized" / "outside-link"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    _touch(link, now - timedelta(hours=25), follow_symlinks=False)

    result = cleanup_runtime_storage(app_settings, now=now)

    assert result.deleted_files == 1
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == "keep"


def test_openai_debug_file_uses_debug_storage_not_obsidian_vault(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    session = ReceiptSession(
        user_id=222,
        image_path=tmp_path / "image.jpg",
        clean_ocr_path=tmp_path / "clean.txt",
        source_ocr_path=tmp_path / "source.txt",
        temporary_base_name="tmp",
        created_at=datetime(2026, 5, 20, 12, 0, 0),
    )

    debug_path = write_openai_debug_file(app_settings, session, "raw response")

    assert debug_path == app_settings.debug_storage_dir / "openai" / "222" / "2026" / "05" / "tmp.openai.raw.txt"
    assert debug_path.read_text(encoding="utf-8") == "raw response"
    assert not (app_settings.obsidian_vault / "Users" / "222" / "DEBUG").exists()


def test_openai_debug_file_sanitizes_temporary_base_name(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    session = ReceiptSession(
        user_id=222,
        image_path=tmp_path / "image.jpg",
        clean_ocr_path=tmp_path / "clean.txt",
        source_ocr_path=tmp_path / "source.txt",
        temporary_base_name="../escape",
        created_at=datetime(2026, 5, 20, 12, 0, 0),
    )

    debug_path = write_openai_debug_file(app_settings, session, "raw response")

    assert debug_path.is_relative_to(app_settings.debug_storage_dir)
    assert debug_path.name == "_escape.openai.raw.txt"
    assert not (tmp_path / "escape.openai.raw.txt").exists()


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "openai_api_key": "key",
        "obsidian_vault": tmp_path / "vault",
        "data_dir": tmp_path / "data",
        "admin_telegram_user_ids": frozenset(),
        "allowed_telegram_user_ids": frozenset(),
    }
    values.update(overrides)
    values["obsidian_vault"].mkdir(parents=True, exist_ok=True)
    return Settings(**values)


def _touch(path: Path, stamp: datetime, *, follow_symlinks: bool = True) -> None:
    timestamp = stamp.timestamp()
    os.utime(path, (timestamp, timestamp), follow_symlinks=follow_symlinks)
