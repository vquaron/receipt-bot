from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median
import unicodedata

from google.api_core.exceptions import GoogleAPICallError, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import vision

from app.storage.normalization import clean_ocr_text, normalize_mixed_receipt_units


LANGUAGE_HINTS = ["hy", "ru", "en"]


class GoogleVisionError(RuntimeError):
    pass


class GoogleVisionCredentialsError(RuntimeError):
    pass


class GoogleVisionNetworkError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WordBox:
    text: str
    left: int
    right: int
    top: int
    bottom: int

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)

    @property
    def char_width(self) -> float:
        return max(1.0, (self.right - self.left) / max(1, len(self.text)))


def run_document_ocr(image_path: Path) -> tuple[str, str]:
    try:
        client = vision.ImageAnnotatorClient()
    except DefaultCredentialsError as exc:
        raise GoogleVisionCredentialsError(str(exc)) from exc

    image = vision.Image(content=image_path.read_bytes())
    try:
        response = client.document_text_detection(
            image=image,
            image_context=vision.ImageContext(language_hints=LANGUAGE_HINTS),
        )
    except ServiceUnavailable as exc:
        raise GoogleVisionNetworkError(str(exc)) from exc
    except GoogleAPICallError as exc:
        raise GoogleVisionError(str(exc)) from exc

    if response.error.message:
        raise GoogleVisionError(response.error.message)

    raw_text = response.full_text_annotation.text or ""
    clean_text = _format_ocr_from_layout(response.full_text_annotation) or clean_ocr_text(raw_text)
    return raw_text, clean_text


def _format_ocr_from_layout(annotation: vision.TextAnnotation) -> str:
    words = _extract_words(annotation)
    if not words:
        return ""

    line_threshold = max(8.0, median(word.height for word in words) * 0.65)
    lines: list[list[WordBox]] = []
    current_line: list[WordBox] = []
    current_center = 0.0
    for word in sorted(words, key=lambda item: (item.center_y, item.left)):
        if not current_line:
            current_line = [word]
            current_center = word.center_y
            continue
        if abs(word.center_y - current_center) <= line_threshold:
            current_line.append(word)
            current_center = sum(item.center_y for item in current_line) / len(current_line)
        else:
            lines.append(current_line)
            current_line = [word]
            current_center = word.center_y
    if current_line:
        lines.append(current_line)

    char_width = max(1.0, median(word.char_width for word in words))
    return _clean_layout_text(
        "\n".join(
            _format_line(line, char_width)
            for line in lines
            if any(word.text.strip() for word in line)
        )
    )


def _extract_words(annotation: vision.TextAnnotation) -> list[WordBox]:
    words: list[WordBox] = []
    for page in annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    text = "".join(symbol.text for symbol in word.symbols).strip()
                    if not text:
                        continue
                    vertices = word.bounding_box.vertices
                    xs = [vertex.x for vertex in vertices]
                    ys = [vertex.y for vertex in vertices]
                    words.append(
                        WordBox(
                            text=unicodedata.normalize("NFC", text),
                            left=min(xs),
                            right=max(xs),
                            top=min(ys),
                            bottom=max(ys),
                        )
                    )
    return words


def _format_line(line: list[WordBox], char_width: float) -> str:
    sorted_words = sorted(line, key=lambda item: item.left)
    parts = [sorted_words[0].text]
    previous = sorted_words[0]
    for word in sorted_words[1:]:
        gap_px = max(0, word.left - previous.right)
        gap_chars = max(1, round(gap_px / char_width))
        parts.append(" " * min(gap_chars, 12))
        parts.append(word.text)
        previous = word
    return normalize_mixed_receipt_units("".join(parts).rstrip())


def _clean_layout_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    lines = [line.rstrip() for line in normalized.splitlines()]
    return "\n".join(line for line in lines if line.strip())
