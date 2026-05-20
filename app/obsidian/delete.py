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


def delete_receipt(
    vault: Path,
    note_name: str,
    *,
    owner_user_id: int | None = None,
    allow_all_users: bool = False,
    user_vault_root: str = "Users",
) -> DeleteResult:
    vault = vault.expanduser().resolve()
    user_root = _user_root(user_vault_root)
    note_path = _find_note(
        vault,
        note_name,
        owner_user_id=owner_user_id,
        allow_all_users=allow_all_users,
        user_root=user_root,
    )
    manifest_path = _find_manifest(vault, note_path, user_root=user_root)
    if manifest_path:
        rel_paths = _paths_from_manifest(vault, manifest_path, note_path)
    else:
        rel_paths = _paths_from_markdown(note_path.read_text(encoding="utf-8"), user_root=user_root)
        rel_paths.append(note_path.relative_to(vault).as_posix())

    targets: list[Path] = []
    for rel_path in _dedupe(rel_paths):
        target = _safe_file(vault, rel_path)
        _ensure_file_owner_scope(
            vault,
            target,
            owner_user_id=owner_user_id,
            allow_all_users=allow_all_users,
            user_root=user_root,
        )
        targets.append(target)

    deleted: list[Path] = []
    missing: list[Path] = []
    for target in targets:
        if target.exists():
            target.unlink()
            deleted.append(target)
        else:
            missing.append(target)
    if manifest_path and manifest_path.exists():
        manifest_path.unlink()
        deleted.append(manifest_path)
    return DeleteResult(note_path=note_path, deleted=deleted, missing=missing, manifest_path=manifest_path)


def _find_note(
    vault: Path,
    note_name: str,
    *,
    owner_user_id: int | None,
    allow_all_users: bool,
    user_root: Path,
) -> Path:
    cleaned = note_name.strip().strip('"').strip("'")
    if not cleaned:
        raise ReceiptDeleteError("Receipt note name is empty.")
    if not cleaned.endswith(".md"):
        cleaned = f"{cleaned}.md"
    candidate = safe_vault_path(vault, cleaned)
    if candidate.exists():
        if candidate.is_file() and candidate.suffix == ".md":
            _ensure_owner_scope(
                vault,
                candidate,
                owner_user_id=owner_user_id,
                allow_all_users=allow_all_users,
                user_root=user_root,
            )
            return candidate
        raise ReceiptDeleteError("Receipt note path is not a Markdown file.")
    matches: list[Path] = []
    for root in _receipt_search_roots(
        vault,
        owner_user_id=owner_user_id,
        allow_all_users=allow_all_users,
        user_root=user_root,
    ):
        if root.exists():
            matches.extend(path for path in root.glob(f"**/{Path(cleaned).name}") if path.is_file())
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        raise ReceiptDeleteError("Several notes have this name. Use full note path.")
    raise ReceiptDeleteError("Receipt note was not found.")


def _find_manifest(vault: Path, note_path: Path, *, user_root: Path) -> Path | None:
    note_rel = note_path.relative_to(vault).as_posix()
    parts = note_path.relative_to(vault).parts
    user_root_parts = user_root.parts
    user_root_len = len(user_root_parts)
    if len(parts) >= 4 and parts[0] == "Receipts":
        expected = vault / "MANIFEST" / "receipts" / parts[1] / parts[2] / f"{note_path.stem}.manifest.json"
        if expected.exists():
            return expected
    if len(parts) >= user_root_len + 5 and parts[:user_root_len] == user_root_parts and parts[user_root_len + 1] == "Receipts":
        expected = (
            vault
            / user_root
            / parts[user_root_len]
            / "MANIFEST"
            / "receipts"
            / parts[user_root_len + 2]
            / parts[user_root_len + 3]
            / f"{note_path.stem}.manifest.json"
        )
        if expected.exists():
            return expected
    for manifest_path in (vault / "MANIFEST" / "receipts").glob("**/*.manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if manifest.get("note") == note_rel:
            return manifest_path
    for manifest_path in (vault / user_root).glob("*/MANIFEST/receipts/**/*.manifest.json"):
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


def _paths_from_markdown(text: str, *, user_root: Path) -> list[str]:
    matches = re.findall(r"!\[\[([^]\n]+)\]\]|\[\[([^]\n]+)\]\]", text)
    flattened = [first or second for first, second in matches]
    user_root_pattern = re.escape(user_root.as_posix())
    return [
        path
        for path in flattened
        if path.startswith(("Attachments/receipts/", "OCR/", "OCR_VERIFIED/", "DEBUG/openai/"))
        or re.match(rf"^{user_root_pattern}/\d+/(Attachments/receipts|OCR|OCR_VERIFIED|DEBUG/openai)/", path)
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


def _receipt_search_roots(
    vault: Path,
    *,
    owner_user_id: int | None,
    allow_all_users: bool,
    user_root: Path,
) -> list[Path]:
    if owner_user_id is not None and not allow_all_users:
        return [vault / user_root / str(owner_user_id) / "Receipts"]
    roots = [vault / "Receipts"]
    roots.extend(path / "Receipts" for path in (vault / user_root).glob("*") if path.is_dir())
    return roots


def _ensure_owner_scope(
    vault: Path,
    note_path: Path,
    *,
    owner_user_id: int | None,
    allow_all_users: bool,
    user_root: Path,
) -> None:
    if owner_user_id is None or allow_all_users:
        return
    owner_root = (vault / user_root / str(owner_user_id) / "Receipts").resolve()
    if not note_path.resolve().is_relative_to(owner_root):
        raise ReceiptDeleteError("Receipt does not belong to this user.")


def _ensure_file_owner_scope(
    vault: Path,
    target: Path,
    *,
    owner_user_id: int | None,
    allow_all_users: bool,
    user_root: Path,
) -> None:
    if owner_user_id is None or allow_all_users:
        return
    owner_root = (vault / user_root / str(owner_user_id)).resolve()
    if not target.resolve().is_relative_to(owner_root):
        raise ReceiptDeleteError("Manifest contains a file outside this user's vault root.")


def _user_root(user_vault_root: str) -> Path:
    root = Path(user_vault_root.strip("/"))
    if root.is_absolute() or ".." in root.parts:
        raise ReceiptDeleteError("USER_VAULT_ROOT must be a safe relative path.")
    return root
