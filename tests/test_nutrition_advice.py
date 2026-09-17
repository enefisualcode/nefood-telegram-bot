"""Rule-based personal daily advice tests."""

import unittest
from datetime import datetime

from services.daily_recap import DailyFoodEntry, DailyRecap, JAKARTA
from services.daily_targets import DailyTargets
from services.nutrition_advice import choose_daily_advice


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
ENTRY = DailyFoodEntry(NOON, "makanan uji", 100, True)


def recap(**changes) -> DailyRecap:
    values = dict(
        local_date=NOON.date(),
        entries=[ENTRY],
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
    return DailyRecap(**values)


class DailyAdviceTest(unittest.TestCase):
    def test_high_sodium_suggests_lower_salt(self):
        advice = choose_daily_advice(recap(sodium=2100), TARGET, NOON)
        self.assertEqual(advice.code, "sodium")
        self.assertIn("rendah garam", advice.message)

    def test_high_sugar_suggests_reducing_sweet_food_or_drink(self):
        advice = choose_daily_advice(recap(added_sugar=55), TARGET, NOON)
        self.assertEqual(advice.code, "sugar")
        self.assertIn("makanan atau minuman manis", advice.message)

    def test_low_fiber_suggests_fruit_or_vegetables(self):
        advice = choose_daily_advice(recap(fiber=10), TARGET, EVENING)
        self.assertEqual(advice.code, "fiber_low")
        self.assertIn("sayur atau buah", advice.message)

    def test_low_protein_suggests_protein_source(self):
        advice = choose_daily_advice(recap(fiber=25, protein=40), TARGET, EVENING)
        self.assertEqual(advice.code, "protein_low")
        self.assertIn("sumber protein", advice.message)

    def test_high_calories_suggests_lighter_next_meal(self):
        advice = choose_daily_advice(recap(calories=2100), TARGET, NOON)
        self.assertEqual(advice.code, "calories_high")
        self.assertIn("lebih ringan", advice.message)

    def test_missing_nutrients_do_not_create_false_advice(self):
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
        advice = choose_daily_advice(value, TARGET, EVENING)
        self.assertEqual(advice.code, "insufficient_data")
        self.assertIn("belum cukup", advice.message)

    def test_normal_consumption_has_positive_message(self):
        advice = choose_daily_advice(recap(), TARGET, NOON)
        self.assertEqual(advice.code, "balanced")
        self.assertIn("cukup sesuai", advice.message)

    def test_only_one_highest_priority_advice_is_returned(self):
        advice = choose_daily_advice(
            recap(sodium=2100, added_sugar=55, calories=2100, fat=80),
            TARGET,
            EVENING,
        )
        self.assertEqual(advice.code, "sodium")
        self.assertIsInstance(advice.message, str)

    def test_two_users_get_advice_from_their_own_recap(self):
        user_one = choose_daily_advice(recap(sodium=2100), TARGET, NOON)
        user_two = choose_daily_advice(recap(sodium=1200, added_sugar=55), TARGET, NOON)
        self.assertEqual(user_one.code, "sodium")
        self.assertEqual(user_two.code, "sugar")

    def test_partial_data_does_not_trigger_low_advice(self):
        value = recap(protein=10, known_nutrition_items=0)
        advice = choose_daily_advice(value, TARGET, EVENING)
        self.assertNotEqual(advice.code, "protein_low")


if __name__ == "__main__":
    unittest.main()
