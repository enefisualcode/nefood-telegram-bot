"""Deterministic daily nutrition targets derived from a saved adult profile.

Energy uses the Mifflin-St Jeor resting-energy equation, then an activity
multiplier. Goal adjustments are deliberately conservative: at most 300 kcal
and at most 15% of maintenance energy. A weight-loss target never goes below
the estimated resting requirement.

Nutrition references used by the policy in this module:
- Mifflin et al. (1990), DOI 10.1093/ajcn/51.2.241
- National Academies DRI: 14 g fibre per 1,000 kcal and AMDR ranges
- WHO healthy-diet guidance: free sugars <10% energy, total fat <=30%
- WHO sodium guidance: adults <2,000 mg/day

These are population estimates for generally healthy adults, not medical
prescriptions. Pregnancy, breastfeeding, illness and athletic requirements
need individual professional assessment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from services.profile_store import UserProfile


ACTIVITY_FACTORS = {
    "Sangat ringan": 1.2,
    "Ringan": 1.375,
    "Sedang": 1.55,
    "Aktif": 1.725,
    "Sangat aktif": 1.9,
}

GOAL_DIRECTIONS = {
    "Menurunkan berat badan": -1,
    "Mempertahankan berat badan": 0,
    "Menaikkan berat badan": 1,
}


class TargetCalculationError(ValueError):
    """Raised when a saved profile cannot be used safely."""


@dataclass(frozen=True)
class DailyTargets:
    calories_kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int
    fiber_g: int
    added_sugar_max_g: int
    sodium_max_mg: int
    resting_energy_kcal: int
    maintenance_energy_kcal: int
    goal: str


def _round_half_up(value: float, quantum: str = "1") -> int:
    return int(Decimal(str(value)).quantize(Decimal(quantum), rounding=ROUND_HALF_UP))


def _validate(profile: UserProfile) -> None:
    if profile.age < 18:
        raise TargetCalculationError(
            "Perhitungan target otomatis saat ini hanya tersedia untuk pengguna usia 18 tahun ke atas."
        )
    if profile.gender not in ("Laki-laki", "Perempuan"):
        raise TargetCalculationError("Jenis kelamin pada profil tidak dikenali.")
    if profile.activity_level not in ACTIVITY_FACTORS:
        raise TargetCalculationError("Tingkat aktivitas pada profil tidak dikenali.")
    if profile.goal not in GOAL_DIRECTIONS:
        raise TargetCalculationError("Tujuan pada profil tidak dikenali.")
    if profile.height_cm <= 0 or profile.weight_kg <= 0:
        raise TargetCalculationError("Tinggi atau berat badan pada profil tidak valid.")


def resting_energy(profile: UserProfile) -> float:
    """Mifflin-St Jeor resting energy expenditure in kcal/day."""
    _validate(profile)
    sex_constant = 5 if profile.gender == "Laki-laki" else -161
    return (
        10 * profile.weight_kg
        + 6.25 * profile.height_cm
        - 5 * profile.age
        + sex_constant
    )


def calculate_daily_targets(profile: UserProfile) -> DailyTargets:
    bmr = resting_energy(profile)
    maintenance = bmr * ACTIVITY_FACTORS[profile.activity_level]

    direction = GOAL_DIRECTIONS[profile.goal]
    conservative_adjustment = min(300.0, maintenance * 0.15)
    target_energy = maintenance + direction * conservative_adjustment
    if direction < 0:
        target_energy = max(target_energy, bmr)

    # Present energy in practical 10-kcal increments.
    calories = _round_half_up(target_energy / 10) * 10

    # Current adult guidance uses 1.2 g/kg as the lower general protein
    # target. Cap it at the AMDR upper edge (35% energy) for unusual profiles.
    protein = min(profile.weight_kg * 1.2, calories * 0.35 / 4)
    protein_energy_share = protein * 4 / calories

    # Aim for 30% fat, but lower it (never below AMDR's 20%) when needed to
    # preserve at least 45% energy from carbohydrate.
    fat_share = max(0.20, min(0.30, 0.55 - protein_energy_share))
    fat = calories * fat_share / 9
    carbs = (calories - protein * 4 - fat * 9) / 4

    return DailyTargets(
        calories_kcal=calories,
        protein_g=_round_half_up(protein),
        carbs_g=_round_half_up(carbs),
        fat_g=_round_half_up(fat),
        fiber_g=_round_half_up(calories / 1000 * 14),
        added_sugar_max_g=_round_half_up(calories * 0.10 / 4),
        sodium_max_mg=2000,
        resting_energy_kcal=_round_half_up(bmr),
        maintenance_energy_kcal=_round_half_up(maintenance),
        goal=profile.goal,
    )
