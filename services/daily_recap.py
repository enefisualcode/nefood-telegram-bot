"""Build one user's daily meal recap in Asia/Jakarta time."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from services.meal_store import MealStore


# Jakarta menggunakan UTC+7 sepanjang tahun dan tidak menerapkan daylight saving.
# Offset tetap juga bekerja di Windows yang tidak selalu membawa basis data IANA.
JAKARTA = timezone(timedelta(hours=7), name="Asia/Jakarta")


@dataclass(frozen=True)
class DailyFoodEntry:
    eaten_at_local: datetime
    name: str
    grams: int
    nutrition_available: bool


@dataclass(frozen=True)
class DailyRecap:
    local_date: date
    entries: list[DailyFoodEntry] = field(default_factory=list)
    calories: float | None = None
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None
    fiber: float | None = None
    added_sugar: float | None = None
    sodium: float | None = None
    known_nutrition_items: int = 0
    fiber_known_items: int = 0
    added_sugar_known_items: int = 0
    sodium_known_items: int = 0

    @property
    def total_items(self) -> int:
        return len(self.entries)

    @property
    def has_partial_nutrition(self) -> bool:
        return 0 < self.known_nutrition_items < self.total_items


def jakarta_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime, date]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    local_date = current.astimezone(JAKARTA).date()
    local_start = datetime.combine(local_date, time.min, tzinfo=JAKARTA)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc), local_date


def build_daily_recap(
    telegram_user_id: int,
    store: MealStore,
    now: datetime | None = None,
) -> DailyRecap:
    start, end, local_date = jakarta_day_bounds(now)
    meals = store.get_for_user_between(telegram_user_id, start, end)

    entries: list[DailyFoodEntry] = []
    totals = {"calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    known = 0
    optional_totals = {"fiber": 0.0, "added_sugar": 0.0, "sodium": 0.0}
    optional_counts = {"fiber": 0, "added_sugar": 0, "sodium": 0}
    for meal in meals:
        eaten_at = datetime.fromisoformat(meal.eaten_at).astimezone(JAKARTA)
        for item in meal.items:
            available = all(
                value is not None
                for value in (item.calories, item.protein, item.carbs, item.fat)
            )
            entries.append(
                DailyFoodEntry(
                    eaten_at_local=eaten_at,
                    name=item.food_name,
                    grams=item.grams,
                    nutrition_available=available,
                )
            )
            if available:
                known += 1
                totals["calories"] += item.calories
                totals["protein"] += item.protein
                totals["carbs"] += item.carbs
                totals["fat"] += item.fat
            for nutrient in optional_totals:
                value = getattr(item, nutrient)
                if value is not None:
                    optional_totals[nutrient] += value
                    optional_counts[nutrient] += 1

    if known == 0:
        return DailyRecap(
            local_date=local_date,
            entries=entries,
            fiber=(optional_totals["fiber"] if optional_counts["fiber"] else None),
            added_sugar=(
                optional_totals["added_sugar"]
                if optional_counts["added_sugar"]
                else None
            ),
            sodium=(optional_totals["sodium"] if optional_counts["sodium"] else None),
            fiber_known_items=optional_counts["fiber"],
            added_sugar_known_items=optional_counts["added_sugar"],
            sodium_known_items=optional_counts["sodium"],
        )
    return DailyRecap(
        local_date=local_date,
        entries=entries,
        calories=totals["calories"],
        protein=totals["protein"],
        carbs=totals["carbs"],
        fat=totals["fat"],
        fiber=(optional_totals["fiber"] if optional_counts["fiber"] else None),
        added_sugar=(
            optional_totals["added_sugar"] if optional_counts["added_sugar"] else None
        ),
        sodium=(optional_totals["sodium"] if optional_counts["sodium"] else None),
        known_nutrition_items=known,
        fiber_known_items=optional_counts["fiber"],
        added_sugar_known_items=optional_counts["added_sugar"],
        sodium_known_items=optional_counts["sodium"],
    )
