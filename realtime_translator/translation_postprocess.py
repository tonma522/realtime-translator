"""Deterministic post-processing for translated engineering text."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .unit_tables import (
    ABRASIVE_TABLE,
    LB_PER_KG,
    LBF_FT_PER_NM,
    MESH_TABLE,
    MICRON_TABLE,
    MM_PER_INCH,
    PSI_PER_BAR,
    PSI_PER_MPA,
)

_NUMBER_RE = r"[+-]?\d+(?:\.\d+)?"
_SIGNED_NUMBER_RE = r"[±+-]?\d+(?:\.\d+)?"
_RANGE_NUMBER_RE = r"[+-]?\d+(?:\.\d+)?\s*-\s*[+-]?\d+(?:\.\d+)?"

_RA_PATTERN = re.compile(r"(?<!\w)(Ra)\s*(%s)(?!\s*(?:um|μm|micron)\b)" % _SIGNED_NUMBER_RE)
_HASH_PATTERN = re.compile(r"(?<!\w)(#\d+)(?!\s*line\b)")
_FEPA_PATTERN = re.compile(r"(?<!\w)(P\d+)\b")
_MESH_PATTERN = re.compile(r"(?<!\w)(%s)\s*(mesh)\b" % _NUMBER_RE, re.IGNORECASE)
_MICRON_PATTERN = re.compile(r"(?<!\w)(%s)\s*(um|μm|micron|microns)\b" % _SIGNED_NUMBER_RE, re.IGNORECASE)
_UNIT_PATTERN = re.compile(
    r"(?<![\w#])(?P<number>%s|%s)\s*(?P<unit>mm|cm|m|in|ft|g|kg|lb|C|F|Nm|lbf·ft|lbf-ft|MPa|bar|psi)\b"
    % (_RANGE_NUMBER_RE, _SIGNED_NUMBER_RE),
    re.IGNORECASE,
)

_ONES = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}
_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}
_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}
_UNIT_READING = {
    "mm": ("millimeter", "millimeters"),
    "cm": ("centimeter", "centimeters"),
    "m": ("meter", "meters"),
    "in": ("inch", "inches"),
    "ft": ("foot", "feet"),
    "g": ("gram", "grams"),
    "kg": ("kilogram", "kilograms"),
    "lb": ("pound", "pounds"),
    "c": ("degree celsius", "degrees celsius"),
    "f": ("degree fahrenheit", "degrees fahrenheit"),
    "nm": ("newton meter", "newton meters"),
    "lbf·ft": ("pound-foot", "pound-feet"),
    "psi": ("psi", "psi"),
    "mpa": ("megapascal", "megapascals"),
    "bar": ("bar", "bar"),
}


def annotate_translation(text: str, *, output_language: str) -> str:
    try:
        return _annotate_text(text, output_language=output_language)
    except Exception:
        return text


def _annotate_text(text: str, *, output_language: str) -> str:
    language = (output_language or "ja").lower()
    normalized = _normalize_text(text)
    normalized = _RA_PATTERN.sub(lambda m: _annotate_ra(m, language), normalized)
    normalized = _HASH_PATTERN.sub(lambda m: _annotate_hash(m, language), normalized)
    normalized = _FEPA_PATTERN.sub(lambda m: _annotate_fepa(m, language), normalized)
    normalized = _MESH_PATTERN.sub(lambda m: _annotate_mesh(m, language), normalized)
    normalized = _MICRON_PATTERN.sub(lambda m: _annotate_micron(m, language), normalized)
    normalized = _UNIT_PATTERN.sub(lambda m: _annotate_unit(m, language), normalized)
    return normalized


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("−", "-").replace("–", "-").replace("—", "-")
    normalized = normalized.replace("µ", "u")
    return re.sub(r"(?<=\d),(?=\d)", ".", normalized)


def _annotate_ra(match: re.Match[str], language: str) -> str:
    token = match.group(0)
    value_text = match.group(2)
    note = f"Ra {value_text} um"
    if language == "en":
        note = f"{note}, R-A {_number_to_words(value_text)}"
    return f"{token} ({note})"


def _annotate_hash(match: re.Match[str], language: str) -> str:
    token = match.group(1)
    info = ABRASIVE_TABLE.get(token)
    if not info:
        return token
    base = f"about {info['micron']} microns / FEPA {info['fepa']}" if language == "en" else f"約 {info['micron']} um / FEPA {info['fepa']}"
    if language == "en":
        base = f"{base}, number {_number_to_words(token[1:])}"
    return f"{token} ({base})"


def _annotate_fepa(match: re.Match[str], language: str) -> str:
    token = match.group(1).upper()
    info = ABRASIVE_TABLE.get(token)
    if not info:
        return token
    base = f"about {info['micron']} microns / JIS {info['jis']}" if language == "en" else f"約 {info['micron']} um / JIS {info['jis']}"
    if language == "en":
        base = f"{base}, {_number_to_words(token[1:])} grit"
    return f"{token} ({base})"


def _annotate_mesh(match: re.Match[str], language: str) -> str:
    number_text = match.group(1)
    token = f"{number_text} mesh"
    info = MESH_TABLE.get(number_text)
    if not info:
        return token
    base = f"{info['micron']} micron"
    if language == "en":
        base = f"{base}, {_number_to_words(number_text)} mesh"
    return f"{token} ({base})"


def _annotate_micron(match: re.Match[str], language: str) -> str:
    number_text = match.group(1)
    unit_text = match.group(2)
    token = f"{number_text} {unit_text}"
    try:
        micron_value = int(Decimal(number_text))
    except InvalidOperation:
        return token
    info = MICRON_TABLE.get(micron_value)
    if not info:
        return token
    parts: list[str] = []
    if "jis" in info:
        parts.append(info["jis"])
    if "fepa" in info:
        parts.append(f"FEPA {info['fepa']}")
    if "mesh" in info:
        parts.append(info["mesh"])
    base = " / ".join(parts)
    if language == "en":
        base = f"{base}, {_number_to_words(number_text)} microns"
    return f"{token} ({base})"


def _annotate_unit(match: re.Match[str], language: str) -> str:
    number_text = match.group("number")
    unit_text = match.group("unit")
    token = f"{number_text} {unit_text}"
    normalized_unit = _normalize_unit(unit_text)
    try:
        annotation = _build_unit_annotation(number_text, normalized_unit, language)
    except InvalidOperation:
        return token
    return f"{token} ({annotation})" if annotation else token


def _normalize_unit(unit_text: str) -> str:
    lowered = unit_text.lower()
    if lowered == "mpa":
        return "MPa"
    if lowered == "nm":
        return "Nm"
    if lowered in {"lbf-ft", "lbf·ft"}:
        return "lbf·ft"
    if lowered == "c":
        return "C"
    if lowered == "f":
        return "F"
    return unit_text


def _build_unit_annotation(number_text: str, unit_text: str, language: str) -> str | None:
    if "-" in number_text and not number_text.startswith("-"):
        start_text, end_text = [part.strip() for part in number_text.split("-", 1)]
        start_note = _convert_single_value(start_text, unit_text)
        end_note = _convert_single_value(end_text, unit_text)
        if not start_note or not end_note:
            return None
        converted = f"{start_note['value']} {start_note['unit']}-{end_note['value']} {end_note['unit']}"
        reading = None
    else:
        single_note = _convert_single_value(number_text, unit_text)
        if not single_note:
            return None
        converted = single_note["rendered"]
        reading = _render_unit_reading(number_text, unit_text) if language == "en" else None
    if reading:
        return f"{converted}, {reading}"
    return converted


def _convert_single_value(number_text: str, unit_text: str) -> dict[str, str] | None:
    sign, number_value, converted_input = _parse_number_for_conversion(number_text)
    converted_value: Decimal
    converted_unit: str
    if unit_text == "mm":
        converted_value = converted_input / MM_PER_INCH
        converted_unit = "in"
        rendered_value = _format_value(converted_value, 2, tolerance=sign == "±")
    elif unit_text == "cm":
        converted_value = converted_input / Decimal("2.54")
        converted_unit = "in"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "m":
        converted_value = converted_input * Decimal("3.28084")
        converted_unit = "ft"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "in":
        converted_value = converted_input * MM_PER_INCH
        converted_unit = "mm"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "ft":
        converted_value = converted_input / Decimal("3.28084")
        converted_unit = "m"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "g":
        converted_value = converted_input / Decimal("453.59237")
        converted_unit = "lb"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "kg":
        converted_value = converted_input * LB_PER_KG
        converted_unit = "lb"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "lb":
        converted_value = converted_input / LB_PER_KG
        converted_unit = "kg"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "C":
        converted_value = (converted_input * Decimal("9") / Decimal("5")) + Decimal("32")
        converted_unit = "F"
        rendered_value = _format_value(converted_value, 1, keep_trailing=True)
    elif unit_text == "F":
        converted_value = (converted_input - Decimal("32")) * Decimal("5") / Decimal("9")
        converted_unit = "C"
        rendered_value = _format_value(converted_value, 1, keep_trailing=True)
    elif unit_text == "Nm":
        converted_value = converted_input * LBF_FT_PER_NM
        converted_unit = "lbf·ft"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "lbf·ft":
        converted_value = converted_input / LBF_FT_PER_NM
        converted_unit = "Nm"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "psi":
        mpa_value = _format_value(converted_input / PSI_PER_MPA, 2)
        bar_value = _format_value(converted_input / PSI_PER_BAR, 2)
        return {
            "value": f"{sign}{mpa_value} MPa / {sign}{bar_value} bar",
            "unit": "",
            "rendered": f"{sign}{mpa_value} MPa / {sign}{bar_value} bar",
        }
    elif unit_text == "MPa":
        converted_value = converted_input * PSI_PER_MPA
        converted_unit = "psi"
        rendered_value = _format_value(converted_value, 2)
    elif unit_text == "bar":
        converted_value = converted_input * PSI_PER_BAR
        converted_unit = "psi"
        rendered_value = _format_value(converted_value, 2)
    else:
        return None
    rendered = f"{sign}{rendered_value} {converted_unit}"
    return {"value": f"{sign}{rendered_value}", "unit": converted_unit, "rendered": rendered}


def _split_sign(number_text: str) -> tuple[str, Decimal]:
    if number_text.startswith("±"):
        return "±", Decimal(number_text[1:])
    if number_text.startswith("+"):
        return "+", Decimal(number_text[1:])
    if number_text.startswith("-"):
        return "-", Decimal(number_text[1:])
    return "", Decimal(number_text)


def _format_value(
    value: Decimal,
    places: int,
    *,
    tolerance: bool = False,
    keep_trailing: bool = False,
) -> str:
    quantized = value.quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    text = format(quantized, f".{places}f") if keep_trailing else _trim_decimal(quantized)
    if tolerance and quantized == 0:
        for extra_places in range(places + 1, 7):
            quantized = value.quantize(Decimal("1").scaleb(-extra_places), rounding=ROUND_HALF_UP)
            if quantized != 0:
                text = _trim_decimal(quantized)
                break
    return text


def _trim_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_number_for_conversion(number_text: str) -> tuple[str, Decimal, Decimal]:
    if number_text.startswith("±"):
        magnitude = Decimal(number_text[1:])
        return "±", magnitude, magnitude
    if number_text.startswith("+"):
        value = Decimal(number_text[1:])
        return "+", value, value
    value = Decimal(number_text)
    return "", abs(value), value


def _render_unit_reading(number_text: str, unit_text: str) -> str:
    unit_key = unit_text.lower()
    singular, plural = _UNIT_READING[unit_key]
    sign, number_value = _split_sign(number_text)
    phrase = _number_to_words(f"{sign}{_trim_decimal(number_value)}")
    unit_name = singular if number_value == 1 else plural
    return f"{phrase} {unit_name}"


def _number_to_words(value_text: str) -> str:
    if value_text.startswith("±"):
        return f"plus or minus {_number_to_words(value_text[1:])}"
    if value_text.startswith("+"):
        return f"plus {_number_to_words(value_text[1:])}"
    if value_text.startswith("-"):
        return f"minus {_number_to_words(value_text[1:])}"
    if "." in value_text:
        integer_part, decimal_part = value_text.split(".", 1)
        integer_words = _integer_to_words(int(integer_part or "0"))
        decimal_words = " ".join(_DIGIT_WORDS[digit] for digit in decimal_part)
        return f"{integer_words} point {decimal_words}"
    return _integer_to_words(int(value_text))


def _integer_to_words(value: int) -> str:
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens = value // 10 * 10
        remainder = value % 10
        return _TENS[tens] if remainder == 0 else f"{_TENS[tens]}-{_ONES[remainder]}"
    if value < 1000:
        hundreds = value // 100
        remainder = value % 100
        prefix = f"{_ONES[hundreds]} hundred"
        return prefix if remainder == 0 else f"{prefix} {_integer_to_words(remainder)}"
    if value < 1_000_000:
        thousands = value // 1000
        remainder = value % 1000
        prefix = f"{_integer_to_words(thousands)} thousand"
        return prefix if remainder == 0 else f"{prefix} {_integer_to_words(remainder)}"
    return str(value)
