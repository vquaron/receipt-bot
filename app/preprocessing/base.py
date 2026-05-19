from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PreprocessingResult:
    input_path: Path
    output_path: Path | None
    provider: str
    ok: bool
    error: str | None = None
    used_for_ocr: Path | None = None
    debug_path: Path | None = None
    http_status: int | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        if self.used_for_ocr is None:
            object.__setattr__(self, "used_for_ocr", self.output_path if self.ok and self.output_path else self.input_path)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("input_path", "output_path", "used_for_ocr", "debug_path"):
            value = data.get(key)
            data[key] = str(value) if value else None
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any] | None, *, fallback_input: Path) -> "PreprocessingResult":
        if not data:
            return disabled_result(fallback_input)
        return cls(
            input_path=Path(str(data.get("input_path") or fallback_input)),
            output_path=Path(str(data["output_path"])) if data.get("output_path") else None,
            provider=str(data.get("provider") or "disabled"),
            ok=bool(data.get("ok")),
            error=str(data["error"]) if data.get("error") else None,
            used_for_ocr=Path(str(data["used_for_ocr"])) if data.get("used_for_ocr") else fallback_input,
            debug_path=Path(str(data["debug_path"])) if data.get("debug_path") else None,
            http_status=int(data["http_status"]) if data.get("http_status") is not None else None,
            content_type=str(data["content_type"]) if data.get("content_type") else None,
        )


def disabled_result(input_path: Path) -> PreprocessingResult:
    return PreprocessingResult(
        input_path=input_path,
        output_path=None,
        provider="disabled",
        ok=False,
        error=None,
        used_for_ocr=input_path,
    )
