"""Strict, small parser for user-entered meal items.

Grams are accepted for every food. Count-based input is deliberately limited
to foods with an explicit conversion here, so the bot never invents a portion.
"""

from __future__ import annotations

import re

from services.food_vision import DetectedFood


class MealInputError(ValueError):
    pass


COUNT_PORTIONS_G = {
    "telur rebus": 50,
}

GRAM_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+(?P<grams>\d+(?:[.,]\d+)?)\s*(?:g|gr|gram|grams)$",
    re.IGNORECASE,
)
COUNT_PATTERN = re.compile(r"^(?P<count>\d+(?:[.,]\d+)?)\s+(?P<name>.+)$")


def _clean_line(line: str) -> str:
    return re.sub(r"^[\s•*\-]+", "", line).strip()


def _food(name: str, grams: float, serving_label: str = "") -> DetectedFood:
    if not name.strip():
        raise MealInputError("Nama makanan belum diisi.")
    if not 1 <= grams <= 2000:
        raise MealInputError("Porsi harus berada antara 1 dan 2.000 gram.")
    return DetectedFood(
        name=name.strip(),
        estimated_grams=int(round(grams)),
        identification_confidence=1.0,
        portion_confidence=1.0,
        serving_label=serving_label,
    )


def parse_meal_text(text: str) -> list[DetectedFood]:
    """Parse one or more foods separated by a newline or semicolon."""
    lines = [_clean_line(part) for part in re.split(r"[;\n]+", text) if _clean_line(part)]
    if not lines:
        raise MealInputError("Masukkan nama makanan dan porsinya.")
    if len(lines) > 10:
        raise MealInputError("Maksimal 10 makanan dalam satu catatan.")

    foods: list[DetectedFood] = []
    for line in lines:
        grams_match = GRAM_PATTERN.fullmatch(line)
        if grams_match:
            grams = float(grams_match.group("grams").replace(",", "."))
            foods.append(_food(grams_match.group("name"), grams))
            continue

        count_match = COUNT_PATTERN.fullmatch(line)
        if count_match:
            name = count_match.group("name").strip()
            normalized = " ".join(name.lower().split())
            grams_each = COUNT_PORTIONS_G.get(normalized)
            if grams_each is not None:
                count = float(count_match.group("count").replace(",", "."))
                if count <= 0 or not count.is_integer():
                    raise MealInputError(f'Jumlah untuk "{name}" harus berupa bilangan bulat.')
                foods.append(
                    _food(name, count * grams_each, f"{int(count)} butir × {grams_each} g")
                )
                continue

        raise MealInputError(
            f'Format "{line}" belum jelas. Gunakan contoh: nasi putih 150 gram.'
        )

    return foods
