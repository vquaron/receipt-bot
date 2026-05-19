import json
from pathlib import Path

import pytest

from app.obsidian.delete import ReceiptDeleteError, delete_receipt


def test_delete_receipt_prefers_manifest(tmp_path: Path) -> None:
    vault = tmp_path
    note = vault / "Receipts/2026/04/a.md"
    image = vault / "Attachments/receipts/2026/04/a.jpg"
    clean = vault / "OCR/2026/04/a.clean.hy.txt"
    source = vault / "OCR_VERIFIED/2026/04/a.verified.hy.txt"
    manifest = vault / "MANIFEST/receipts/2026/04/a.manifest.json"
    for path in (note, image, clean, source, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    note.write_text("![[Attachments/receipts/2026/04/not-from-manifest.jpg]]", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "Receipts/2026/04/a.md",
                "files": [
                    "Receipts/2026/04/a.md",
                    "Attachments/receipts/2026/04/a.jpg",
                    "OCR/2026/04/a.clean.hy.txt",
                    "OCR_VERIFIED/2026/04/a.verified.hy.txt",
                ],
            }
        ),
        encoding="utf-8",
    )
    result = delete_receipt(vault, "a.md")
    assert len(result.deleted) == 5
    assert not note.exists()
    assert not image.exists()
    assert not clean.exists()
    assert not source.exists()
    assert not manifest.exists()


def test_delete_receipt_rejects_manifest_escape(tmp_path: Path) -> None:
    vault = tmp_path
    note = vault / "Receipts/2026/04/a.md"
    manifest = vault / "MANIFEST/receipts/2026/04/a.manifest.json"
    note.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("x", encoding="utf-8")
    manifest.write_text(
        json.dumps({"version": 1, "note": "Receipts/2026/04/a.md", "files": ["../escape.txt"]}),
        encoding="utf-8",
    )
    with pytest.raises(ReceiptDeleteError):
        delete_receipt(vault, "a.md")


def test_delete_receipt_removes_preprocessed_image_from_manifest(tmp_path: Path) -> None:
    vault = tmp_path
    note = vault / "Receipts/2026/04/a.md"
    image = vault / "Attachments/receipts/2026/04/a.jpg"
    preprocessed = vault / "Attachments/receipts_preprocessed/2026/04/a.preprocessed.jpg"
    manifest = vault / "MANIFEST/receipts/2026/04/a.manifest.json"
    for path in (note, image, preprocessed, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "Receipts/2026/04/a.md",
                "files": [
                    "Receipts/2026/04/a.md",
                    "Attachments/receipts/2026/04/a.jpg",
                    "Attachments/receipts_preprocessed/2026/04/a.preprocessed.jpg",
                ],
            }
        ),
        encoding="utf-8",
    )

    result = delete_receipt(vault, "a.md")

    assert len(result.deleted) == 4
    assert not note.exists()
    assert not image.exists()
    assert not preprocessed.exists()
    assert not manifest.exists()
