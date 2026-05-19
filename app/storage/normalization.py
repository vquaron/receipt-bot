from __future__ import annotations

import re
import unicodedata


SAFE_SLUG_RE = re.compile(r"[^a-z0-9_.]+")
ZOVQ_MARKERS = ("զովք", "zovq", "zovk", "зовк")
CATEGORY_ALIASES = {
    "grocery": "Grocery",
    "groceries": "Grocery",
    "supermarket": "Grocery",
    "food": "Grocery",
    "продукты": "Grocery",
    "супермаркет": "Grocery",
    "շուկա": "Grocery",
    "սուպերմարկետ": "Grocery",
}

CYRILLIC_TRANSLIT = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ы": "y",
        "э": "e",
        "ю": "yu",
        "я": "ya",
        "ь": "",
        "ъ": "",
    }
)

ARMENIAN_TRANSLIT = str.maketrans(
    {
        "ա": "a",
        "բ": "b",
        "գ": "g",
        "դ": "d",
        "ե": "e",
        "զ": "z",
        "է": "e",
        "ը": "y",
        "թ": "t",
        "ժ": "zh",
        "ի": "i",
        "լ": "l",
        "խ": "kh",
        "ծ": "ts",
        "կ": "k",
        "հ": "h",
        "ձ": "dz",
        "ղ": "gh",
        "ճ": "ch",
        "մ": "m",
        "յ": "y",
        "ն": "n",
        "շ": "sh",
        "ո": "o",
        "չ": "ch",
        "պ": "p",
        "ջ": "j",
        "ռ": "r",
        "ս": "s",
        "վ": "v",
        "տ": "t",
        "ր": "r",
        "ց": "ts",
        "ւ": "v",
        "փ": "p",
        "ք": "q",
        "օ": "o",
        "ֆ": "f",
    }
)


def clean_ocr_text(raw_text: str) -> str:
    normalized = unicodedata.normalize("NFC", raw_text)
    cleaned_lines = []
    for line in normalized.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if compact:
            cleaned_lines.append(normalize_mixed_receipt_units(compact))
    return "\n".join(cleaned_lines)


def normalize_mixed_receipt_units(text: str) -> str:
    text = re.sub(r"(?<=\d)\s*[Հհ][աա][տտ]\b", " հատ", text)
    text = re.sub(r"\b[Հհ][աա][տտ]\b", "հատ", text)
    text = re.sub(r"(?<=\d)\s*[Կկ][գգ]\b", " կգ", text)
    text = re.sub(r"(?<=\d)\s*[Շշ][Տտ]\b", " шт", text)
    text = re.sub(r"\b[Շշ][Տտ]\b", "шт", text)
    text = re.sub(r"(?<=\d)\s*[Կկ][Տտ]\b", " кг", text)
    return text


def normalize_merchant_name(value: str) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip(" «»\"'")
    lowered = compact.lower()
    if any(marker in lowered for marker in ZOVQ_MARKERS):
        return "Zovq Supermarket"
    if "սուպերմարկետ" in lowered or "супермаркет" in lowered:
        compact = re.sub(
            r"սուպերմարկետ|супермаркет",
            "Supermarket",
            compact,
            flags=re.IGNORECASE,
        )
    return compact or "unknown_merchant"


def normalize_date_value(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    match = re.fullmatch(r"(\d{2})[-./](\d{2})[-./](\d{4})", value)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return value


def normalize_time_value(value: str) -> str:
    value = value.strip()
    match = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", value)
    if not match:
        return value
    hour, minute, second = match.groups()
    if second is None:
        return f"{int(hour):02d}:{minute}"
    return f"{int(hour):02d}:{minute}:{second}"


def normalize_amount(value: str) -> str:
    compact = re.sub(r"[^\d,.-]+", "", value or "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", compact)
    if not match:
        return ""
    amount = match.group(0)
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return amount


def amount_for_filename(value: str) -> str:
    return normalize_amount(value) or "unknown_amount"


def normalize_category(value: str) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if not compact:
        return ""
    alias = CATEGORY_ALIASES.get(compact.lower())
    if alias:
        return alias
    words = re.split(r"([\s/_-]+)", compact)
    return "".join(part.capitalize() if part.isalpha() else part for part in words)


def normalize_receipt_properties(parsed: dict[str, object]) -> dict[str, object]:
    normalized = dict(parsed)
    normalized["date"] = normalize_date_value(str(normalized.get("date", "")))
    normalized["time"] = normalize_time_value(str(normalized.get("time", "")))
    normalized["merchant"] = normalize_merchant_name(str(normalized.get("merchant", "")))
    normalized["amount"] = normalize_amount(str(normalized.get("amount", "")))
    normalized["category"] = normalize_category(str(normalized.get("category", "")))
    normalized["currency"] = "AMD"
    return normalized


def slugify_merchant(value: str) -> str:
    value = normalize_merchant_name(value)
    normalized = unicodedata.normalize("NFKD", value).lower()
    transliterated = normalized.translate(CYRILLIC_TRANSLIT).translate(ARMENIAN_TRANSLIT)
    ascii_value = transliterated.encode("ascii", "ignore").decode("ascii").lower()
    underscored = re.sub(r"\s+", "_", ascii_value.strip())
    safe = SAFE_SLUG_RE.sub("", underscored).strip("_")
    return safe or "unknown_merchant"
