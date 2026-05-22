from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.obsidian.delete import ReceiptDeleteError as LegacyReceiptDeleteError
from app.obsidian.delete import delete_receipt as delete_legacy_receipt
from app.repositories.documents import DocumentAmbiguousError, DocumentRepository, DocumentStorageError
from app.receipts.document_types import normalize_document_type
from app.receipts.models import ReceiptFileRecord, ReceiptRecord
from app.storage.paths import ensure_parent, next_available_stem, safe_vault_path
from app.users.paths import user_root_rel


class ReceiptRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.vault = settings.obsidian_vault
        self.documents = DocumentRepository(settings)

    def list_user_receipts(self, user_id: int) -> list[ReceiptRecord]:
        root = self.vault / user_root_rel(self.settings, user_id) / "MANIFEST" / "receipts"
        records = self.documents.list_user_documents(user_id)
        records.extend(record for path in root.glob("**/*.manifest.json") if (record := self._read_record(path)))
        return sorted(records, key=lambda record: (record.date, record.created_at, record.receipt_id), reverse=True)

    def find_user_receipt(self, user_id: int, query: str) -> ReceiptRecord | None:
        cleaned = query.strip().strip('"').strip("'")
        if not cleaned:
            return None
        db_record = self.documents.get_user_document(user_id, cleaned)
        if db_record is not None:
            return db_record
        cleaned_stem = Path(cleaned).stem
        for record in self.list_user_receipts(user_id):
            if record.source == "db":
                continue
            if cleaned in {record.receipt_id, record.note_rel.as_posix(), record.note_rel.name}:
                return record
            if cleaned_stem in {record.receipt_id, record.note_rel.stem}:
                return record
        return None

    def find_any_receipt(self, query: str) -> ReceiptRecord | None:
        cleaned = query.strip().strip('"').strip("'")
        if not cleaned:
            return None
        cleaned_stem = Path(cleaned).stem
        for record in self._list_all_receipts():
            if cleaned in {record.receipt_id, record.note_rel.as_posix(), record.note_rel.name}:
                return record
            if cleaned_stem in {record.receipt_id, record.note_rel.stem}:
                return record
        return None

    def file_path(self, file_record: ReceiptFileRecord) -> Path:
        return self.documents.file_path(file_record)

    def delete_receipt(
        self,
        query: str,
        *,
        owner_user_id: int,
        allow_all_users: bool = False,
    ) -> ReceiptDeleteResult:
        try:
            record = self.documents.get_any_document(query) if allow_all_users else self.documents.get_user_document(owner_user_id, query)
        except DocumentAmbiguousError as exc:
            raise ReceiptDeleteError(str(exc)) from exc
        if record is not None:
            try:
                result = self.documents.delete_document(record)
            except DocumentStorageError as exc:
                raise ReceiptDeleteError(str(exc)) from exc
            return ReceiptDeleteResult(
                receipt_id=result.record.receipt_id,
                document_id=result.record.document_id,
                deleted=result.deleted,
                missing=result.missing,
                note_path=result.record.note_rel,
                source="db",
            )

        try:
            legacy = delete_legacy_receipt(
                self.settings.obsidian_vault,
                query,
                owner_user_id=owner_user_id,
                allow_all_users=allow_all_users,
                user_vault_root=self.settings.user_vault_root,
            )
        except LegacyReceiptDeleteError as exc:
            raise ReceiptDeleteError(str(exc)) from exc
        return ReceiptDeleteResult(
            receipt_id=legacy.note_path.stem,
            document_id="",
            deleted=legacy.deleted,
            missing=legacy.missing,
            note_path=legacy.note_path,
            source="legacy",
        )

    def copy_receipt_to_user(self, query: str, target_user_id: int) -> ReceiptRecord:
        try:
            db_source = self.documents.get_any_document(query)
        except DocumentAmbiguousError as exc:
            raise ReceiptCopyError(str(exc)) from exc
        if db_source is not None:
            try:
                return self.documents.copy_document_to_user(db_source, target_user_id).record
            except DocumentStorageError as exc:
                raise ReceiptCopyError(str(exc)) from exc

        source = self.find_any_receipt(query)
        if source is None:
            raise ReceiptNotFoundError("Receipt was not found.")
        note_date = _date_parts(source.date, source.note_rel)
        stem = next_available_stem(
            self.vault / user_root_rel(self.settings, target_user_id) / "Receipts" / note_date[0] / note_date[1],
            source.receipt_id,
            ".md",
        )
        mapping = self._copy_record_files(source, target_user_id, stem, note_date)
        manifest_rel = user_root_rel(self.settings, target_user_id) / "MANIFEST" / "receipts" / note_date[0] / note_date[1] / f"{stem}.manifest.json"
        manifest_path = safe_vault_path(self.vault, manifest_rel)
        ensure_parent(manifest_path)
        manifest = {
            "version": 1,
            "receipt_id": stem,
            "owner_user_id": target_user_id,
            "copied_from": source.receipt_id,
            "created_at": datetime.now().isoformat(),
            "document_type": source.document_type,
            "date": source.date,
            "merchant": source.merchant,
            "amount": source.amount,
            "currency": source.currency,
            "note": mapping[source.note_rel].as_posix(),
            "files": [target.as_posix() for target in mapping.values()],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        record = self._read_record(manifest_path)
        if record is None:
            raise ReceiptCopyError("Copied receipt manifest is invalid.")
        return record

    def export_user_receipts(self, user_id: int) -> Path:
        root_rel = user_root_rel(self.settings, user_id)
        root = safe_vault_path(self.vault, root_rel)
        export_path = self.settings.data_dir / "exports" / str(user_id) / f"receipts_{datetime.now():%Y%m%d_%H%M%S}.zip"
        ensure_parent(export_path)
        with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if root.exists():
                for path in sorted(root.glob("**/*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root).as_posix())
            for archive_file in self.documents.archive_files_for_user(user_id):
                archive.write(archive_file.path, archive_file.archive_name)
        return export_path

    def _list_all_receipts(self) -> list[ReceiptRecord]:
        paths = list((self.vault / "MANIFEST" / "receipts").glob("**/*.manifest.json"))
        user_root = _safe_user_root(self.settings)
        if user_root is not None:
            paths.extend((self.vault / user_root).glob("*/MANIFEST/receipts/**/*.manifest.json"))
        records = [record for path in paths if (record := self._read_record(path))]
        return sorted(records, key=lambda record: (record.date, record.created_at, record.receipt_id), reverse=True)

    def _copy_record_files(
        self,
        source: ReceiptRecord,
        target_user_id: int,
        target_stem: str,
        note_date: tuple[str, str],
    ) -> dict[Path, Path]:
        mapping: dict[Path, Path] = {}
        for rel_path in sorted(source.files, key=lambda path: path == source.note_rel):
            target_rel = _target_rel_for_copy(self.settings, target_user_id, rel_path, target_stem, note_date)
            if target_rel is None:
                continue
            source_path = safe_vault_path(self.vault, rel_path)
            target_path = safe_vault_path(self.vault, target_rel)
            if not source_path.exists() or not source_path.is_file():
                continue
            ensure_parent(target_path)
            if rel_path == source.note_rel:
                text = source_path.read_text(encoding="utf-8")
                for old_rel, new_rel in mapping.items():
                    text = text.replace(old_rel.as_posix(), new_rel.as_posix())
                target_path.write_text(text, encoding="utf-8")
            else:
                shutil.copy2(source_path, target_path)
            mapping[rel_path] = target_rel
        if source.note_rel not in mapping:
            raise ReceiptCopyError("Source note was not copied.")
        return mapping

    def _read_record(self, manifest_path: Path) -> ReceiptRecord | None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(manifest, dict):
            return None
        note_path = _safe_rel_path(manifest.get("note"))
        files_raw = manifest.get("files", [])
        if note_path is None or not isinstance(files_raw, list):
            return None
        file_paths = [_safe_rel_path(item) for item in files_raw]
        if any(path is None for path in file_paths):
            return None
        files = tuple(path for path in file_paths if path is not None)
        try:
            manifest_rel = manifest_path.relative_to(self.vault)
        except ValueError:
            return None
        expected_owner_root = _owner_root_from_manifest(manifest_rel, _safe_user_root(self.settings))
        if expected_owner_root is not None:
            if not note_path.is_relative_to(expected_owner_root):
                return None
            if any(not file_path.is_relative_to(expected_owner_root) for file_path in files):
                return None
        receipt_id = str(manifest.get("receipt_id") or note_path.stem)
        owner_user_id = _owner_user_id(manifest.get("owner_user_id"))
        return ReceiptRecord(
            receipt_id=receipt_id,
            owner_user_id=owner_user_id,
            note_rel=note_path,
            manifest_rel=manifest_rel,
            date=str(manifest.get("date", "")),
            merchant=str(manifest.get("merchant", "")),
            amount=str(manifest.get("amount", "")),
            currency=str(manifest.get("currency", "AMD")),
            created_at=str(manifest.get("created_at", "")),
            files=files,
            document_type=normalize_document_type(manifest.get("document_type", "receipt")),
            file_records=tuple(ReceiptFileRecord(kind="legacy", path=path, storage="vault") for path in files),
        )


class ReceiptNotFoundError(RuntimeError):
    pass


class ReceiptDeleteError(RuntimeError):
    pass


class ReceiptCopyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiptDeleteResult:
    receipt_id: str
    document_id: str
    deleted: list[Path]
    missing: list[Path]
    note_path: Path
    source: str


def _date_parts(date_value: str, note_rel: Path) -> tuple[str, str]:
    parts = note_rel.parts
    if len(date_value) >= 7 and date_value[4] == "-":
        return date_value[:4], date_value[5:7]
    if len(parts) >= 4 and parts[-3].isdigit() and parts[-2].isdigit():
        return parts[-3], parts[-2]
    return f"{datetime.now():%Y}", f"{datetime.now():%m}"


def _target_rel_for_copy(
    settings: Settings,
    target_user_id: int,
    rel_path: Path,
    stem: str,
    date_parts: tuple[str, str],
) -> Path | None:
    suffix = "".join(rel_path.suffixes)
    year, month = date_parts
    parts = rel_path.parts
    root = user_root_rel(settings, target_user_id)
    if "Receipts" in parts and rel_path.suffix == ".md":
        return root / "Receipts" / year / month / f"{stem}.md"
    if "Attachments" in parts and "receipts" in parts:
        return root / "Attachments" / "receipts" / year / month / f"{stem}{rel_path.suffix or '.jpg'}"
    if "OCR_VERIFIED" in parts:
        return root / "OCR_VERIFIED" / year / month / f"{stem}.verified.hy.txt"
    if "OCR" in parts:
        return root / "OCR" / year / month / f"{stem}.clean.hy.txt"
    if "DEBUG" in parts and "openai" in parts:
        return root / "DEBUG" / "openai" / year / month / f"{stem}.openai.raw.txt"
    if suffix:
        return root / "Attachments" / "receipts" / year / month / f"{stem}{rel_path.suffix}"
    return None


def _safe_user_root(settings: Settings) -> Path | None:
    root = Path(settings.user_vault_root.strip("/"))
    if root.is_absolute() or ".." in root.parts:
        return None
    return root


def _safe_rel_path(value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _owner_root_from_manifest(manifest_rel: Path, user_root: Path | None) -> Path | None:
    if user_root is None:
        return None
    parts = manifest_rel.parts
    root_parts = user_root.parts
    root_len = len(root_parts)
    if len(parts) < root_len + 3:
        return None
    if parts[:root_len] != root_parts:
        return None
    if parts[root_len + 1 : root_len + 3] != ("MANIFEST", "receipts"):
        return None
    return Path(*parts[: root_len + 1])


def _owner_user_id(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
