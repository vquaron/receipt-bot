from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.repositories.documents import DocumentAmbiguousError, DocumentRepository, DocumentStorageError
from app.receipts.models import ReceiptFileRecord, ReceiptRecord
from app.storage.paths import ensure_parent


class ReceiptRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.documents = DocumentRepository(settings)

    def list_user_receipts(self, user_id: int) -> list[ReceiptRecord]:
        return self.documents.list_user_documents(user_id)

    def find_user_receipt(self, user_id: int, query: str) -> ReceiptRecord | None:
        return self.documents.get_user_document(user_id, query)

    def file_path(self, file_record: ReceiptFileRecord) -> Path:
        return self.documents.file_path(file_record)

    def materialize_file(self, file_record: ReceiptFileRecord, target_dir: Path) -> Path:
        return self.documents.materialize_file(file_record, target_dir)

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
        if record is None:
            raise ReceiptDeleteError("Receipt was not found in the DB archive.")
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
        )

    def copy_receipt_to_user(self, query: str, target_user_id: int) -> ReceiptRecord:
        try:
            source = self.documents.get_any_document(query)
        except DocumentAmbiguousError as exc:
            raise ReceiptCopyError(str(exc)) from exc
        if source is None:
            raise ReceiptNotFoundError("Receipt was not found in the DB archive.")
        try:
            return self.documents.copy_document_to_user(source, target_user_id).record
        except DocumentStorageError as exc:
            raise ReceiptCopyError(str(exc)) from exc

    def export_user_receipts(self, user_id: int) -> Path:
        export_name = f"receipts_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.zip"
        export_path = self.settings.export_storage_dir / str(user_id) / export_name
        materialize_root = self.settings.tmp_storage_dir / "exports" / uuid4().hex
        ensure_parent(export_path)
        try:
            with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for archive_file in self.documents.archive_files_for_user(user_id, materialize_root=materialize_root):
                    archive.write(archive_file.path, archive_file.archive_name)
        finally:
            shutil.rmtree(materialize_root, ignore_errors=True)
        return export_path


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
