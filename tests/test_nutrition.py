"""Unit tests for food matching and nutrition calculation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.food_matcher import (
    PROVISIONAL,
    VERIFIED,
    FoodMatcher,
    FoodRecord,
    get_matcher,
    normalize,
)
from services.food_vision import DetectedFood
from services.nutrition_calculator import (
    COUNTED,
    UNMATCHED,
    UNVERIFIED,
    Nutrition,
    calculate_meal,
    scale,
)

# Test fixtures with made-up-but-plausible values. These are example data for
# exercising the arithmetic, not sourced nutrition facts.
RICE = FoodRecord(
    id="nasi_putih",
    name="Nasi putih",
    aliases=["nasi", "white rice"],
    calories_per_100g=130.0,
    protein_per_100g=2.7,
    carbs_per_100g=28.2,
    fat_per_100g=0.3,
    data_status=VERIFIED,
)
CHICKEN = FoodRecord(
    id="ayam_goreng",
    name="Ayam goreng",
    aliases=["fried chicken"],
    calories_per_100g=279.0,
    protein_per_100g=27.1,
    carbs_per_100g=8.4,
    fat_per_100g=15.2,
    data_status=VERIFIED,
)
# Same values as CHICKEN, but unverified: it must never reach a total.
PROVISIONAL_CHICKEN = FoodRecord(
    id="ayam_goreng",
    name="Ayam goreng",
    aliases=["fried chicken"],
    calories_per_100g=279.0,
    protein_per_100g=27.1,
    carbs_per_100g=8.4,
    fat_per_100g=15.2,
    data_status=PROVISIONAL,
)


def detected(name, grams):
    return DetectedFood(
        name=name,
        estimated_grams=grams,
        identification_confidence=0.9,
        portion_confidence=0.5,
    )


class NormalizeTest(unittest.TestCase):
    def test_trims_and_lowercases(self):
        self.assertEqual(normalize("  Nasi Putih  "), "nasi putih")

    def test_collapses_whitespace_and_punctuation(self):
        self.assertEqual(normalize("Ayam   Goreng,"), "ayam goreng")


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.matcher = FoodMatcher([RICE, CHICKEN])

    def test_exact_match_by_name(self):
        self.assertIs(self.matcher.match("Nasi putih"), RICE)

    def test_exact_match_is_case_and_space_insensitive(self):
        self.assertIs(self.matcher.match("  AYAM GORENG "), CHICKEN)

    def test_alias_match(self):
        self.assertIs(self.matcher.match("nasi"), RICE)
        self.assertIs(self.matcher.match("Fried Chicken"), CHICKEN)

    def test_close_typo_still_matches(self):
        self.assertIs(self.matcher.match("ayam gorengg"), CHICKEN)

    def test_unknown_food_returns_none(self):
        self.assertIsNone(self.matcher.match("kemangi"))

    def test_unrelated_food_is_not_matched(self):
        # Must not fuzzy-match onto "nasi" or "ayam goreng".
        for name in ("sambal", "rendang", "es teh manis", "kerupuk"):
            with self.subTest(name=name):
                self.assertIsNone(self.matcher.match(name))

    def test_empty_name_returns_none(self):
        self.assertIsNone(self.matcher.match("   "))


class ScalingTest(unittest.TestCase):
    def test_scales_per_100g_values(self):
        result = scale(RICE, 200).rounded()
        self.assertEqual(result.calories, 260)
        self.assertEqual(result.protein, 5.4)
        self.assertEqual(result.carbs, 56.4)
        self.assertEqual(result.fat, 0.6)

    def test_partial_portion(self):
        result = scale(CHICKEN, 150).rounded()
        self.assertEqual(result.calories, 419)  # 279 * 1.5 = 418.5 -> 419
        self.assertEqual(result.protein, 40.7)  # 27.1 * 1.5 = 40.65 -> 40.7
        self.assertEqual(result.fat, 22.8)

    def test_zero_grams(self):
        result = scale(RICE, 0).rounded()
        self.assertEqual(result.calories, 0)
        self.assertEqual(result.protein, 0.0)


class RoundingTest(unittest.TestCase):
    def test_calories_round_to_integer_and_macros_to_one_decimal(self):
        result = Nutrition(calories=418.5, protein=40.65, carbs=1.24, fat=0.05).rounded()
        self.assertIsInstance(result.calories, int)
        self.assertEqual(result.calories, 419)
        self.assertEqual(result.protein, 40.7)
        self.assertEqual(result.carbs, 1.2)

    def test_totals_are_summed_before_rounding(self):
        # 0.44 + 0.44 rounds to 0.9, not 0.8 (which rounding first would give).
        meal = Nutrition(protein=0.44) + Nutrition(protein=0.44)
        self.assertEqual(meal.rounded().protein, 0.9)


class MealCalculationTest(unittest.TestCase):
    def setUp(self):
        self.matcher = FoodMatcher([RICE, CHICKEN])

    def test_full_meal_totals(self):
        meal = calculate_meal(
            [detected("nasi putih", 100), detected("ayam goreng", 100)], self.matcher
        )
        total = meal.total.rounded()
        self.assertEqual(len(meal.counted_items), 2)
        self.assertEqual(total.calories, 409)  # 130 + 279
        self.assertEqual(total.protein, 29.8)  # 2.7 + 27.1
        self.assertEqual(total.fat, 15.5)

    def test_partial_meal_skips_unmatched_but_still_totals(self):
        meal = calculate_meal(
            [detected("nasi putih", 100), detected("kemangi", 10)], self.matcher
        )
        self.assertEqual(len(meal.items), 2)
        self.assertEqual([i.name for i in meal.unmatched_items], ["kemangi"])
        # Total comes only from the matched rice.
        self.assertEqual(meal.total.rounded().calories, 130)

    def test_all_unmatched_gives_zero_total(self):
        meal = calculate_meal([detected("kemangi", 10)], self.matcher)
        self.assertEqual(meal.counted_items, [])
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_unmatched_item_keeps_name_and_grams(self):
        meal = calculate_meal([detected("Kemangi", 15)], self.matcher)
        item = meal.unmatched_items[0]
        self.assertEqual(item.grams, 15)
        self.assertIsNone(item.nutrition)

    def test_empty_detection(self):
        meal = calculate_meal([], self.matcher)
        self.assertEqual(meal.items, [])
        self.assertEqual(meal.total.rounded().calories, 0)


class DataStatusTest(unittest.TestCase):
    """Only verified records may contribute to user-facing nutrition."""

    def test_record_defaults_to_provisional(self):
        record = FoodRecord(
            id="x", name="X", aliases=[], calories_per_100g=1.0,
            protein_per_100g=1.0, carbs_per_100g=1.0, fat_per_100g=1.0,
        )
        self.assertFalse(record.is_verified)

    def test_verified_record_contributes_to_total(self):
        matcher = FoodMatcher([CHICKEN])
        meal = calculate_meal([detected("ayam goreng", 100)], matcher)
        item = meal.items[0]
        self.assertEqual(item.status, COUNTED)
        self.assertTrue(item.counted)
        self.assertEqual(meal.total.rounded().calories, 279)

    def test_provisional_record_does_not_contribute(self):
        matcher = FoodMatcher([PROVISIONAL_CHICKEN])
        meal = calculate_meal([detected("ayam goreng", 100)], matcher)
        item = meal.items[0]
        self.assertEqual(item.status, UNVERIFIED)
        self.assertFalse(item.counted)
        self.assertIsNone(item.nutrition)
        self.assertEqual(meal.total.rounded().calories, 0)
        self.assertEqual(meal.counted_items, [])
        self.assertEqual(len(meal.unverified_items), 1)

    def test_provisional_record_is_still_matched_and_kept(self):
        matcher = FoodMatcher([PROVISIONAL_CHICKEN])
        meal = calculate_meal([detected("ayam goreng", 100)], matcher)
        # The record is retained for reference even though it is not counted.
        self.assertIs(meal.items[0].record, PROVISIONAL_CHICKEN)

    def test_unmatched_food_does_not_contribute(self):
        matcher = FoodMatcher([CHICKEN])
        meal = calculate_meal([detected("kemangi", 10)], matcher)
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_mixed_verified_provisional_and_unmatched_meal(self):
        matcher = FoodMatcher([RICE, PROVISIONAL_CHICKEN])
        meal = calculate_meal(
            [
                detected("nasi putih", 100),      # verified   -> counted
                detected("ayam goreng", 140),     # provisional -> not counted
                detected("sambal", 35),           # unmatched   -> not counted
            ],
            matcher,
        )
        self.assertEqual([i.status for i in meal.items], [COUNTED, UNVERIFIED, UNMATCHED])
        self.assertEqual([i.name for i in meal.counted_items], ["nasi putih"])
        self.assertEqual([i.name for i in meal.unverified_items], ["ayam goreng"])
        self.assertEqual([i.name for i in meal.unmatched_items], ["sambal"])
        # Total reflects the verified rice alone.
        total = meal.total.rounded()
        self.assertEqual(total.calories, 130)
        self.assertEqual(total.protein, 2.7)


class RealDatabaseTest(unittest.TestCase):
    """The shipped data/foods.json must load and be internally consistent."""

    def setUp(self):
        self.matcher = get_matcher()

    def test_database_loads(self):
        self.assertGreater(len(self.matcher.records), 0)

    def test_every_record_cites_a_source(self):
        for record in self.matcher.records:
            with self.subTest(food=record.id):
                self.assertTrue(record.source.strip(), "missing source")
                self.assertTrue(record.source_reference.strip(), "missing reference")

    def test_every_record_is_reachable_by_its_own_name(self):
        for record in self.matcher.records:
            with self.subTest(food=record.id):
                self.assertIs(self.matcher.match(record.name), record)

    def test_every_record_declares_a_known_data_status(self):
        for record in self.matcher.records:
            with self.subTest(food=record.id):
                self.assertIn(record.data_status, (VERIFIED, PROVISIONAL))

    def test_shipped_database_has_no_unverified_food_counted(self):
        """Whatever is shipped, provisional records must never reach a total."""
        for record in self.matcher.records:
            if record.is_verified:
                continue
            meal = calculate_meal([detected(record.name, 100)], self.matcher)
            with self.subTest(food=record.id):
                self.assertEqual(meal.total.rounded().calories, 0)

    def test_ids_are_unique(self):
        ids = [r.id for r in self.matcher.records]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
