from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.receipts.models import ReceiptRecord
from app.storage.paths import ensure_parent, next_available_stem, safe_vault_path
from app.users.paths import user_root_rel


class ReceiptRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.vault = settings.obsidian_vault

    def list_user_receipts(self, user_id: int) -> list[ReceiptRecord]:
        root = self.vault / user_root_rel(self.settings, user_id) / "MANIFEST" / "receipts"
        records = [record for path in root.glob("**/*.manifest.json") if (record := self._read_record(path))]
        return sorted(records, key=lambda record: (record.date, record.created_at, record.receipt_id), reverse=True)

    def find_user_receipt(self, user_id: int, query: str) -> ReceiptRecord | None:
        cleaned = query.strip().strip('"').strip("'")
        if not cleaned:
            return None
        cleaned_stem = Path(cleaned).stem
        for record in self.list_user_receipts(user_id):
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

    def copy_receipt_to_user(self, query: str, target_user_id: int) -> ReceiptRecord:
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
        return export_path

    def _list_all_receipts(self) -> list[ReceiptRecord]:
        paths = list((self.vault / "MANIFEST" / "receipts").glob("**/*.manifest.json"))
        paths.extend((self.vault / "Users").glob("*/MANIFEST/receipts/**/*.manifest.json"))
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
        note = manifest.get("note")
        files = manifest.get("files", [])
        if not isinstance(note, str) or not isinstance(files, list):
            return None
        try:
            manifest_rel = manifest_path.relative_to(self.vault)
        except ValueError:
            return None
        receipt_id = str(manifest.get("receipt_id") or Path(note).stem)
        owner_user_id = int(manifest.get("owner_user_id") or 0)
        return ReceiptRecord(
            receipt_id=receipt_id,
            owner_user_id=owner_user_id,
            note_rel=Path(note),
            manifest_rel=manifest_rel,
            date=str(manifest.get("date", "")),
            merchant=str(manifest.get("merchant", "")),
            amount=str(manifest.get("amount", "")),
            currency=str(manifest.get("currency", "AMD")),
            created_at=str(manifest.get("created_at", "")),
            files=tuple(Path(str(item)) for item in files),
        )


class ReceiptNotFoundError(RuntimeError):
    pass


class ReceiptCopyError(RuntimeError):
    pass


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
