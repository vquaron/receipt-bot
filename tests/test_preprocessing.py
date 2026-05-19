import json
import logging
from pathlib import Path

import httpx

from app.config import Settings
from app.preprocessing.cloudmersive import unskew_document
from app.preprocessing.image_variants import preprocess_receipt_image


def settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    api_key: str = "cm-secret",
    save_debug: bool = True,
) -> Settings:
    return Settings(
        telegram_bot_token="telegram-token",
        openai_api_key="openai-key",
        obsidian_vault=tmp_path,
        data_dir=tmp_path / "data",
        admin_telegram_user_ids=frozenset({111}),
        allowed_telegram_user_ids=frozenset({111}),
        receipt_preprocessing_enabled=enabled,
        cloudmersive_api_key=api_key,
        cloudmersive_save_debug=save_debug,
    )


def test_preprocessing_disabled_does_not_call_provider(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"original-image")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Cloudmersive should not be called")

    monkeypatch.setattr("app.preprocessing.image_variants.unskew_document", fail_if_called)

    result = preprocess_receipt_image(image, tmp_path / "preprocessed", settings(tmp_path, enabled=False))

    assert result.provider == "disabled"
    assert not result.ok
    assert result.used_for_ocr == image


def test_cloudmersive_success_writes_image(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"original-image")
    output_bytes = b"\xff\xd8" + (b"x" * 256)

    def fake_post(*args, **kwargs):
        assert kwargs["headers"]["Apikey"] == "cm-secret"
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=output_bytes)

    monkeypatch.setattr("app.preprocessing.cloudmersive.httpx.post", fake_post)

    result = unskew_document(image, tmp_path / "preprocessed", settings(tmp_path))

    assert result.ok
    assert result.output_path is not None
    assert result.output_path.exists()
    assert result.output_path.read_bytes() == output_bytes
    assert result.used_for_ocr == result.output_path


def test_cloudmersive_http_error_falls_back_to_original(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"original-image")

    def fake_post(*args, **kwargs):
        return httpx.Response(401, headers={"content-type": "application/json"}, content=b"{}")

    monkeypatch.setattr("app.preprocessing.cloudmersive.httpx.post", fake_post)

    result = unskew_document(image, tmp_path / "preprocessed", settings(tmp_path))

    assert not result.ok
    assert result.used_for_ocr == image
    assert "HTTP 401" in str(result.error)


def test_cloudmersive_timeout_falls_back_to_original(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"original-image")

    def fake_post(*args, **kwargs):
        raise httpx.TimeoutException("too slow")

    monkeypatch.setattr("app.preprocessing.cloudmersive.httpx.post", fake_post)

    result = unskew_document(image, tmp_path / "preprocessed", settings(tmp_path))

    assert not result.ok
    assert result.used_for_ocr == image
    assert "Timeout" in str(result.error)


def test_preprocessing_debug_has_no_api_key(tmp_path: Path, monkeypatch, caplog) -> None:
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"original-image")

    def fake_post(*args, **kwargs):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8" + (b"x" * 256))

    monkeypatch.setattr("app.preprocessing.cloudmersive.httpx.post", fake_post)

    caplog.set_level(logging.INFO)
    result = preprocess_receipt_image(image, tmp_path / "preprocessed", settings(tmp_path, api_key="cm-secret"))

    assert result.ok
    assert result.debug_path is not None
    assert "cm-secret" not in result.debug_path.read_text(encoding="utf-8")
    assert "cm-secret" not in caplog.text
    debug = json.loads(result.debug_path.read_text(encoding="utf-8"))
    assert debug["provider"] == "cloudmersive"
    assert debug["ok"] is True
