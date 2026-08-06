from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.obsidian.purge import purge_legacy_manifest_receipts


def test_purge_legacy_manifest_receipts_dry_run_does_not_delete(tmp_path: Path) -> None:
    note, image, manifest = _legacy_files(tmp_path, user_id=222)

    result = purge_legacy_manifest_receipts(tmp_path, apply=False)

    assert not result.applied
    assert result.manifests_seen == 1
    assert len(result.planned_paths) == 3
    assert note.exists()
    assert image.exists()
    assert manifest.exists()


def test_purge_legacy_manifest_receipts_apply_deletes_declared_files_only(tmp_path: Path) -> None:
    note, image, manifest = _legacy_files(tmp_path, user_id=222)
    orphan = tmp_path / "Users/222/Receipts/2026/05/orphan.md"
    orphan.write_text("keep", encoding="utf-8")

    result = purge_legacy_manifest_receipts(tmp_path, apply=True)

    assert result.applied
    assert result.files_deleted == 2
    assert result.manifests_deleted == 1
    assert not note.exists()
    assert not image.exists()
    assert not manifest.exists()
    assert orphan.exists()


def test_purge_legacy_manifest_receipts_rejects_unsafe_manifest_paths(tmp_path: Path) -> None:
    note, image, manifest = _legacy_files(tmp_path, user_id=222)
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "receipt_id": "legacy",
                "note": "Users/222/Receipts/2026/05/legacy.md",
                "files": ["Users/222/Receipts/2026/05/legacy.md", "../escape.txt"],
            }
        ),
        encoding="utf-8",
    )

    result = purge_legacy_manifest_receipts(tmp_path, apply=True)

    assert result.skipped_manifests == 1
    assert result.files_deleted == 0
    assert note.exists()
    assert image.exists()
    assert manifest.exists()


def test_purge_legacy_manifest_receipts_skips_symlinked_manifest_escape(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    manifest_dir = vault / "Users/222/MANIFEST/receipts/2026/05"
    manifest_dir.mkdir(parents=True)
    outside.mkdir()
    outside_manifest = outside / "outside.manifest.json"
    outside_manifest.write_text(json.dumps({"version": 1, "files": []}), encoding="utf-8")
    symlink_manifest = manifest_dir / "legacy.manifest.json"
    try:
        symlink_manifest.symlink_to(outside_manifest)
    except OSError:
        pytest.skip("symlinks are not available in this environment")

    result = purge_legacy_manifest_receipts(vault, apply=True)

    assert result.manifests_seen == 1
    assert result.skipped_manifests == 1
    assert result.manifests_deleted == 0
    assert result.planned_paths == ()
    assert outside_manifest.exists()
    assert symlink_manifest.is_symlink()
    assert "symlink" in result.errors[0]


def _legacy_files(vault: Path, *, user_id: int) -> tuple[Path, Path, Path]:
    note = vault / f"Users/{user_id}/Receipts/2026/05/legacy.md"
    image = vault / f"Users/{user_id}/Attachments/receipts/2026/05/legacy.jpg"
    manifest = vault / f"Users/{user_id}/MANIFEST/receipts/2026/05/legacy.manifest.json"
    for path in (note, image, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("note", encoding="utf-8")
    image.write_text("image", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "receipt_id": "legacy",
                "note": f"Users/{user_id}/Receipts/2026/05/legacy.md",
                "files": [
                    f"Users/{user_id}/Receipts/2026/05/legacy.md",
                    f"Users/{user_id}/Attachments/receipts/2026/05/legacy.jpg",
                ],
            }
        ),
        encoding="utf-8",
    )
    return note, image, manifest
