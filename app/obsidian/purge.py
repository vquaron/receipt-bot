from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.storage.paths import safe_vault_path


@dataclass(frozen=True, slots=True)
class LegacyManifestPurgeResult:
    applied: bool
    manifests_seen: int = 0
    manifests_deleted: int = 0
    files_deleted: int = 0
    files_missing: int = 0
    skipped_manifests: int = 0
    planned_paths: tuple[Path, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


def purge_legacy_manifest_receipts(
    vault: Path,
    *,
    user_vault_root: str = "Users",
    apply: bool = False,
) -> LegacyManifestPurgeResult:
    vault = vault.expanduser().resolve()
    errors: list[str] = []
    planned_paths: list[Path] = []
    manifests_deleted = 0
    files_deleted = 0
    files_missing = 0
    skipped_manifests = 0
    manifest_paths = _manifest_paths(vault, user_vault_root)

    for manifest_path in manifest_paths:
        try:
            manifest_path = _safe_manifest_path(vault, manifest_path)
            file_paths = _declared_files(vault, manifest_path)
        except ValueError as exc:
            skipped_manifests += 1
            errors.append(f"{_display_path(vault, manifest_path)}: {exc}")
            continue

        targets = [*file_paths, manifest_path]
        planned_paths.extend(targets)
        if not apply:
            continue

        directory_target = next((target for target in targets if target.is_dir() and not target.is_symlink()), None)
        if directory_target is not None:
            skipped_manifests += 1
            errors.append(f"{directory_target.relative_to(vault).as_posix()}: refusing to delete a directory")
            continue
        for target in targets:
            if not target.exists() and not target.is_symlink():
                files_missing += 1
                continue
            target.unlink()
            if target == manifest_path:
                manifests_deleted += 1
            else:
                files_deleted += 1

    return LegacyManifestPurgeResult(
        applied=apply,
        manifests_seen=len(manifest_paths),
        manifests_deleted=manifests_deleted,
        files_deleted=files_deleted,
        files_missing=files_missing,
        skipped_manifests=skipped_manifests,
        planned_paths=tuple(_dedupe_paths(planned_paths)),
        errors=tuple(errors),
    )


def _manifest_paths(vault: Path, user_vault_root: str) -> list[Path]:
    paths = list((vault / "MANIFEST" / "receipts").glob("**/*.manifest.json"))
    root = Path(user_vault_root.strip("/"))
    if not root.is_absolute() and ".." not in root.parts:
        paths.extend((vault / root).glob("*/MANIFEST/receipts/**/*.manifest.json"))
    return sorted(set(paths))


def _safe_manifest_path(vault: Path, manifest_path: Path) -> Path:
    if manifest_path.is_symlink():
        raise ValueError("manifest path must not be a symlink")
    resolved = manifest_path.resolve(strict=False)
    if not resolved.is_relative_to(vault):
        raise ValueError("manifest path escapes Obsidian vault")
    return manifest_path


def _declared_files(vault: Path, manifest_path: Path) -> list[Path]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("manifest files must be an array")
    raw_note = manifest.get("note")
    values = [raw_note, *raw_files] if isinstance(raw_note, str) else list(raw_files)
    result: list[Path] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("manifest file paths must be strings")
        rel_path = Path(value)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise ValueError(f"unsafe path {value!r}")
        result.append(safe_vault_path(vault, rel_path))
    return _dedupe_paths(result)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def _display_path(vault: Path, path: Path) -> str:
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return path.as_posix()
