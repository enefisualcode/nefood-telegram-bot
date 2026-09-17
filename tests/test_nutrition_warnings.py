"""Deterministic daily nutrition warning tests."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from services.daily_recap import DailyRecap, JAKARTA, build_daily_recap
from services.daily_targets import DailyTargets
from services.meal_store import MealStore
from services.nutrition_calculator import COUNTED, FoodNutrition, MealNutrition, Nutrition
from services.nutrition_warnings import MAX_WARNINGS, build_nutrition_warnings


TARGET = DailyTargets(
    calories_kcal=2000,
    protein_g=80,
    carbs_g=260,
    fat_g=65,
    fiber_g=28,
    added_sugar_max_g=50,
    sodium_max_mg=2000,
    resting_energy_kcal=1500,
    maintenance_energy_kcal=2000,
    goal="Mempertahankan berat badan",
)
NOON = datetime(2026, 9, 17, 12, tzinfo=JAKARTA)
EVENING = datetime(2026, 9, 17, 20, tzinfo=JAKARTA)


def recap(**changes) -> DailyRecap:
    values = dict(
        local_date=NOON.date(),
        calories=1800,
        protein=75,
        carbs=230,
        fat=60,
        fiber=25,
        added_sugar=30,
        sodium=1400,
        known_nutrition_items=1,
        fiber_known_items=1,
        added_sugar_known_items=1,
        sodium_known_items=1,
    )
    values.update(changes)
    result = DailyRecap(**values)
    # Direct rule tests need one logical item for completeness checks.
    object.__setattr__(result, "entries", [object()])
    return result


def codes(value: DailyRecap, now=NOON) -> list[str]:
    return [warning.code for warning in build_nutrition_warnings(value, TARGET, now)]


class NutritionWarningRulesTest(unittest.TestCase):
    def test_normal_consumption_has_no_warning(self):
        self.assertEqual(codes(recap()), [])

    def test_calories_over_target_warns(self):
        self.assertIn("calories_high", codes(recap(calories=2050)))

    def test_sodium_near_limit_warns(self):
        self.assertIn("sodium_near", codes(recap(sodium=1600)))

    def test_added_sugar_over_limit_warns(self):
        self.assertIn("sugar_high", codes(recap(added_sugar=55)))

    def test_low_fiber_warns_only_late_with_complete_data(self):
        value = recap(fiber=14)
        self.assertNotIn("fiber_low", codes(value, NOON))
        self.assertIn("fiber_low", codes(value, EVENING))

    def test_missing_nutrients_create_no_false_warning(self):
        value = recap(
            calories=None,
            protein=None,
            fat=None,
            fiber=None,
            added_sugar=None,
            sodium=None,
            known_nutrition_items=0,
            fiber_known_items=0,
        )
        self.assertEqual(codes(value, EVENING), [])

    def test_incomplete_data_does_not_create_low_warning(self):
        value = recap(fiber=2, fiber_known_items=0)
        self.assertNotIn("fiber_low", codes(value, EVENING))

    def test_two_users_receive_warnings_from_their_own_data(self):
        first = codes(recap(calories=2100))
        second = codes(recap(calories=1800))
        self.assertIn("calories_high", first)
        self.assertNotIn("calories_high", second)

    def test_warning_count_is_limited_and_prioritized(self):
        value = recap(calories=2200, fat=80, added_sugar=60, sodium=2100)
        warnings = build_nutrition_warnings(value, TARGET, EVENING)
        self.assertEqual(len(warnings), MAX_WARNINGS)
        self.assertEqual([item.code for item in warnings], ["sodium_high", "sugar_high", "calories_high"])


class OptionalNutrientStorageTest(unittest.TestCase):
    def test_optional_nutrients_survive_storage_and_recap(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = MealStore(Path(temporary) / "nutrufood.db")
            nutrition = Nutrition(
                calories=200,
                protein=10,
                carbs=20,
                fat=8,
                fiber=5,
                added_sugar=4,
                sodium=700,
            )
            meal = MealNutrition(
                items=[
                    FoodNutrition(
                        name="makanan uji",
                        estimated_gross_grams=100,
                        calculated_edible_grams=100,
                        status=COUNTED,
                        nutrition=nutrition,
                        source="local",
                    )
                ],
                total=nutrition,
            )
            store.save(1, "text", meal, NOON)
            result = build_daily_recap(1, MealStore(store.database_path), NOON)
            self.assertEqual(result.fiber, 5)
            self.assertEqual(result.added_sugar, 4)
            self.assertEqual(result.sodium, 700)


if __name__ == "__main__":
    unittest.main()
