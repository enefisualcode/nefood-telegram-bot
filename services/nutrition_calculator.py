"""Deterministic nutrition calculation.

Values are scaled from the curated per-100 g database records. Nothing here
calls a model or a network service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from services.food_matcher import FoodMatcher, FoodRecord, get_matcher
from services.usda_food_data import UsdaClient, UsdaFood, get_client
from services.food_vision import DetectedFood


def _round_half_up(value: float, digits: int = 0) -> float:
    """Round half away from zero.

    Python's built-in round() uses banker's rounding, which would turn
    418.5 kcal into 418 - not what a nutrition readout should show.
    """
    quantum = Decimal(1).scaleb(-digits)
    result = Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return int(result) if digits == 0 else float(result)


@dataclass(frozen=True)
class Nutrition:
    calories: float = 0.0
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0

    def __add__(self, other: "Nutrition") -> "Nutrition":
        return Nutrition(
            calories=self.calories + other.calories,
            protein=self.protein + other.protein,
            carbs=self.carbs + other.carbs,
            fat=self.fat + other.fat,
        )

    def rounded(self) -> "Nutrition":
        """kcal to the nearest integer, macros to one decimal place."""
        return Nutrition(
            calories=_round_half_up(self.calories),
            protein=_round_half_up(self.protein, 1),
            carbs=_round_half_up(self.carbs, 1),
            fat=_round_half_up(self.fat, 1),
        )


# How a detected food ended up being treated.
COUNTED = "counted"  # resolved to trusted data and contributes to the total
UNVERIFIED = "unverified"  # only a provisional local record, and no USDA data
UNMATCHED = "unmatched"  # no usable data anywhere

# Where a counted food's numbers came from.
SOURCE_LOCAL = "local"
SOURCE_USDA = "usda"


@dataclass(frozen=True)
class FoodNutrition:
    """One detected food and how it was resolved against the database."""

    name: str
    grams: int
    status: str = UNMATCHED
    nutrition: Nutrition | None = None
    record: FoodRecord | None = None
    source: str = ""
    usda: UsdaFood | None = None

    @property
    def counted(self) -> bool:
        """True only when this food contributes to the meal total."""
        return self.status == COUNTED


@dataclass(frozen=True)
class MealNutrition:
    items: list[FoodNutrition] = field(default_factory=list)
    total: Nutrition = Nutrition()

    @property
    def counted_items(self) -> list[FoodNutrition]:
        return [item for item in self.items if item.status == COUNTED]

    @property
    def unverified_items(self) -> list[FoodNutrition]:
        return [item for item in self.items if item.status == UNVERIFIED]

    @property
    def unmatched_items(self) -> list[FoodNutrition]:
        return [item for item in self.items if item.status == UNMATCHED]


def scale(source, grams: float) -> Nutrition:
    """Scale per-100 g values to the estimated portion.

    Accepts anything exposing the per-100 g fields - a local FoodRecord or a
    USDA record - since the arithmetic is identical.
    """
    factor = grams / 100.0
    return Nutrition(
        calories=source.calories_per_100g * factor,
        protein=source.protein_per_100g * factor,
        carbs=source.carbs_per_100g * factor,
        fat=source.fat_per_100g * factor,
    )


async def resolve_food(
    food: DetectedFood,
    matcher: FoodMatcher,
    usda: UsdaClient | None,
) -> FoodNutrition:
    """Resolve one detected food against the source priority.

    1. verified local record
    2. USDA FoodData Central
    3. unavailable
    """
    record = matcher.match(food.name)

    if record is not None and record.is_verified:
        return FoodNutrition(
            name=food.name,
            grams=food.estimated_grams,
            status=COUNTED,
            nutrition=scale(record, food.estimated_grams),
            record=record,
            source=SOURCE_LOCAL,
        )

    # A provisional local record must not block the USDA fallback.
    usda_food = await usda.lookup(food.name) if usda is not None else None

    if usda_food is not None:
        return FoodNutrition(
            name=food.name,
            grams=food.estimated_grams,
            status=COUNTED,
            nutrition=scale(usda_food, food.estimated_grams),
            record=record,
            source=SOURCE_USDA,
            usda=usda_food,
        )

    # Nothing trustworthy. Distinguish "we have an untrusted record" from
    # "we have nothing at all" so the user gets an accurate explanation.
    status = UNVERIFIED if record is not None else UNMATCHED
    return FoodNutrition(
        name=food.name, grams=food.estimated_grams, status=status, record=record
    )


async def calculate_meal(
    foods: list[DetectedFood],
    matcher: FoodMatcher | None = None,
    usda: UsdaClient | None = None,
) -> MealNutrition:
    """Resolve every detected food and total up the trusted ones."""
    matcher = matcher or get_matcher()
    if usda is None:
        usda = get_client()

    items: list[FoodNutrition] = []
    total = Nutrition()

    for food in foods:
        item = await resolve_food(food, matcher, usda)
        items.append(item)
        if item.status == COUNTED:
            total = total + item.nutrition

    return MealNutrition(items=items, total=total)
