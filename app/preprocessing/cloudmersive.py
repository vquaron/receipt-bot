from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import Settings
from app.preprocessing.base import PreprocessingResult
from app.storage.paths import ensure_parent


LOGGER = logging.getLogger(__name__)
ENDPOINT_PATH = "/image/recognize/detect-document/unskew"
MIN_OUTPUT_BYTES = 128


def unskew_document(input_path: Path, temp_dir: Path, settings: Settings) -> PreprocessingResult:
    if not settings.cloudmersive_api_key:
        return PreprocessingResult(
            input_path=input_path,
            output_path=None,
            provider="cloudmersive",
            ok=False,
            error="CLOUDMERSIVE_API_KEY is not set.",
            used_for_ocr=input_path,
        )

    endpoint = f"{settings.cloudmersive_base_url.rstrip('/')}{ENDPOINT_PATH}"
    try:
        with input_path.open("rb") as image_file:
            response = httpx.post(
                endpoint,
                headers={"Apikey": settings.cloudmersive_api_key},
                files={"imageFile": (input_path.name, image_file, _mime_type(input_path))},
                timeout=settings.cloudmersive_timeout_seconds,
            )
    except httpx.TimeoutException as exc:
        return _failed(input_path, f"Timeout: {exc}")
    except httpx.HTTPError as exc:
        return _failed(input_path, f"Network error: {exc}")
    except OSError as exc:
        return _failed(input_path, f"Input image error: {exc}")

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not 200 <= response.status_code < 300:
        return _failed(
            input_path,
            f"HTTP {response.status_code}: {response.reason_phrase}",
            status=response.status_code,
            content_type=content_type,
        )
    if not content_type.startswith("image/"):
        return _failed(
            input_path,
            f"Non-image response: {content_type or 'unknown content-type'}",
            status=response.status_code,
            content_type=content_type,
        )
    if len(response.content) < MIN_OUTPUT_BYTES:
        return _failed(
            input_path,
            "Output image is too small.",
            status=response.status_code,
            content_type=content_type,
        )

    suffix = _suffix_for_content_type(content_type)
    if suffix == ".jpg" and content_type not in {"image/jpeg", "image/jpg"}:
        LOGGER.warning("Unknown Cloudmersive image content-type=%s; saving as jpg.", content_type)

    output_path = temp_dir / f"{input_path.stem}.preprocessed{suffix}"
    try:
        ensure_parent(output_path)
        output_path.write_bytes(response.content)
    except OSError as exc:
        return _failed(
            input_path,
            f"Failed to save output image: {exc}",
            status=response.status_code,
            content_type=content_type,
        )

    if output_path.stat().st_size < MIN_OUTPUT_BYTES:
        return _failed(
            input_path,
            "Saved output image is too small.",
            status=response.status_code,
            content_type=content_type,
        )

    return PreprocessingResult(
        input_path=input_path,
        output_path=output_path,
        provider="cloudmersive",
        ok=True,
        error=None,
        used_for_ocr=output_path,
        http_status=response.status_code,
        content_type=content_type,
    )


def _failed(
    input_path: Path,
    error: str,
    *,
    status: int | None = None,
    content_type: str | None = None,
) -> PreprocessingResult:
    return PreprocessingResult(
        input_path=input_path,
        output_path=None,
        provider="cloudmersive",
        ok=False,
        error=error,
        used_for_ocr=input_path,
        http_status=status,
        content_type=content_type,
    )


def _suffix_for_content_type(content_type: str) -> str:
    if content_type in {"image/png"}:
        return ".png"
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    return ".jpg"


def _mime_type(path: Path) -> str:
    if path.suffix.lower() == ".png":
        return "image/png"
    return "image/jpeg"
