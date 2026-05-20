from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReceiptRecord:
    receipt_id: str
    owner_user_id: int
    note_rel: Path
    manifest_rel: Path
    date: str
    merchant: str
    amount: str
    currency: str
    created_at: str
    files: tuple[Path, ...]

