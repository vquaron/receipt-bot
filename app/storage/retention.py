from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.config import PROJECT_ROOT, Settings


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


class RetentionSafetyError(RuntimeError):
    pass


def cleanup_runtime_storage(settings: Settings, *, now: datetime | None = None) -> RetentionCleanupResult:
    current = now or datetime.now()
    result = RetentionCleanupResult()
    result += cleanup_matching_files(
        settings,
        settings.export_storage_dir,
        current - timedelta(days=max(1, settings.storage_retention_export_days)),
        ("*", "receipts_*.zip"),
    )
    result += cleanup_matching_files(
        settings,
        settings.debug_storage_dir,
        current - timedelta(days=max(1, settings.storage_retention_debug_days)),
        ("openai", "*", "*", "*", "*.openai.raw.txt"),
    )
    tmp_cutoff = current - timedelta(hours=max(1, settings.storage_retention_tmp_hours))
    for name in ("materialized", "exports", "telegram"):
        result += cleanup_old_tree(settings, settings.tmp_storage_dir / name, tmp_cutoff)
    return result


def cleanup_matching_files(
    settings: Settings,
    root: Path,
    cutoff: datetime,
    pattern_parts: tuple[str, ...],
) -> RetentionCleanupResult:
    _validate_cleanup_root(settings, root)
    if not root.exists():
        return RetentionCleanupResult()
    resolved_root = _resolved_directory(root)
    result = RetentionCleanupResult()
    for path in root.glob(str(Path(*pattern_parts))):
        result += _cleanup_path(path, resolved_root, cutoff, recurse=False)
        _remove_empty_parents(path.parent, resolved_root)
    return result


def cleanup_old_tree(settings: Settings, root: Path, cutoff: datetime) -> RetentionCleanupResult:
    _validate_cleanup_root(settings, root)
    if not root.exists():
        return RetentionCleanupResult()
    resolved_root = _resolved_directory(root)
    return _cleanup_children(root, resolved_root, cutoff)


def _resolved_directory(root: Path) -> Path:
    try:
        resolved_root = root.resolve()
    except OSError:
        LOGGER.warning("Failed to resolve retention root: %s", root, exc_info=True)
        raise RetentionSafetyError(f"Failed to resolve retention root: {root}") from None
    if root.is_symlink() or not root.is_dir():
        LOGGER.warning("Refusing to cleanup non-directory retention root: %s", root)
        raise RetentionSafetyError(f"Refusing to cleanup non-directory retention root: {root}")
    return resolved_root


def _validate_cleanup_root(settings: Settings, root: Path) -> None:
    try:
        candidate = root.expanduser().resolve(strict=False)
    except OSError as exc:
        raise RetentionSafetyError(f"Failed to resolve retention root: {root}") from exc
    protected_equal = {
        settings.data_dir,
        settings.app_storage_dir,
        settings.obsidian_vault,
        settings.tmp_storage_dir,
        _sqlite_db_directory(settings),
        PROJECT_ROOT,
    }
    for protected in protected_equal:
        protected_resolved = protected.expanduser().resolve(strict=False)
        if candidate == protected_resolved:
            raise RetentionSafetyError(f"Refusing to cleanup protected storage root: {root}")
        if protected_resolved.is_relative_to(candidate):
            raise RetentionSafetyError(f"Refusing to cleanup ancestor of protected storage root: {root}")

    for canonical_root in (settings.app_storage_dir, settings.obsidian_vault):
        canonical_resolved = canonical_root.expanduser().resolve(strict=False)
        if candidate.is_relative_to(canonical_resolved):
            raise RetentionSafetyError(f"Refusing to cleanup inside canonical storage root: {root}")


def _cleanup_children(path: Path, root: Path, cutoff: datetime) -> RetentionCleanupResult:
    result = RetentionCleanupResult()
    try:
        children = list(path.iterdir())
    except OSError:
        LOGGER.warning("Failed to list retention path: %s", path, exc_info=True)
        return RetentionCleanupResult(skipped=1)

    for child in children:
        result += _cleanup_path(child, root, cutoff, recurse=True)
    return result


def _cleanup_path(path: Path, root: Path, cutoff: datetime, *, recurse: bool) -> RetentionCleanupResult:
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
    if not recurse:
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


def _remove_empty_parents(path: Path, root: Path) -> None:
    try:
        current = path.resolve(strict=False)
    except OSError:
        return
    while current != root and current.is_relative_to(root):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _sqlite_db_directory(settings: Settings) -> Path:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        return settings.data_dir
    return Path(settings.database_url[len(prefix) :]).expanduser().parent
