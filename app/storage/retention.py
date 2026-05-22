from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.config import Settings


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionCleanupResult:
    deleted_files: int = 0
    deleted_dirs: int = 0
    skipped: int = 0

    def __add__(self, other: RetentionCleanupResult) -> RetentionCleanupResult:
        return RetentionCleanupResult(
            deleted_files=self.deleted_files + other.deleted_files,
            deleted_dirs=self.deleted_dirs + other.deleted_dirs,
            skipped=self.skipped + other.skipped,
        )


def cleanup_runtime_storage(settings: Settings, *, now: datetime | None = None) -> RetentionCleanupResult:
    current = now or datetime.now()
    result = RetentionCleanupResult()
    result += cleanup_old_tree(
        settings.export_storage_dir,
        current - timedelta(days=max(1, settings.storage_retention_export_days)),
    )
    result += cleanup_old_tree(
        settings.debug_storage_dir,
        current - timedelta(days=max(1, settings.storage_retention_debug_days)),
    )
    tmp_cutoff = current - timedelta(hours=max(1, settings.storage_retention_tmp_hours))
    for name in ("materialized", "exports", "telegram"):
        result += cleanup_old_tree(settings.tmp_storage_dir / name, tmp_cutoff)
    return result


def cleanup_old_tree(root: Path, cutoff: datetime) -> RetentionCleanupResult:
    if not root.exists():
        return RetentionCleanupResult()
    try:
        resolved_root = root.resolve()
    except OSError:
        LOGGER.warning("Failed to resolve retention root: %s", root, exc_info=True)
        return RetentionCleanupResult(skipped=1)
    if root.is_symlink() or not root.is_dir():
        LOGGER.warning("Refusing to cleanup non-directory retention root: %s", root)
        return RetentionCleanupResult(skipped=1)
    return _cleanup_children(root, resolved_root, cutoff)


def _cleanup_children(path: Path, root: Path, cutoff: datetime) -> RetentionCleanupResult:
    result = RetentionCleanupResult()
    try:
        children = list(path.iterdir())
    except OSError:
        LOGGER.warning("Failed to list retention path: %s", path, exc_info=True)
        return RetentionCleanupResult(skipped=1)

    for child in children:
        result += _cleanup_path(child, root, cutoff)
    return result


def _cleanup_path(path: Path, root: Path, cutoff: datetime) -> RetentionCleanupResult:
    try:
        parent = path.parent.resolve(strict=False)
    except OSError:
        return RetentionCleanupResult(skipped=1)
    if not parent.is_relative_to(root):
        LOGGER.warning("Refusing to cleanup path outside retention root: %s", path)
        return RetentionCleanupResult(skipped=1)

    try:
        stat = path.lstat()
    except OSError:
        return RetentionCleanupResult(skipped=1)
    modified_at = datetime.fromtimestamp(stat.st_mtime)

    if path.is_symlink() or path.is_file():
        if modified_at >= cutoff:
            return RetentionCleanupResult()
        try:
            path.unlink()
        except OSError:
            LOGGER.warning("Failed to cleanup old file: %s", path, exc_info=True)
            return RetentionCleanupResult(skipped=1)
        return RetentionCleanupResult(deleted_files=1)

    if not path.is_dir():
        return RetentionCleanupResult(skipped=1)

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return RetentionCleanupResult(skipped=1)
    if not resolved.is_relative_to(root):
        LOGGER.warning("Refusing to cleanup directory outside retention root: %s", path)
        return RetentionCleanupResult(skipped=1)

    result = _cleanup_children(path, root, cutoff)
    try:
        path.rmdir()
    except OSError:
        return result
    return result + RetentionCleanupResult(deleted_dirs=1)
