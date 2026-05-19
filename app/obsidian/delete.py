from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.storage.paths import safe_vault_path


class ReceiptDeleteError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeleteResult:
    note_path: Path
    deleted: list[Path]
    missing: list[Path]
    manifest_path: Path | None


def delete_receipt(vault: Path, note_name: str) -> DeleteResult:
    vault = vault.expanduser().resolve()
    note_path = _find_note(vault, note_name)
    manifest_path = _find_manifest(vault, note_path)
    if manifest_path:
        rel_paths = _paths_from_manifest(vault, manifest_path, note_path)
    else:
        rel_paths = _paths_from_markdown(note_path.read_text(encoding="utf-8"))
        rel_paths.append(note_path.relative_to(vault).as_posix())

    deleted: list[Path] = []
    missing: list[Path] = []
    for rel_path in _dedupe(rel_paths):
        target = _safe_file(vault, rel_path)
        if target.exists():
            target.unlink()
            deleted.append(target)
        else:
            missing.append(target)
    if manifest_path and manifest_path.exists():
        manifest_path.unlink()
        deleted.append(manifest_path)
    return DeleteResult(note_path=note_path, deleted=deleted, missing=missing, manifest_path=manifest_path)


def _find_note(vault: Path, note_name: str) -> Path:
    cleaned = note_name.strip().strip('"').strip("'")
    if not cleaned:
        raise ReceiptDeleteError("Receipt note name is empty.")
    if not cleaned.endswith(".md"):
        cleaned = f"{cleaned}.md"
    candidate = safe_vault_path(vault, cleaned)
    if candidate.exists():
        if candidate.is_file() and candidate.suffix == ".md":
            return candidate
        raise ReceiptDeleteError("Receipt note path is not a Markdown file.")
    matches = [path for path in (vault / "Receipts").glob(f"**/{Path(cleaned).name}") if path.is_file()]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        raise ReceiptDeleteError("Several notes have this name. Use Receipts/YYYY/MM/name.md.")
    raise ReceiptDeleteError("Receipt note was not found.")


def _find_manifest(vault: Path, note_path: Path) -> Path | None:
    note_rel = note_path.relative_to(vault).as_posix()
    parts = note_path.relative_to(vault).parts
    if len(parts) >= 4 and parts[0] == "Receipts":
        expected = vault / "MANIFEST" / "receipts" / parts[1] / parts[2] / f"{note_path.stem}.manifest.json"
        if expected.exists():
            return expected
    for manifest_path in (vault / "MANIFEST" / "receipts").glob("**/*.manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if manifest.get("note") == note_rel:
            return manifest_path
    return None


def _paths_from_manifest(vault: Path, manifest_path: Path, note_path: Path) -> list[str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReceiptDeleteError("Manifest JSON is invalid.") from exc
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ReceiptDeleteError("Manifest has no files list.")
    rel_paths = [str(item) for item in files]
    note_rel = note_path.relative_to(vault).as_posix()
    if note_rel not in rel_paths:
        rel_paths.append(note_rel)
    return rel_paths


def _paths_from_markdown(text: str) -> list[str]:
    matches = re.findall(r"!\[\[([^]\n]+)\]\]|\[\[([^]\n]+)\]\]", text)
    flattened = [first or second for first, second in matches]
    return [
        path
        for path in flattened
        if path.startswith(
            (
                "Attachments/receipts/",
                "Attachments/receipts_preprocessed/",
                "OCR/",
                "OCR_VERIFIED/",
                "DEBUG/openai/",
                "DEBUG/preprocessing/",
            )
        )
    ]


def _safe_file(vault: Path, rel_path: str) -> Path:
    try:
        target = safe_vault_path(vault, rel_path)
    except ValueError as exc:
        raise ReceiptDeleteError("Refusing to use path outside Obsidian vault.") from exc
    if target.exists() and not target.is_file():
        raise ReceiptDeleteError(f"Refusing to delete non-file path: {rel_path}")
    return target


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
