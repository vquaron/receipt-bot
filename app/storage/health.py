from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.repositories.documents import (
    DOCUMENT_STATUS_CONFIRMED,
    DOCUMENT_STATUS_DELETED,
    DOCUMENT_STATUS_EXPORT_FAILED,
    STORAGE_BACKEND_LOCAL,
    STORAGE_BACKEND_OBSIDIAN,
    STORAGE_BACKEND_S3,
)
from app.storage.object_store import S3Storage


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True, slots=True)
class StorageHealthIssue:
    severity: str
    code: str
    document_id: str
    file_kind: str
    storage_backend: str
    path_or_key: str
    message: str


@dataclass(frozen=True, slots=True)
class StorageHealthReport:
    issues: tuple[StorageHealthIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == SEVERITY_WARNING)


class StorageHealthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def check(self) -> StorageHealthReport:
        initialize_database(self.settings)
        issues: list[StorageHealthIssue] = []
        with connect_database(self.settings) as connection:
            documents = connection.execute(
                """
                select id, status, deleted_at
                from documents
                order by created_at desc
                """
            ).fetchall()
            rows = connection.execute(
                """
                select
                    document_id,
                    kind,
                    path,
                    storage_backend,
                    storage_key,
                    bucket,
                    is_canonical,
                    size_bytes,
                    sha256
                from document_files
                order by document_id, id
                """
            ).fetchall()

        document_status = {str(row["id"]): str(row["status"] or "") for row in documents}
        deleted_documents = {str(row["id"]) for row in documents if row["deleted_at"] is not None or str(row["status"]) == DOCUMENT_STATUS_DELETED}
        live_document_ids = {document_id for document_id, status in document_status.items() if status in {DOCUMENT_STATUS_CONFIRMED, DOCUMENT_STATUS_EXPORT_FAILED}}

        for document_id, status in document_status.items():
            if status not in {DOCUMENT_STATUS_CONFIRMED, DOCUMENT_STATUS_EXPORT_FAILED, DOCUMENT_STATUS_DELETED}:
                issues.append(
                    StorageHealthIssue(
                        severity=SEVERITY_WARNING,
                        code="document_non_final_status",
                        document_id=document_id,
                        file_kind="",
                        storage_backend="",
                        path_or_key="",
                        message=f"Document has non-final storage status: {status}",
                    )
                )

        referenced_app_paths: set[Path] = set()
        s3_store: S3Storage | None = None
        for row in rows:
            document_id = str(row["document_id"])
            kind = str(row["kind"] or "")
            backend = str(row["storage_backend"] or _backend_for_kind(kind))
            storage_key = str(row["storage_key"] or row["path"] or "")
            path_text = str(row["path"] or "")
            is_deleted = document_id in deleted_documents
            if backend == STORAGE_BACKEND_LOCAL:
                self._check_local_file(
                    issues,
                    document_id=document_id,
                    kind=kind,
                    backend=backend,
                    path_text=path_text,
                    size_bytes=int(row["size_bytes"] or 0),
                    sha256=str(row["sha256"] or ""),
                    is_deleted=is_deleted,
                    referenced_app_paths=referenced_app_paths,
                )
            elif backend == STORAGE_BACKEND_OBSIDIAN:
                self._check_vault_file(
                    issues,
                    document_id=document_id,
                    kind=kind,
                    backend=backend,
                    path_text=path_text,
                    size_bytes=int(row["size_bytes"] or 0),
                    sha256=str(row["sha256"] or ""),
                    is_deleted=is_deleted,
                )
            elif backend == STORAGE_BACKEND_S3:
                if is_deleted:
                    continue
                if _unsafe_object_key(storage_key):
                    issues.append(_issue(SEVERITY_ERROR, "unsafe_s3_key", document_id, kind, backend, storage_key, "S3 storage key is unsafe."))
                    continue
                try:
                    if s3_store is None:
                        s3_store = S3Storage(self.settings)
                    head = s3_store.head(storage_key)
                except Exception as exc:
                    issues.append(_issue(SEVERITY_ERROR, "s3_object_missing_or_unavailable", document_id, kind, backend, storage_key, str(exc)))
                    continue
                expected_size = int(row["size_bytes"] or 0)
                if expected_size and head.size_bytes != expected_size:
                    issues.append(_issue(SEVERITY_ERROR, "size_mismatch", document_id, kind, backend, storage_key, "S3 object size does not match DB metadata."))
                expected_sha = str(row["sha256"] or "")
                if expected_sha and head.sha256 and head.sha256 != expected_sha:
                    issues.append(_issue(SEVERITY_ERROR, "sha256_mismatch", document_id, kind, backend, storage_key, "S3 sha256 metadata does not match DB metadata."))
            else:
                issues.append(_issue(SEVERITY_ERROR, "unknown_storage_backend", document_id, kind, backend, storage_key or path_text, "Unknown storage backend."))

        issues.extend(self._orphan_app_files(referenced_app_paths, live_document_ids))
        return StorageHealthReport(issues=tuple(issues))

    def _check_local_file(
        self,
        issues: list[StorageHealthIssue],
        *,
        document_id: str,
        kind: str,
        backend: str,
        path_text: str,
        size_bytes: int,
        sha256: str,
        is_deleted: bool,
        referenced_app_paths: set[Path],
    ) -> None:
        try:
            path = _safe_path(self.settings.app_storage_dir, path_text)
        except ValueError:
            issues.append(_issue(SEVERITY_ERROR, "unsafe_path", document_id, kind, backend, path_text, "Local file path escapes APP_STORAGE_DIR."))
            return
        referenced_app_paths.add(path)
        self._check_filesystem_path(issues, document_id, kind, backend, path, path_text, size_bytes, sha256, is_deleted)

    def _check_vault_file(
        self,
        issues: list[StorageHealthIssue],
        *,
        document_id: str,
        kind: str,
        backend: str,
        path_text: str,
        size_bytes: int,
        sha256: str,
        is_deleted: bool,
    ) -> None:
        try:
            path = _safe_path(self.settings.obsidian_vault, path_text)
        except ValueError:
            issues.append(_issue(SEVERITY_ERROR, "unsafe_path", document_id, kind, backend, path_text, "Obsidian file path escapes OBSIDIAN_VAULT."))
            return
        self._check_filesystem_path(issues, document_id, kind, backend, path, path_text, size_bytes, sha256, is_deleted)

    def _check_filesystem_path(
        self,
        issues: list[StorageHealthIssue],
        document_id: str,
        kind: str,
        backend: str,
        path: Path,
        path_text: str,
        size_bytes: int,
        sha256: str,
        is_deleted: bool,
    ) -> None:
        if is_deleted:
            if path.exists():
                issues.append(_issue(SEVERITY_WARNING, "deleted_file_leftover", document_id, kind, backend, path_text, "Deleted document still has a file on disk."))
            return
        if not path.exists():
            issues.append(_issue(SEVERITY_ERROR, "missing_file", document_id, kind, backend, path_text, "Recorded file is missing."))
            return
        if not path.is_file():
            issues.append(_issue(SEVERITY_ERROR, "non_file_target", document_id, kind, backend, path_text, "Recorded file path is not a regular file."))
            return
        stat = path.stat()
        if size_bytes and stat.st_size != size_bytes:
            issues.append(_issue(SEVERITY_ERROR, "size_mismatch", document_id, kind, backend, path_text, "File size does not match DB metadata."))
        if sha256:
            try:
                actual_sha = _sha256_file(path)
            except OSError as exc:
                issues.append(_issue(SEVERITY_ERROR, "file_unreadable", document_id, kind, backend, path_text, str(exc)))
                return
            if actual_sha != sha256:
                issues.append(_issue(SEVERITY_ERROR, "sha256_mismatch", document_id, kind, backend, path_text, "File sha256 does not match DB metadata."))

    def _orphan_app_files(self, referenced_app_paths: set[Path], live_document_ids: set[str]) -> list[StorageHealthIssue]:
        root = self.settings.app_storage_dir / "documents"
        if not root.exists() or not root.is_dir() or root.is_symlink():
            return []
        issues: list[StorageHealthIssue] = []
        referenced = {path.resolve(strict=False) for path in referenced_app_paths}
        for path in sorted(root.glob("*/*")):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=False)
                rel = path.relative_to(self.settings.app_storage_dir)
            except ValueError:
                continue
            document_id = path.parent.name
            if resolved not in referenced and document_id not in live_document_ids:
                issues.append(_issue(SEVERITY_WARNING, "orphan_app_file", document_id, "", STORAGE_BACKEND_LOCAL, rel.as_posix(), "App storage file is not referenced by live document_files."))
            elif resolved not in referenced:
                issues.append(_issue(SEVERITY_WARNING, "orphan_app_file", document_id, "", STORAGE_BACKEND_LOCAL, rel.as_posix(), "App storage file is not referenced by document_files."))
        return issues


def _issue(severity: str, code: str, document_id: str, kind: str, backend: str, path_or_key: str, message: str) -> StorageHealthIssue:
    return StorageHealthIssue(
        severity=severity,
        code=code,
        document_id=document_id,
        file_kind=kind,
        storage_backend=backend,
        path_or_key=path_or_key,
        message=message,
    )


def _safe_path(root: Path, rel_path: str) -> Path:
    root = root.expanduser().resolve()
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Path escapes storage root.")
    return candidate


def _unsafe_object_key(key: str) -> bool:
    path = Path(key)
    return not key or path.is_absolute() or ".." in path.parts


def _backend_for_kind(kind: str) -> str:
    if kind in {"original_image", "stored_image", "clean_ocr", "source_ocr"}:
        return STORAGE_BACKEND_LOCAL
    return STORAGE_BACKEND_OBSIDIAN


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
