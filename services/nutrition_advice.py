"""Choose exactly one deterministic daily nutrition suggestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from services.daily_recap import DailyRecap
from services.daily_targets import DailyTargets
from services.nutrition_warnings import LATE_DAY_HOUR


@dataclass(frozen=True)
class DailyAdvice:
    code: str
    message: str


def choose_daily_advice(
    recap: DailyRecap,
    target: DailyTargets,
    now_local: datetime,
) -> DailyAdvice:
    """Select one suggestion using the product's explicit priority order."""
    if recap.sodium is not None and recap.sodium >= target.sodium_max_mg * 0.8:
        return DailyAdvice(
            "sodium",
            "Untuk makan berikutnya, pilih makanan yang lebih rendah garam dan batasi tambahan saus atau bumbu asin.",
        )

    if (
        recap.added_sugar is not None
        and recap.added_sugar >= target.added_sugar_max_g * 0.8
    ):
        return DailyAdvice(
            "sugar",
            "Untuk konsumsi berikutnya, kurangi makanan atau minuman manis dan pilih pilihan tanpa gula tambahan.",
        )

    if recap.calories is not None and recap.calories > target.calories_kcal:
        return DailyAdvice(
            "calories_high",
            "Untuk makan berikutnya, pilih porsi yang lebih ringan dan tetap utamakan makanan bergizi seimbang.",
        )

    late_day = now_local.hour >= LATE_DAY_HOUR
    complete_macros = recap.total_items > 0 and recap.known_nutrition_items == recap.total_items
    complete_fiber = recap.total_items > 0 and recap.fiber_known_items == recap.total_items

    if (
        late_day
        and complete_fiber
        and recap.fiber is not None
        and recap.fiber < target.fiber_g * 0.7
    ):
        return DailyAdvice(
            "fiber_low",
            "Coba tambahkan satu porsi sayur atau buah pada makan berikutnya.",
        )

    if (
        late_day
        and complete_macros
        and recap.protein is not None
        and recap.protein < target.protein_g * 0.8
    ):
        return DailyAdvice(
            "protein_low",
            "Tambahkan satu sumber protein pada makan berikutnya, dengan porsi yang sesuai kebutuhan Anda.",
        )

    if recap.fat is not None and recap.fat > target.fat_g:
        return DailyAdvice(
            "fat_high",
            "Untuk makan berikutnya, pilih olahan yang tidak terlalu berminyak atau digoreng.",
        )

    if (
        late_day
        and complete_macros
        and recap.calories is not None
        and recap.calories < target.calories_kcal * 0.7
    ):
        return DailyAdvice(
            "calories_low",
            "Konsumsi hari ini masih jauh di bawah target; pertimbangkan makan berikutnya yang seimbang sesuai kebutuhan Anda.",
        )

    available = any(
        value is not None
        for value in (
            recap.calories,
            recap.protein,
            recap.fat,
            recap.fiber,
            recap.added_sugar,
            recap.sodium,
        )
    )
    if not available:
        return DailyAdvice(
            "insufficient_data",
            "Data nutrisi yang tersedia belum cukup untuk memberikan saran spesifik.",
        )

    return DailyAdvice(
        "balanced",
        "Pola konsumsi hari ini masih cukup sesuai dengan target Anda. Pertahankan pola makan yang seimbang.",
    )
