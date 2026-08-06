from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReceiptFileRecord:
    kind: str
    path: Path
    storage_backend: str = ""
    storage_key: str = ""
    bucket: str = ""
    is_canonical: bool = False
    mime_type: str = ""
    size_bytes: int = 0
    sha256: str = ""
    etag: str = ""


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    receipt_id: str
    owner_user_id: int
    note_rel: Path
    date: str
    merchant: str
    amount: str
    currency: str
    created_at: str
    files: tuple[Path, ...]
    document_type: str = "receipt"
    document_id: str = ""
    file_records: tuple[ReceiptFileRecord, ...] = ()
