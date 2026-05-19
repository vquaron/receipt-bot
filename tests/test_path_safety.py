from pathlib import Path

import pytest

from app.storage.paths import safe_vault_path


def test_safe_vault_path_accepts_inside_path(tmp_path: Path) -> None:
    assert safe_vault_path(tmp_path, "Receipts/2026/05/a.md") == (
        tmp_path / "Receipts/2026/05/a.md"
    ).resolve()


def test_safe_vault_path_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_vault_path(tmp_path, "../outside.md")
