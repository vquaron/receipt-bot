from __future__ import annotations

import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.storage.paths import ensure_parent


class ObjectStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    backend: str
    key: str
    bucket: str
    mime_type: str
    size_bytes: int
    sha256: str
    etag: str = ""


class LocalStorage:
    backend = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def put_file(self, source: Path, key: str, *, content_type: str = "") -> StoredObject:
        target = self._path(key)
        ensure_parent(target)
        if source.resolve(strict=False) != target.resolve(strict=False):
            shutil.copy2(source, target)
        return _stored_object(self.backend, "", key, target, content_type=content_type)

    def download_to(self, key: str, target: Path) -> None:
        source = self._path(key)
        if not source.exists() or not source.is_file():
            raise ObjectStorageError(f"Local object is missing: {key}")
        ensure_parent(target)
        shutil.copy2(source, target)

    def copy(self, source_key: str, target_key: str, *, content_type: str = "") -> StoredObject:
        source = self._path(source_key)
        if not source.exists() or not source.is_file():
            raise ObjectStorageError(f"Local object is missing: {source_key}")
        target = self._path(target_key)
        ensure_parent(target)
        shutil.copy2(source, target)
        return _stored_object(self.backend, "", target_key, target, content_type=content_type)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            return False
        if not path.is_file():
            raise ObjectStorageError(f"Refusing to delete non-file local object: {key}")
        path.unlink()
        return True

    def delete_all_versions(self, key: str) -> bool:
        return self.delete(key)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ObjectStorageError("Local object key escapes storage root.")
        return candidate


class S3Storage:
    backend = "s3"

    def __init__(self, settings: Settings) -> None:
        if not settings.s3_bucket_name:
            raise ObjectStorageError("S3_BUCKET_NAME is required for S3 image storage.")
        if not settings.s3_endpoint_url:
            raise ObjectStorageError("S3_ENDPOINT_URL is required for S3 image storage.")
        if not settings.s3_access_key_id or not settings.s3_secret_access_key:
            raise ObjectStorageError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY are required for S3 image storage.")
        try:
            import boto3
        except ImportError as exc:
            raise ObjectStorageError("boto3 is required for S3 image storage.") from exc
        self.bucket = settings.s3_bucket_name
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def put_file(self, source: Path, key: str, *, content_type: str = "") -> StoredObject:
        metadata = _file_metadata(source, content_type=content_type)
        extra_args = {"ContentType": metadata.mime_type, "Metadata": {"sha256": metadata.sha256}}
        self.client.upload_file(str(source), self.bucket, key, ExtraArgs=extra_args)
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return StoredObject(
            backend=self.backend,
            bucket=self.bucket,
            key=key,
            mime_type=str(head.get("ContentType") or metadata.mime_type),
            size_bytes=int(head.get("ContentLength") or metadata.size_bytes),
            sha256=metadata.sha256,
            etag=str(head.get("ETag") or "").strip('"'),
        )

    def download_to(self, key: str, target: Path) -> None:
        ensure_parent(target)
        self.client.download_file(self.bucket, key, str(target))

    def copy(self, source_key: str, target_key: str, *, content_type: str = "") -> StoredObject:
        self.client.copy_object(
            Bucket=self.bucket,
            Key=target_key,
            CopySource={"Bucket": self.bucket, "Key": source_key},
            MetadataDirective="COPY",
        )
        head = self.client.head_object(Bucket=self.bucket, Key=target_key)
        metadata = head.get("Metadata") or {}
        return StoredObject(
            backend=self.backend,
            bucket=self.bucket,
            key=target_key,
            mime_type=str(head.get("ContentType") or content_type or "application/octet-stream"),
            size_bytes=int(head.get("ContentLength") or 0),
            sha256=str(metadata.get("sha256") or ""),
            etag=str(head.get("ETag") or "").strip('"'),
        )

    def delete(self, key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def delete_all_versions(self, key: str) -> bool:
        deleted = False
        paginator = self.client.get_paginator("list_object_versions")
        try:
            pages = paginator.paginate(Bucket=self.bucket, Prefix=key)
            for page in pages:
                objects = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]}
                    for item in [*(page.get("Versions") or []), *(page.get("DeleteMarkers") or [])]
                    if item.get("Key") == key and item.get("VersionId")
                ]
                if objects:
                    self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects, "Quiet": True})
                    deleted = True
        except Exception:
            self.delete(key)
            return True
        if not deleted:
            self.delete(key)
        return True


def image_storage(settings: Settings):
    if settings.storage_image_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.app_storage_dir)


def storage_key(settings: Settings, *parts: str) -> str:
    prefix = settings.s3_key_prefix.strip("/") if settings.storage_image_backend == "s3" else ""
    clean_parts = [part.strip("/") for part in parts if part.strip("/")]
    return "/".join(([prefix] if prefix else []) + clean_parts)


@dataclass(frozen=True, slots=True)
class _FileMetadata:
    mime_type: str
    size_bytes: int
    sha256: str


def _stored_object(backend: str, bucket: str, key: str, path: Path, *, content_type: str = "") -> StoredObject:
    metadata = _file_metadata(path, content_type=content_type)
    return StoredObject(
        backend=backend,
        bucket=bucket,
        key=key,
        mime_type=metadata.mime_type,
        size_bytes=metadata.size_bytes,
        sha256=metadata.sha256,
    )


def _file_metadata(path: Path, *, content_type: str = "") -> _FileMetadata:
    stat = path.stat()
    return _FileMetadata(
        mime_type=content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size_bytes=stat.st_size,
        sha256=_sha256_file(path),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
