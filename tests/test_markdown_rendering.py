from pathlib import Path

from app.obsidian.writer import render_markdown


def test_markdown_frontmatter_has_only_receipt_properties() -> None:
    markdown = render_markdown(
        parsed={
            "date": "2026-04-07",
            "time": "20:41:00",
            "merchant": "Զովք",
            "amount": "4 465,75 AMD",
            "category": "grocery",
            "summary_ru": "Покупка",
            "items": [],
        },
        note_date="2026-04-07",
        attachment_rel=Path("Attachments/receipts/2026/04/a.jpg"),
        clean_rel=Path("OCR/2026/04/a.clean.hy.txt"),
        source_rel=Path("OCR_VERIFIED/2026/04/a.verified.hy.txt"),
        possible_errors=[],
    )
    frontmatter = markdown.split("---")[1].strip().splitlines()
    assert frontmatter == [
        'date: "2026-04-07"',
        'time: "20:41:00"',
        'merchant: "Zovq Supermarket"',
        'amount: "4465.75"',
        'category: "Grocery"',
    ]


def test_markdown_renders_items_table() -> None:
    markdown = render_markdown(
        parsed={
            "date": "2026-04-07",
            "time": "20:41:00",
            "merchant": "Zovq Supermarket",
            "amount": "20",
            "category": "Grocery",
            "summary_ru": "",
            "items": [
                {
                    "name_original": "մեծ",
                    "name_ru": "Пакет большой",
                    "name_en": "Large bag",
                    "unit_price": "20",
                    "quantity": "1",
                    "unit": "WT",
                    "line_total": "20",
                }
            ],
        },
        note_date="2026-04-07",
        attachment_rel=Path("Attachments/receipts/2026/04/a.jpg"),
        clean_rel=Path("OCR/2026/04/a.clean.hy.txt"),
        source_rel=Path("OCR_VERIFIED/2026/04/a.verified.hy.txt"),
        possible_errors=[],
    )
    assert "| 1 | Пакет большой | 20 | 1 | шт | 20 |" in markdown
    assert "| 1 | Large bag | 20 | 1 | pcs | 20 |" in markdown
