"""Small, deterministic warning rules for a user's daily recap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from services.daily_recap import DailyRecap
from services.daily_targets import DailyTargets


MAX_WARNINGS = 3
LATE_DAY_HOUR = 18


@dataclass(frozen=True)
class NutritionWarning:
    code: str
    message: str
    priority: int


def build_nutrition_warnings(
    recap: DailyRecap,
    target: DailyTargets,
    now_local: datetime,
    limit: int = MAX_WARNINGS,
) -> list[NutritionWarning]:
    """Return at most ``limit`` rule-based warnings in priority order.

    High/near-limit warnings remain valid with partial data because the known
    amount alone reached the threshold. Low-intake warnings require complete
    coverage, so missing nutrients are never silently treated as zero.
    """
    if limit <= 0:
        return []

    warnings: list[NutritionWarning] = []

    if recap.sodium is not None:
        ratio = recap.sodium / target.sodium_max_mg
        if ratio >= 1:
            warnings.append(NutritionWarning("sodium_high", "Sodium hari ini sudah melewati batas harian.", 100))
        elif ratio >= 0.8:
            warnings.append(NutritionWarning("sodium_near", "Sodium hari ini sudah mendekati batas harian.", 80))

    if recap.added_sugar is not None:
        ratio = recap.added_sugar / target.added_sugar_max_g
        if ratio >= 1:
            warnings.append(NutritionWarning("sugar_high", "Gula tambahan hari ini sudah melewati batas harian.", 95))
        elif ratio >= 0.8:
            warnings.append(NutritionWarning("sugar_near", "Gula tambahan hari ini sudah mendekati batas harian.", 75))

    if recap.calories is not None and recap.calories > target.calories_kcal:
        warnings.append(NutritionWarning("calories_high", "Kalori hari ini sudah melewati target.", 90))

    if recap.fat is not None and recap.fat > target.fat_g:
        warnings.append(NutritionWarning("fat_high", "Lemak hari ini sudah melewati target.", 70))

    late_day = now_local.hour >= LATE_DAY_HOUR
    complete_macros = recap.total_items > 0 and recap.known_nutrition_items == recap.total_items
    if late_day and complete_macros:
        if recap.calories is not None and recap.calories < target.calories_kcal * 0.7:
            warnings.append(NutritionWarning("calories_low", "Kalori hari ini masih jauh di bawah target.", 65))
        if recap.protein is not None and recap.protein < target.protein_g * 0.8:
            warnings.append(NutritionWarning("protein_low", "Protein hari ini masih rendah.", 60))

    complete_fiber = recap.total_items > 0 and recap.fiber_known_items == recap.total_items
    if late_day and complete_fiber and recap.fiber is not None and recap.fiber < target.fiber_g * 0.7:
        warnings.append(NutritionWarning("fiber_low", "Serat hari ini masih rendah.", 55))

    warnings.sort(key=lambda warning: warning.priority, reverse=True)
    return warnings[:limit]
