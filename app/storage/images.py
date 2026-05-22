from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import Settings
from app.storage.paths import ensure_parent


LOGGER = logging.getLogger(__name__)


def create_stored_image(source: Path, target: Path, settings: Settings) -> Path:
    ensure_parent(target)
    try:
        from PIL import Image, ImageOps
    except ImportError:
        LOGGER.warning("Pillow is not installed; copying original image as stored image.")
        shutil.copy2(source, target)
        return target

    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(
                (
                    max(1, settings.storage_stored_image_max_edge_px),
                    max(1, settings.storage_stored_image_max_edge_px),
                )
            )
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(
                target,
                format="JPEG",
                quality=_jpeg_quality(settings),
                optimize=True,
            )
    except Exception:
        LOGGER.warning("Failed to optimize stored image; copying original image.", exc_info=True)
        shutil.copy2(source, target)
    return target


def _jpeg_quality(settings: Settings) -> int:
    return min(95, max(1, settings.storage_stored_image_jpeg_quality))
