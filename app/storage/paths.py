from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def dated_relpath(root: str, stamp: datetime, filename: str) -> Path:
    return Path(root) / f"{stamp:%Y}" / f"{stamp:%m}" / filename


def yaml_string(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def safe_vault_path(vault: Path, rel_path: str | Path) -> Path:
    vault = vault.expanduser().resolve()
    candidate = (vault / rel_path).resolve()
    if not candidate.is_relative_to(vault):
        raise ValueError("Path escapes Obsidian vault.")
    return candidate


def next_available_stem(directory: Path, stem: str, suffix: str) -> str:
    candidate = stem
    counter = 2
    while (directory / f"{candidate}{suffix}").exists():
        candidate = f"{stem}_{counter}"
        counter += 1
    return candidate
