import json
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.obsidian.writer import write_receipt_note
from app.preprocessing.base import PreprocessingResult
from app.review.models import ReceiptSession


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="telegram-token",
        openai_api_key="openai-key",
        obsidian_vault=tmp_path,
        data_dir=tmp_path / "data",
        admin_telegram_user_ids=frozenset({111}),
        allowed_telegram_user_ids=frozenset({111}),
        receipt_preprocessing_enabled=True,
        receipt_preprocessing_provider="cloudmersive",
        cloudmersive_api_key="cm-secret",
    )


def test_manifest_includes_preprocessed_image(tmp_path: Path) -> None:
    original = tmp_path / "tmp/original.jpg"
    preprocessed = tmp_path / "tmp/original.preprocessed.jpg"
    clean = tmp_path / "tmp/clean.txt"
    source = tmp_path / "tmp/source.txt"
    for path in (original, preprocessed, clean, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    session = ReceiptSession(
        user_id=111,
        image_path=original,
        clean_ocr_path=clean,
        source_ocr_path=source,
        temporary_base_name="tmp",
        created_at=datetime(2026, 4, 7, 20, 41),
        preprocessing_result=PreprocessingResult(
            input_path=original,
            output_path=preprocessed,
            provider="cloudmersive",
            ok=True,
            error=None,
            used_for_ocr=preprocessed,
            http_status=200,
            content_type="image/jpeg",
        ),
    )

    artifact = write_receipt_note(
        settings(tmp_path),
        session,
        {
            "date": "2026-04-07",
            "time": "20:41:00",
            "merchant": "Zovq Supermarket",
            "amount": "20",
            "category": "Grocery",
            "summary_ru": "",
            "items": [],
            "possible_errors": [],
        },
    )

    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    assert manifest["preprocessing"] == {
        "enabled": True,
        "provider": "cloudmersive",
        "ok": True,
        "error": None,
    }
    assert manifest["original_image"] == "Attachments/receipts/2026/04/2026-04-07_zovq_supermarket_20AMD.jpg"
    assert (
        manifest["preprocessed_image"]
        == "Attachments/receipts_preprocessed/2026/04/2026-04-07_zovq_supermarket_20AMD.preprocessed.jpg"
    )
    assert manifest["ocr_input_image"] == manifest["preprocessed_image"]
    assert manifest["preprocessed_image"] in manifest["files"]
