from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db import initialize_database
from app.db.connection import connect_database
from app.storage.normalization import normalize_merchant_name, normalize_receipt_properties


SCOPE_MERCHANT = "merchant"
SCOPE_UNIT = "unit"
SCOPE_ITEM_NAME_RU = "item_name_ru"
SCOPE_ITEM_NAME_ORIGINAL = "item_name_original"

LEGACY_SCOPE_KEYS = {
    SCOPE_MERCHANT: "merchants",
    SCOPE_UNIT: "units",
    SCOPE_ITEM_NAME_RU: "item_names_ru",
    SCOPE_ITEM_NAME_ORIGINAL: "item_names_by_original",
}


@dataclass(frozen=True, slots=True)
class CorrectionRule:
    id: int
    scope: str
    source: str
    target: str


class CorrectionStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        initialize_database(settings)

    def apply(self, parsed: dict[str, Any], *, owner_telegram_user_id: int | None = None) -> dict[str, Any]:
        rules = self._load_rules(owner_telegram_user_id)
        applied_rule_ids: list[int] = []
        result = deepcopy(parsed)
        merchant = str(result.get("merchant", ""))
        merchant_rule = _lookup(rules[SCOPE_MERCHANT], merchant)
        if merchant_rule is not None and merchant_rule.target != merchant:
            result["merchant"] = normalize_merchant_name(merchant_rule.target)
            applied_rule_ids.append(merchant_rule.id)
        else:
            result["merchant"] = normalize_merchant_name(merchant)

        items = result.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                unit = str(item.get("unit", ""))
                unit_rule = _lookup(rules[SCOPE_UNIT], unit)
                if unit_rule is not None and unit_rule.target != unit:
                    item["unit"] = unit_rule.target
                    applied_rule_ids.append(unit_rule.id)

                original_name = str(item.get("name_original", ""))
                original_rule = _lookup(rules[SCOPE_ITEM_NAME_ORIGINAL], original_name)
                if original_rule is not None and original_rule.target != item.get("name_ru"):
                    item["name_ru"] = original_rule.target
                    applied_rule_ids.append(original_rule.id)
                    continue

                name_ru = str(item.get("name_ru", ""))
                name_rule = _lookup(rules[SCOPE_ITEM_NAME_RU], name_ru)
                if name_rule is not None and name_rule.target != name_ru:
                    item["name_ru"] = name_rule.target
                    applied_rule_ids.append(name_rule.id)

        if applied_rule_ids:
            self._record_usage(applied_rule_ids)
        return normalize_receipt_properties(result)

    def learn(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        owner_telegram_user_id: int,
        created_by_telegram_user_id: int | None = None,
    ) -> int:
        if owner_telegram_user_id <= 0:
            return 0
        now = datetime.now().isoformat()
        changed = 0
        changed += self._upsert_mapping(
            SCOPE_MERCHANT,
            str(before.get("merchant", "")),
            normalize_merchant_name(str(after.get("merchant", ""))),
            owner_telegram_user_id=owner_telegram_user_id,
            created_at=now,
            created_by_telegram_user_id=created_by_telegram_user_id,
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
                changed += self._upsert_mapping(
                    SCOPE_UNIT,
                    str(before_item.get("unit", "")),
                    str(after_item.get("unit", "")),
                    owner_telegram_user_id=owner_telegram_user_id,
                    created_at=now,
                    created_by_telegram_user_id=created_by_telegram_user_id,
                )
                before_name_ru = str(before_item.get("name_ru", ""))
                after_name_ru = str(after_item.get("name_ru", ""))
                if before_name_ru.strip() != after_name_ru.strip():
                    changed += self._upsert_mapping(
                        SCOPE_ITEM_NAME_RU,
                        before_name_ru,
                        after_name_ru,
                        owner_telegram_user_id=owner_telegram_user_id,
                        created_at=now,
                        created_by_telegram_user_id=created_by_telegram_user_id,
                    )
                    changed += self._upsert_mapping(
                        SCOPE_ITEM_NAME_ORIGINAL,
                        str(before_item.get("name_original", "")),
                        after_name_ru,
                        owner_telegram_user_id=owner_telegram_user_id,
                        created_at=now,
                        created_by_telegram_user_id=created_by_telegram_user_id,
                    )
        return changed

    def _load_rules(self, owner_telegram_user_id: int | None) -> dict[str, list[CorrectionRule]]:
        rules: dict[str, list[CorrectionRule]] = {scope: [] for scope in LEGACY_SCOPE_KEYS}
        if owner_telegram_user_id is None or owner_telegram_user_id <= 0:
            return rules
        with connect_database(self.settings) as connection:
            rows = connection.execute(
                """
                select id, scope, source, target
                from correction_rules
                where owner_telegram_user_id = ?
                  and language = '' and document_type = '' and merchant = ''
                order by id
                """,
                (owner_telegram_user_id,),
            ).fetchall()
        for row in rows:
            scope = str(row["scope"])
            if scope not in rules:
                continue
            rules[scope].append(
                CorrectionRule(
                    id=int(row["id"]),
                    scope=scope,
                    source=str(row["source"]),
                    target=str(row["target"]),
                )
            )
        return rules

    def _upsert_mapping(
        self,
        scope: str,
        source: str,
        target: str,
        *,
        owner_telegram_user_id: int,
        created_at: str,
        created_by_telegram_user_id: int | None = None,
    ) -> int:
        source = source.strip()
        target = target.strip()
        if not source or not target or source == target:
            return 0
        with connect_database(self.settings) as connection:
            row = connection.execute(
                """
                select id, target
                from correction_rules
                where owner_telegram_user_id = ?
                  and scope = ? and source = ? and language = '' and document_type = '' and merchant = ''
                """,
                (owner_telegram_user_id, scope, source),
            ).fetchone()
            if row is not None:
                if str(row["target"]) == target:
                    return 0
                connection.execute(
                    """
                    update correction_rules
                    set target = ?,
                        updated_at = ?,
                        created_by_telegram_user_id = coalesce(created_by_telegram_user_id, ?)
                    where id = ?
                    """,
                    (target, created_at, created_by_telegram_user_id, int(row["id"])),
                )
                return 1
            connection.execute(
                """
                insert into correction_rules(
                    scope,
                    source,
                    target,
                    owner_telegram_user_id,
                    language,
                    document_type,
                    merchant,
                    usage_count,
                    created_at,
                    updated_at,
                    created_by_telegram_user_id
                )
                values (?, ?, ?, ?, '', '', '', 0, ?, ?, ?)
                """,
                (
                    scope,
                    source,
                    target,
                    owner_telegram_user_id,
                    created_at,
                    created_at,
                    created_by_telegram_user_id,
                ),
            )
            return 1

    def _record_usage(self, rule_ids: list[int]) -> None:
        now = datetime.now().isoformat()
        counts: dict[int, int] = {}
        for rule_id in rule_ids:
            counts[rule_id] = counts.get(rule_id, 0) + 1
        with connect_database(self.settings) as connection:
            for rule_id, count in counts.items():
                connection.execute(
                    """
                    update correction_rules
                    set usage_count = usage_count + ?, last_used_at = ?
                    where id = ?
                    """,
                    (count, now, rule_id),
                )

def _lookup(rules: list[CorrectionRule], value: str) -> CorrectionRule | None:
    for rule in rules:
        if rule.source == value:
            return rule
    lowered = value.lower()
    for rule in rules:
        if rule.source.lower() == lowered:
            return rule
    return None
