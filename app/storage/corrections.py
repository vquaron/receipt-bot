from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.storage.normalization import normalize_merchant_name, normalize_receipt_properties
from app.storage.paths import ensure_parent


CORRECTIONS_FILE = "corrections.json"


class CorrectionStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / CORRECTIONS_FILE

    def apply(self, parsed: dict[str, Any]) -> dict[str, Any]:
        rules = self._load()
        result = deepcopy(parsed)
        merchant = str(result.get("merchant", ""))
        result["merchant"] = normalize_merchant_name(_lookup(rules["merchants"], merchant) or merchant)
        items = result.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                unit = str(item.get("unit", ""))
                item["unit"] = _lookup(rules["units"], unit) or unit
                original_name = str(item.get("name_original", ""))
                corrected_from_original = _lookup(rules["item_names_by_original"], original_name)
                if corrected_from_original:
                    item["name_ru"] = corrected_from_original
                    continue
                name_ru = str(item.get("name_ru", ""))
                item["name_ru"] = _lookup(rules["item_names_ru"], name_ru) or name_ru
        return normalize_receipt_properties(result)

    def learn(self, before: dict[str, Any], after: dict[str, Any]) -> int:
        rules = self._load()
        added = 0
        added += _record_mapping(
            rules["merchants"],
            str(before.get("merchant", "")),
            normalize_merchant_name(str(after.get("merchant", ""))),
        )
        before_items = before.get("items", [])
        after_items = after.get("items", [])
        if isinstance(before_items, list) and isinstance(after_items, list):
            for index, after_item in enumerate(after_items):
                if not isinstance(after_item, dict) or index >= len(before_items):
                    continue
                before_item = before_items[index]
                if not isinstance(before_item, dict):
                    continue
                added += _record_mapping(
                    rules["units"],
                    str(before_item.get("unit", "")),
                    str(after_item.get("unit", "")),
                )
                before_name_ru = str(before_item.get("name_ru", ""))
                after_name_ru = str(after_item.get("name_ru", ""))
                if before_name_ru.strip() != after_name_ru.strip():
                    added += _record_mapping(rules["item_names_ru"], before_name_ru, after_name_ru)
                    added += _record_mapping(
                        rules["item_names_by_original"],
                        str(before_item.get("name_original", "")),
                        after_name_ru,
                    )
        if added:
            self._save(rules)
        return added

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return _empty_rules()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _empty_rules()
        rules = _empty_rules()
        if isinstance(loaded, dict):
            for key in rules:
                value = loaded.get(key)
                if isinstance(value, dict):
                    rules[key].update(
                        {
                            str(source): str(target)
                            for source, target in value.items()
                            if str(source).strip() and str(target).strip()
                        }
                    )
        return rules

    def _save(self, rules: dict[str, dict[str, str]]) -> None:
        ensure_parent(self.path)
        self.path.write_text(
            json.dumps(rules, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _empty_rules() -> dict[str, dict[str, str]]:
    return {
        "merchants": {},
        "units": {},
        "item_names_ru": {},
        "item_names_by_original": {},
    }


def _lookup(mapping: dict[str, str], value: str) -> str | None:
    if value in mapping:
        return mapping[value]
    lowered = value.lower()
    for source, target in mapping.items():
        if source.lower() == lowered:
            return target
    return None


def _record_mapping(mapping: dict[str, str], source: str, target: str) -> int:
    source = source.strip()
    target = target.strip()
    if not source or not target or source == target:
        return 0
    if mapping.get(source) == target:
        return 0
    mapping[source] = target
    return 1
