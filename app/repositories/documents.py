from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.obsidian.writer import ReceiptArtifact, export_receipt_note
from app.receipts.document_types import normalize_document_type
from app.receipts.models import ReceiptFileRecord, ReceiptRecord
from app.review.models import ReceiptSession
from app.review.receipt_review import build_review_payload
from app.storage.normalization import (
    amount_for_filename,
    normalize_receipt_properties,
    slugify_merchant,
)
from app.storage.paths import ensure_parent, safe_vault_path
from app.users.paths import user_dated_relpath


DOCUMENT_STATUS_CONFIRMED = "confirmed"
DOCUMENT_STATUS_DELETED = "deleted"
DOCUMENT_STATUS_EXPORT_FAILED = "export_failed"

FILE_KIND_ORIGINAL_IMAGE = "original_image"
FILE_KIND_CLEAN_OCR = "clean_ocr"
FILE_KIND_SOURCE_OCR = "source_ocr"
FILE_KIND_OBSIDIAN_NOTE = "obsidian_note"
FILE_KIND_OBSIDIAN_ATTACHMENT = "obsidian_attachment"

PARSER_VERSION = "openai_parser_v1"
PARSED_SCHEMA_VERSION = "receipt_schema_v1"
PROMPT_VERSION = "prompt_v1"


@dataclass(frozen=True, slots=True)
class DocumentCreateResult:
    record: ReceiptRecord
    artifact: ReceiptArtifact | None


@dataclass(frozen=True, slots=True)
class DocumentDeleteResult:
    record: ReceiptRecord
    deleted: list[Path]
    missing: list[Path]


@dataclass(frozen=True, slots=True)
class DocumentArchiveFile:
    path: Path
    archive_name: str


class DocumentRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        initialize_database(settings)

    def create_confirmed_from_session(
        self,
        session: ReceiptSession,
        parsed: dict[str, object],
    ) -> DocumentCreateResult:
        normalized, note_date = _final_parsed(parsed)
        document_id = uuid4().hex
        document_type = normalize_document_type(session.document_type)
        file_stem = self._next_file_stem(
            user_id=session.user_id,
            note_date=note_date,
            merchant=str(normalized.get("merchant", "")),
            amount=str(normalized.get("amount", "")),
        )
        document_root = self.settings.app_storage_dir / "documents" / document_id
        canonical_targets = (
            (session.image_path, document_root / "original.jpg", FILE_KIND_ORIGINAL_IMAGE),
            (session.clean_ocr_path, document_root / "clean.hy.txt", FILE_KIND_CLEAN_OCR),
            (session.source_ocr_path, document_root / "source.hy.txt", FILE_KIND_SOURCE_OCR),
        )
        moved: list[tuple[Path, Path]] = []
        now = datetime.now()

        try:
            with connect_database(self.settings) as connection:
                connection.execute("begin immediate")
                _insert_document(
                    connection,
                    document_id=document_id,
                    user_id=session.user_id,
                    document_type=document_type,
                    status=DOCUMENT_STATUS_CONFIRMED,
                    file_stem=file_stem,
                    parsed=normalized,
                    created_at=now,
                    reviewed_at=now,
                    ocr_text_hash=_sha256_text_file(session.source_ocr_path),
                )
                _insert_items(connection, document_id=document_id, parsed=normalized, created_at=now)
                for source, target, kind in canonical_targets:
                    ensure_parent(target)
                    shutil.move(str(source), target)
                    moved.append((source, target))
                    _insert_file(
                        connection,
                        document_id=document_id,
                        kind=kind,
                        path=_relative_to(self.settings.app_storage_dir, target),
                        absolute_path=target,
                        created_at=now,
                    )
        except Exception:
            for source, target in reversed(moved):
                if target.exists() and not source.exists():
                    ensure_parent(source)
                    shutil.move(str(target), source)
            raise

        artifact: ReceiptArtifact | None = None
        try:
            artifact = export_receipt_note(
                self.settings,
                user_id=session.user_id,
                file_stem=file_stem,
                document_type=document_type,
                parsed=normalized,
                source_image_path=document_root / "original.jpg",
            )
            self.add_file(document_id, FILE_KIND_OBSIDIAN_NOTE, artifact.note_path, storage_root="vault")
            if artifact.attachment_path is not None:
                self.add_file(document_id, FILE_KIND_OBSIDIAN_ATTACHMENT, artifact.attachment_path, storage_root="vault")
        except Exception:
            self.update_status(document_id, DOCUMENT_STATUS_EXPORT_FAILED)
            artifact = None

        record = self.get_user_document(session.user_id, file_stem)
        if record is None:
            raise DocumentStorageError("Created document could not be read back.")
        return DocumentCreateResult(record=record, artifact=artifact)

    def update_status(self, document_id: str, status: str) -> None:
        with connect_database(self.settings) as connection:
            connection.execute(
                """
                update documents
                set status = ?, updated_at = ?
                where id = ?
                """,
                (status, datetime.now().isoformat(), document_id),
            )

    def mark_deleted(self, document_id: str) -> None:
        now = datetime.now().isoformat()
        with connect_database(self.settings) as connection:
            connection.execute(
                """
                update documents
                set status = ?,
                    deleted_at = ?,
                    updated_at = ?
                where id = ?
                """,
                (DOCUMENT_STATUS_DELETED, now, now, document_id),
            )

    def add_file(self, document_id: str, kind: str, path: Path, *, storage_root: str) -> None:
        root = self.settings.app_storage_dir if storage_root == "app" else self.settings.obsidian_vault
        with connect_database(self.settings) as connection:
            _insert_file(
                connection,
                document_id=document_id,
                kind=kind,
                path=_relative_to(root, path),
                absolute_path=path,
                created_at=datetime.now(),
            )

    def list_user_documents(self, user_id: int) -> list[ReceiptRecord]:
        with connect_database(self.settings) as connection:
            rows = connection.execute(
                """
                select *
                from documents
                where owner_telegram_user_id = ?
                  and deleted_at is null
                order by coalesce(date, '') desc, created_at desc, id desc
                """,
                (user_id,),
            ).fetchall()
            files_by_document = _files_by_document(connection, [str(row["id"]) for row in rows])
        return [_record_from_document_row(row, files_by_document.get(str(row["id"]), ())) for row in rows]

    def get_user_document(self, user_id: int, query: str) -> ReceiptRecord | None:
        cleaned = query.strip().strip('"').strip("'")
        if not cleaned:
            return None
        cleaned_stem = Path(cleaned).stem
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select *
                from documents
                where owner_telegram_user_id = ?
                  and deleted_at is null
                  and (id = ? or file_stem = ? or file_stem = ?)
                order by created_at desc
                limit 1
                """,
                (user_id, cleaned, cleaned, cleaned_stem),
            ).fetchone()
            if row is None:
                return None
            files = _files_by_document(connection, [str(row["id"])]).get(str(row["id"]), ())
        return _record_from_document_row(row, files)

    def get_any_document(self, query: str) -> ReceiptRecord | None:
        cleaned = query.strip().strip('"').strip("'")
        if not cleaned:
            return None
        cleaned_stem = Path(cleaned).stem
        with connect_database(self.settings) as connection:
            exact = connection.execute(
                """
                select *
                from documents
                where id = ?
                  and deleted_at is null
                limit 1
                """,
                (cleaned,),
            ).fetchone()
            if exact is not None:
                files = _files_by_document(connection, [str(exact["id"])]).get(str(exact["id"]), ())
                return _record_from_document_row(exact, files)

            rows = connection.execute(
                """
                select *
                from documents
                where deleted_at is null
                  and (file_stem = ? or file_stem = ?)
                order by created_at desc
                """,
                (cleaned, cleaned_stem),
            ).fetchall()
            if len(rows) > 1:
                raise DocumentAmbiguousError("Several DB documents share this receipt_id. Use the full document id.")
            if not rows:
                return None
            row = rows[0]
            files = _files_by_document(connection, [str(row["id"])]).get(str(row["id"]), ())
        return _record_from_document_row(row, files)

    def delete_document(self, record: ReceiptRecord) -> DocumentDeleteResult:
        if not record.document_id:
            raise DocumentStorageError("Document id is required for DB delete.")
        targets = self._validated_file_targets(record.file_records)
        deleted: list[Path] = []
        missing: list[Path] = []
        for path in targets:
            if path.exists():
                path.unlink()
                deleted.append(path)
            else:
                missing.append(path)
        self.mark_deleted(record.document_id)
        return DocumentDeleteResult(record=record, deleted=deleted, missing=missing)

    def copy_document_to_user(self, source: ReceiptRecord, target_user_id: int) -> DocumentCreateResult:
        if not source.document_id:
            raise DocumentStorageError("Document id is required for DB copy.")
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select *
                from documents
                where id = ? and deleted_at is null
                """,
                (source.document_id,),
            ).fetchone()
            if row is None:
                raise DocumentStorageError("Source document was not found.")
            item_rows = connection.execute(
                """
                select *
                from document_items
                where document_id = ?
                order by position
                """,
                (source.document_id,),
            ).fetchall()
            files = _files_by_document(connection, [source.document_id]).get(source.document_id, ())

        new_document_id = uuid4().hex
        parsed = _json_object(row["parsed_json"])
        note_date, _used_fallback = _resolve_note_date(str(row["date"] or ""))
        file_stem = self._next_available_file_stem(
            target_user_id=target_user_id,
            base_stem=str(row["file_stem"] or source.receipt_id),
            note_date=note_date,
        )
        document_root = self.settings.app_storage_dir / "documents" / new_document_id
        canonical_files = [file for file in files if file.storage == "app"]
        copied: list[tuple[Path, Path]] = []
        now = datetime.now()

        try:
            with connect_database(self.settings) as connection:
                connection.execute("begin immediate")
                _insert_document(
                    connection,
                    document_id=new_document_id,
                    user_id=target_user_id,
                    document_type=str(row["document_type"]),
                    status=DOCUMENT_STATUS_CONFIRMED,
                    file_stem=file_stem,
                    parsed=parsed,
                    created_at=now,
                    reviewed_at=datetime.fromisoformat(str(row["reviewed_at"] or row["created_at"])),
                    ocr_text_hash=str(row["ocr_text_hash"] or ""),
                    parser_version=str(row["parser_version"] or PARSER_VERSION),
                    schema_version=str(row["schema_version"] or PARSED_SCHEMA_VERSION),
                    prompt_version=str(row["prompt_version"] or PROMPT_VERSION),
                )
                _copy_item_rows(
                    connection,
                    document_id=new_document_id,
                    item_rows=item_rows,
                    created_at=now,
                )
                for file in canonical_files:
                    source_path = self.file_path(file)
                    if not source_path.exists() or not source_path.is_file():
                        raise DocumentStorageError(f"Source canonical file is missing: {file.path.as_posix()}")
                    target_path = document_root / file.path.name
                    ensure_parent(target_path)
                    shutil.copy2(source_path, target_path)
                    copied.append((source_path, target_path))
                    _insert_file(
                        connection,
                        document_id=new_document_id,
                        kind=file.kind,
                        path=_relative_to(self.settings.app_storage_dir, target_path),
                        absolute_path=target_path,
                        created_at=now,
                    )
        except Exception:
            for _source, target in reversed(copied):
                if target.exists():
                    target.unlink()
            raise

        artifact: ReceiptArtifact | None = None
        original = self._first_existing_canonical(new_document_id, FILE_KIND_ORIGINAL_IMAGE)
        if original is not None:
            try:
                artifact = export_receipt_note(
                    self.settings,
                    user_id=target_user_id,
                    file_stem=file_stem,
                    document_type=str(row["document_type"]),
                    parsed=parsed,
                    source_image_path=original,
                )
                self.add_file(new_document_id, FILE_KIND_OBSIDIAN_NOTE, artifact.note_path, storage_root="vault")
                if artifact.attachment_path is not None:
                    self.add_file(new_document_id, FILE_KIND_OBSIDIAN_ATTACHMENT, artifact.attachment_path, storage_root="vault")
            except Exception:
                self.update_status(new_document_id, DOCUMENT_STATUS_EXPORT_FAILED)
                artifact = None

        record = self.get_user_document(target_user_id, file_stem)
        if record is None:
            raise DocumentStorageError("Copied document could not be read back.")
        return DocumentCreateResult(record=record, artifact=artifact)

    def archive_files_for_user(self, user_id: int) -> list[DocumentArchiveFile]:
        result: list[DocumentArchiveFile] = []
        for record in self.list_user_documents(user_id):
            for file in record.file_records:
                if file.storage != "app":
                    continue
                path = self.file_path(file)
                if not path.exists():
                    continue
                if not path.is_file():
                    raise DocumentStorageError(f"Refusing to export non-file path: {file.path.as_posix()}")
                result.append(
                    DocumentArchiveFile(
                        path=path,
                        archive_name=f"Canonical/{record.receipt_id}/{file.path.name}",
                    )
                )
        return result

    def file_path(self, file_record: ReceiptFileRecord) -> Path:
        root = self.settings.app_storage_dir if file_record.storage == "app" else self.settings.obsidian_vault
        if file_record.storage == "vault":
            return safe_vault_path(root, file_record.path)
        return _safe_storage_path(root, file_record.path)

    def _validated_file_targets(self, files: tuple[ReceiptFileRecord, ...]) -> list[Path]:
        targets: list[Path] = []
        for file in files:
            try:
                target = self.file_path(file)
            except ValueError as exc:
                raise DocumentStorageError("Refusing to use path outside configured storage roots.") from exc
            if target.exists() and not target.is_file():
                raise DocumentStorageError(f"Refusing to delete non-file path: {file.path.as_posix()}")
            targets.append(target)
        return _dedupe_paths(targets)

    def _first_existing_canonical(self, document_id: str, kind: str) -> Path | None:
        record = self.get_any_document(document_id)
        if record is None:
            return None
        for file in record.file_records:
            if file.kind != kind:
                continue
            path = self.file_path(file)
            if path.exists() and path.is_file():
                return path
        return None

    def _next_file_stem(self, *, user_id: int, note_date: date, merchant: str, amount: str) -> str:
        base_stem = f"{note_date.isoformat()}_{slugify_merchant(merchant)}_{amount_for_filename(amount)}AMD"
        return self._next_available_file_stem(target_user_id=user_id, base_stem=base_stem, note_date=note_date)

    def _next_available_file_stem(self, *, target_user_id: int, base_stem: str, note_date: date) -> str:
        receipt_dir = self.settings.obsidian_vault / user_dated_relpath(
            self.settings,
            target_user_id,
            "Receipts",
            datetime(note_date.year, note_date.month, note_date.day),
            "",
        )
        candidate = base_stem
        counter = 2
        with connect_database(self.settings) as connection:
            while True:
                row = connection.execute(
                    """
                    select 1
                    from documents
                    where owner_telegram_user_id = ? and file_stem = ?
                    limit 1
                    """,
                    (target_user_id, candidate),
                ).fetchone()
                if row is None and not (receipt_dir / f"{candidate}.md").exists():
                    return candidate
                candidate = f"{base_stem}_{counter}"
                counter += 1


