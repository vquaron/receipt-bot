import json
from pathlib import Path

from app.config import Settings
from app.db.connection import connect_database
from app.llm.openai_parser import ParsedReceipt
from app.pipeline import receipt_pipeline
from app.storage.corrections import CorrectionStore


def test_scoped_corrections_apply_only_fields(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    store = CorrectionStore(app_settings)
    before = _receipt()
    after = {
        **before,
        "merchant": "Zovq Supermarket",
        "items": [{**before["items"][0], "name_ru": "Пакет большой", "unit": "шт"}],
    }

    assert store.learn(before, after, created_by_telegram_user_id=777) == 4
    applied = store.apply(before)

    assert applied["merchant"] == "Zovq Supermarket"
    assert applied["items"][0]["name_ru"] == "Пакет большой"
    assert applied["items"][0]["unit"] == "шт"
    assert applied["amount"] == "20"
    with connect_database(app_settings) as connection:
        rows = connection.execute(
            """
            select scope, source, target, usage_count, last_used_at, created_by_telegram_user_id
            from correction_rules
            order by scope, source
            """
        ).fetchall()
    assert len(rows) == 4
    assert {row["scope"] for row in rows} == {"merchant", "unit", "item_name_ru", "item_name_original"}
    assert {int(row["created_by_telegram_user_id"]) for row in rows} == {777}
    assert sum(int(row["usage_count"]) for row in rows) == 3
    assert all(row["last_used_at"] for row in rows if int(row["usage_count"]))


def test_exact_lookup_has_priority_over_case_insensitive(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    CorrectionStore(app_settings)
    with connect_database(app_settings) as connection:
        connection.execute(
            """
            insert into correction_rules(scope, source, target, language, document_type, merchant, created_at, updated_at)
            values (?, ?, ?, '', '', '', ?, ?)
            """,
            ("unit", "WT", "case exact", "now", "now"),
        )
        connection.execute(
            """
            insert into correction_rules(scope, source, target, language, document_type, merchant, created_at, updated_at)
            values (?, ?, ?, '', '', '', ?, ?)
            """,
            ("unit", "wt", "case insensitive", "now", "now"),
        )

    applied = CorrectionStore(app_settings).apply(_receipt())

    assert applied["items"][0]["unit"] == "case exact"


def test_learning_updates_changed_target_without_duplicate(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    store = CorrectionStore(app_settings)
    before = _receipt()
    first = {**before, "merchant": "First"}
    second = {**before, "merchant": "Second"}

    assert store.learn(before, first) == 1
    assert store.learn(before, first) == 0
    assert store.learn(before, second) == 1

    with connect_database(app_settings) as connection:
        rows = connection.execute(
            "select source, target from correction_rules where scope = 'merchant'"
        ).fetchall()
    assert [(row["source"], row["target"]) for row in rows] == [("Զովք", "Second")]


def test_legacy_json_imports_once_when_database_is_empty(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    app_settings.data_dir.mkdir(parents=True)
    legacy_path = app_settings.data_dir / "corrections.json"
    legacy_path.write_text(
        json.dumps(
            {
                "merchants": {"Զովք": "Zovq Supermarket"},
                "units": {"WT": "шт"},
                "item_names_ru": {"Պ / տ մեծ": "Пакет большой"},
                "item_names_by_original": {"մեծ": "Пакет по оригиналу"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = CorrectionStore(app_settings)
    assert store.apply(_receipt())["merchant"] == "Zovq Supermarket"
    legacy_path.write_text(json.dumps({"merchants": {"Զովք": "Changed"}}, ensure_ascii=False), encoding="utf-8")
    store = CorrectionStore(app_settings)

    assert store.apply(_receipt())["merchant"] == "Zovq Supermarket"
    with connect_database(app_settings) as connection:
        count = connection.execute("select count(*) as count from correction_rules").fetchone()["count"]
    assert count == 4


def test_invalid_legacy_json_does_not_break_startup(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    app_settings.data_dir.mkdir(parents=True)
    (app_settings.data_dir / "corrections.json").write_text("{bad", encoding="utf-8")

    store = CorrectionStore(app_settings)

    with connect_database(app_settings) as connection:
        count = connection.execute("select count(*) as count from correction_rules").fetchone()["count"]
    assert count == 0
    assert store.apply({**_receipt(), "merchant": "Արարատ"})["merchant"] == "Արարատ"


def test_learning_does_not_write_legacy_json(tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    store = CorrectionStore(app_settings)

    assert store.learn(_receipt(), {**_receipt(), "merchant": "Zovq"}) == 1

    assert not (app_settings.data_dir / "corrections.json").exists()


def test_parse_for_review_applies_db_backed_corrections(monkeypatch, tmp_path: Path) -> None:
    app_settings = _settings(tmp_path)
    store = CorrectionStore(app_settings)
    store.learn(_receipt(), {**_receipt(), "merchant": "Corrected"})

    def fake_parse_receipt_text(ocr_text: str, *, api_key: str, model: str, document_type: str) -> ParsedReceipt:
        return ParsedReceipt(data=_receipt(), raw_response="{}")

    monkeypatch.setattr(receipt_pipeline, "parse_receipt_text", fake_parse_receipt_text)

    parsed = receipt_pipeline.parse_for_review("ocr", settings=app_settings, correction_store=store)

    assert parsed.data["merchant"] == "Corrected"


def _receipt() -> dict[str, object]:
    return {
        "merchant": "Զովք",
        "date": "2026-04-07",
        "time": "20:41",
        "amount": "20",
        "category": "grocery",
        "items": [
            {
                "name_original": "մեծ",
                "name_ru": "Պ / տ մեծ",
                "name_en": "Large",
                "unit_price": "20",
                "quantity": "1",
                "unit": "WT",
                "line_total": "20",
            }
        ],
    }


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        telegram_bot_token="telegram",
        openai_api_key="openai",
        obsidian_vault=tmp_path,
        data_dir=data_dir,
        admin_telegram_user_ids=frozenset(),
        allowed_telegram_user_ids=frozenset(),
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        app_storage_dir=data_dir / "storage",
        tmp_storage_dir=data_dir / "tmp",
        export_storage_dir=data_dir / "exports",
        debug_storage_dir=data_dir / "debug",
    )
