"""Deterministic nutrition calculation.

Values are scaled from the curated per-100 g database records. Nothing here
calls a model or a network service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from services.food_matcher import FoodMatcher, FoodRecord, get_matcher
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
COUNTED = "counted"  # matched a verified record and contributes to the total
UNVERIFIED = "unverified"  # matched, but the record is provisional
UNMATCHED = "unmatched"  # no record at all


@dataclass(frozen=True)
class FoodNutrition:
    """One detected food and how it was resolved against the database."""

    name: str
    grams: int
    status: str = UNMATCHED
    nutrition: Nutrition | None = None
    record: FoodRecord | None = None

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


def scale(record: FoodRecord, grams: float) -> Nutrition:
    """Scale per-100 g values to the estimated portion."""
    factor = grams / 100.0
    return Nutrition(
        calories=record.calories_per_100g * factor,
        protein=record.protein_per_100g * factor,
        carbs=record.carbs_per_100g * factor,
        fat=record.fat_per_100g * factor,
    )


def calculate_meal(
    foods: list[DetectedFood], matcher: FoodMatcher | None = None
) -> MealNutrition:
    """Match every detected food and total up the ones we have data for."""
    matcher = matcher or get_matcher()

    items: list[FoodNutrition] = []
    total = Nutrition()

    for food in foods:
        record = matcher.match(food.name)

        if record is None:
            items.append(
                FoodNutrition(
                    name=food.name, grams=food.estimated_grams, status=UNMATCHED
                )
            )
            continue

        if not record.is_verified:
            # The record exists but its values were never checked against the
            # cited source, so it must not reach the user as nutrition data.
            items.append(
                FoodNutrition(
                    name=food.name,
                    grams=food.estimated_grams,
                    status=UNVERIFIED,
                    record=record,
                )
            )
            continue

        nutrition = scale(record, food.estimated_grams)
        total = total + nutrition
        items.append(
            FoodNutrition(
                name=food.name,
                grams=food.estimated_grams,
                status=COUNTED,
                nutrition=nutrition,
                record=record,
            )
        )

    return MealNutrition(items=items, total=total)