class DocumentAmbiguousError(RuntimeError):
    pass


class DocumentStorageError(RuntimeError):
    pass


def _insert_document(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    user_id: int,
    document_type: str,
    status: str,
    file_stem: str,
    parsed: dict[str, object],
    created_at: datetime,
    reviewed_at: datetime,
    ocr_text_hash: str,
    parser_version: str = PARSER_VERSION,
    schema_version: str = PARSED_SCHEMA_VERSION,
    prompt_version: str = PROMPT_VERSION,
) -> None:
    review_payload = build_review_payload(parsed)
    possible_errors = review_payload.get("possible_errors", [])
    connection.execute(
        """
        insert into documents(
            id,
            owner_telegram_user_id,
            document_type,
            status,
            date,
            time,
            merchant,
            amount,
            currency,
            category,
            summary_ru,
            parsed_json,
            review_payload_json,
            possible_errors_json,
            ocr_text_hash,
            file_stem,
            parser_version,
            schema_version,
            prompt_version,
            created_at,
            updated_at,
            reviewed_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            user_id,
            document_type,
            status,
            str(parsed.get("date", "")),
            str(parsed.get("time", "")),
            str(parsed.get("merchant", "")),
            str(parsed.get("amount", "")),
            str(parsed.get("currency", "AMD") or "AMD"),
            str(parsed.get("category", "")),
            str(parsed.get("summary_ru", "")),
            json.dumps(parsed, ensure_ascii=False, sort_keys=True),
            json.dumps(review_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(possible_errors if isinstance(possible_errors, list) else [], ensure_ascii=False),
            ocr_text_hash,
            file_stem,
            parser_version,
            schema_version,
            prompt_version,
            created_at.isoformat(),
            created_at.isoformat(),
            reviewed_at.isoformat(),
        ),
    )


def _insert_items(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    parsed: dict[str, object],
    created_at: datetime,
) -> None:
    items = parsed.get("items", [])
    if not isinstance(items, list):
        return
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        connection.execute(
            """
            insert into document_items(
                document_id,
                position,
                name_original,
                name_ru,
                name_en,
                unit_price,
                quantity,
                unit,
                line_total,
                confidence,
                possible_error,
                created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, null, null, ?)
            """,
            (
                document_id,
                position,
                str(item.get("name_original", "")),
                str(item.get("name_ru", "")),
                str(item.get("name_en", "")),
                str(item.get("unit_price", "")),
                str(item.get("quantity", "")),
                str(item.get("unit", "")),
                str(item.get("line_total", "")),
                created_at.isoformat(),
            ),
        )


def _copy_item_rows(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    item_rows: list[sqlite3.Row],
    created_at: datetime,
) -> None:
    for row in item_rows:
        connection.execute(
            """
            insert into document_items(
                document_id,
                position,
                name_original,
                name_ru,
                name_en,
                unit_price,
                quantity,
                unit,
                line_total,
                confidence,
                possible_error,
                created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                int(row["position"]),
                row["name_original"],
                row["name_ru"],
                row["name_en"],
                row["unit_price"],
                row["quantity"],
                row["unit"],
                row["line_total"],
                row["confidence"],
                row["possible_error"],
                created_at.isoformat(),
            ),
        )


