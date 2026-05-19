from pathlib import Path

from app.storage.corrections import CorrectionStore


def test_scoped_corrections_apply_only_fields(tmp_path: Path) -> None:
    store = CorrectionStore(tmp_path)
    before = {
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
    after = {
        **before,
        "merchant": "Zovq Supermarket",
        "items": [{**before["items"][0], "name_ru": "Пакет большой", "unit": "шт"}],
    }
    assert store.learn(before, after) == 4
    applied = store.apply(before)
    assert applied["merchant"] == "Zovq Supermarket"
    assert applied["items"][0]["name_ru"] == "Пакет большой"
    assert applied["items"][0]["unit"] == "шт"
    assert applied["amount"] == "20"
