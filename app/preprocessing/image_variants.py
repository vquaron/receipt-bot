from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.preprocessing.base import PreprocessingResult, disabled_result
from app.preprocessing.cloudmersive import ENDPOINT_PATH, unskew_document
from app.storage.paths import dated_relpath, ensure_parent


LOGGER = logging.getLogger(__name__)


def preprocess_receipt_image(input_path: Path, temp_dir: Path, settings: Settings) -> PreprocessingResult:
    if not settings.receipt_preprocessing_enabled:
        LOGGER.info("Receipt image preprocessing disabled.")
        return disabled_result(input_path)

    provider = settings.receipt_preprocessing_provider
    LOGGER.info("Receipt image preprocessing started: provider=%s input=%s", provider, input_path.name)
    if provider != "cloudmersive":
        result = PreprocessingResult(
            input_path=input_path,
            output_path=None,
            provider=provider,
            ok=False,
            error=f"Unsupported preprocessing provider: {provider}",
            used_for_ocr=input_path,
        )
        _write_debug_if_enabled(settings, result)
        LOGGER.warning("Receipt image preprocessing failed: fallback_to_original=true error=%s", result.error)
        return result

    try:
        result = unskew_document(input_path, temp_dir, settings)
    except Exception as exc:
        result = PreprocessingResult(
            input_path=input_path,
            output_path=None,
            provider="cloudmersive",
            ok=False,
            error=f"Unexpected preprocessing error: {exc}",
            used_for_ocr=input_path,
        )

    result = _write_debug_if_enabled(settings, result)
    if result.ok:
        LOGGER.info("Receipt image preprocessing succeeded: output=%s", result.output_path.name if result.output_path else "")
    else:
        LOGGER.warning("Receipt image preprocessing failed: fallback_to_original=true error=%s", result.error)
    return result


def _write_debug_if_enabled(settings: Settings, result: PreprocessingResult) -> PreprocessingResult:
    if not settings.cloudmersive_save_debug:
        return result
    debug_path = settings.obsidian_vault / dated_relpath(
        "DEBUG/preprocessing",
        _mtime_as_datetime(result.input_path),
        f"{result.input_path.stem}.{result.provider}.json",
    )
    payload = {
        "provider": result.provider,
        "endpoint": ENDPOINT_PATH if result.provider == "cloudmersive" else "",
        "input_path": str(result.input_path),
        "output_path": str(result.output_path) if result.output_path else None,
        "ok": result.ok,
        "http_status": result.http_status,
        "content_type": result.content_type,
        "error": result.error,
    }
    try:
        ensure_parent(debug_path)
        debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        LOGGER.exception("Failed to write preprocessing debug file.")
        return result
    return PreprocessingResult(
        input_path=result.input_path,
        output_path=result.output_path,
        provider=result.provider,
        ok=result.ok,
        error=result.error,
        used_for_ocr=result.used_for_ocr,
        debug_path=debug_path,
        http_status=result.http_status,
        content_type=result.content_type,
    )


def _mtime_as_datetime(path: Path):
    return datetime.fromtimestamp(path.stat().st_mtime)