def _insert_file(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    kind: str,
    path: Path,
    absolute_path: Path,
    created_at: datetime,
) -> None:
    stat = absolute_path.stat()
    mime_type = mimetypes.guess_type(absolute_path.name)[0] or "application/octet-stream"
    connection.execute(
        """
        insert into document_files(
            document_id,
            kind,
            path,
            mime_type,
            size_bytes,
            sha256,
            created_at
        )
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            kind,
            path.as_posix(),
            mime_type,
            stat.st_size,
            _sha256_file(absolute_path),
            created_at.isoformat(),
        ),
    )


def _files_by_document(
    connection: sqlite3.Connection,
    document_ids: list[str],
) -> dict[str, tuple[ReceiptFileRecord, ...]]:
    if not document_ids:
        return {}
    placeholders = ", ".join("?" for _ in document_ids)
    rows = connection.execute(
        f"""
        select document_id, kind, path, mime_type, size_bytes, sha256
        from document_files
        where document_id in ({placeholders})
        order by id
        """,
        tuple(document_ids),
    ).fetchall()
    result: dict[str, list[ReceiptFileRecord]] = {}
    for row in rows:
        result.setdefault(str(row["document_id"]), []).append(
            ReceiptFileRecord(
                kind=str(row["kind"]),
                path=Path(str(row["path"])),
                storage=_storage_for_kind(str(row["kind"])),
                mime_type=str(row["mime_type"] or ""),
                size_bytes=int(row["size_bytes"] or 0),
                sha256=str(row["sha256"] or ""),
            )
        )
    return {key: tuple(value) for key, value in result.items()}


def _record_from_document_row(row: sqlite3.Row, files: tuple[ReceiptFileRecord, ...]) -> ReceiptRecord:
    note_file = _first_file(files, FILE_KIND_OBSIDIAN_NOTE)
    return ReceiptRecord(
        receipt_id=str(row["file_stem"] or row["id"]),
        owner_user_id=int(row["owner_telegram_user_id"]),
        note_rel=note_file.path if note_file is not None else Path(),
        manifest_rel=Path(),
        date=str(row["date"] or ""),
        merchant=str(row["merchant"] or ""),
        amount=str(row["amount"] or ""),
        currency=str(row["currency"] or "AMD"),
        created_at=str(row["created_at"] or ""),
        files=tuple(file.path for file in files if file.storage == "vault"),
        document_type=normalize_document_type(row["document_type"]),
        document_id=str(row["id"]),
        source="db",
        file_records=files,
    )


def _first_file(files: tuple[ReceiptFileRecord, ...], kind: str) -> ReceiptFileRecord | None:
    return next((file for file in files if file.kind == kind), None)


def _final_parsed(parsed: dict[str, object]) -> tuple[dict[str, object], date]:
    normalized = normalize_receipt_properties(parsed)
    note_date, used_fallback_date = _resolve_note_date(str(normalized.get("date", "")))
    normalized["date"] = note_date.isoformat()
    possible_errors = _normalized_possible_errors(normalized.get("possible_errors", []))
    if used_fallback_date:
        possible_errors.append("Дата не определена из чека; использована текущая дата.")
    normalized["possible_errors"] = possible_errors
    normalized["currency"] = "AMD"
    return normalized, note_date


def _json_object(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _resolve_note_date(value: str) -> tuple[date, bool]:
    if value:
        try:
            return date.fromisoformat(value), False
        except ValueError:
            pass
    return datetime.now().date(), True


def _relative_to(root: Path, path: Path) -> Path:
    return path.expanduser().resolve().relative_to(root.expanduser().resolve())


def _safe_storage_path(root: Path, rel_path: Path) -> Path:
    root = root.expanduser().resolve()
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Path escapes storage root.")
    return candidate


def _storage_for_kind(kind: str) -> str:
    if kind in {FILE_KIND_ORIGINAL_IMAGE, FILE_KIND_CLEAN_OCR, FILE_KIND_SOURCE_OCR}:
        return "app"
    return "vault"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _normalized_possible_errors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        compact = " ".join(str(item).split())
        if compact:
            result.append(compact[:180])
    return result[:8]


def _sha256_text_file(path: Path) -> str:
    return _sha256_file(path) if path.exists() else ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
