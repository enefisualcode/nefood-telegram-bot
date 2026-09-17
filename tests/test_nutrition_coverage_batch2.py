"""Stage 8: verified Indonesian food coverage expansion, batch 2."""

import asyncio
import unittest

from services.food_matcher import get_matcher
from services.food_vision import DetectedFood
from services.nutrition_calculator import COUNTED, SOURCE_LOCAL, calculate_meal
from services.usda_food_data import UsdaClient


NEW_RECORDS = {
    "kangkung_segar": ("DR100", 28, 3.4, 3.9, 0.7, 2.0, None),
    "sawi_segar": ("DR141", 28, 2.3, 4.0, 0.3, 1.7, None),
    "wortel_segar": ("DR166", 36, 1.0, 7.9, 0.6, 1.0, 70),
    "gado_gado": ("DP031", 137, 6.1, 21.0, 3.2, 1.1, None),
    "apel_segar": ("ER004", 58, 0.3, 14.9, 0.4, 2.6, 2),
    "jeruk_manis": ("ER039", 45, 0.9, 11.2, 0.2, 1.4, 1),
    "pepaya_segar": ("ER073", 46, 0.5, 12.2, 0.1, 1.6, 4),
    "semangka_segar": ("ER115", 28, 0.5, 6.9, 0.2, 0.4, 7),
}


class Batch2DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.matcher = get_matcher()
        self.by_id = {record.id: record for record in self.matcher.records}

    def test_all_batch_records_are_verified_with_exact_tkpi_codes(self):
        for food_id, expected in NEW_RECORDS.items():
            with self.subTest(food=food_id):
                record = self.by_id[food_id]
                self.assertTrue(record.is_verified)
                self.assertEqual(record.source_food_code, expected[0])
                self.assertEqual(record.source_version, "TKPI 2020")
                self.assertIn("Kementerian Kesehatan RI", record.source)

    def test_macros_fiber_and_sodium_match_audited_rows(self):
        for food_id, expected in NEW_RECORDS.items():
            with self.subTest(food=food_id):
                record = self.by_id[food_id]
                self.assertEqual(record.calories_per_100g, expected[1])
                self.assertEqual(record.protein_per_100g, expected[2])
                self.assertEqual(record.carbs_per_100g, expected[3])
                self.assertEqual(record.fat_per_100g, expected[4])
                self.assertEqual(record.fiber_per_100g, expected[5])
                self.assertEqual(record.sodium_mg_per_100g, expected[6])
                self.assertIsNone(record.added_sugar_per_100g)

    def test_raw_vegetables_do_not_claim_ambiguous_plain_names(self):
        self.assertIsNone(self.matcher.match("kangkung"))
        self.assertIsNone(self.matcher.match("sawi"))
        self.assertIsNone(self.matcher.match("wortel"))
        self.assertEqual(self.matcher.match("kangkung segar").id, "kangkung_segar")
        self.assertEqual(self.matcher.match("sawi segar").id, "sawi_segar")
        self.assertEqual(self.matcher.match("wortel segar").id, "wortel_segar")

    def test_common_fruit_aliases_resolve_safely(self):
        expected = {
            "apel": "apel_segar",
            "jeruk": "jeruk_manis",
            "pepaya": "pepaya_segar",
            "semangka": "semangka_segar",
            "gado gado": "gado_gado",
        }
        for alias, food_id in expected.items():
            with self.subTest(alias=alias):
                self.assertEqual(self.matcher.match(alias).id, food_id)

    def test_old_verified_values_are_unchanged(self):
        expected = {
            "nasi_putih": (180, 3.0, 39.8, 0.3),
            "kol": (29, 1.4, 5.3, 0.2),
            "tempe_goreng": (350, 24.5, 10.4, 26.6),
            "tahu_goreng": (115, 9.7, 2.5, 8.5),
            "selada": (18, 1.2, 2.9, 0.2),
            "timun": (8, 0.2, 1.4, 0.2),
        }
        for food_id, values in expected.items():
            with self.subTest(food=food_id):
                record = self.by_id[food_id]
                self.assertEqual(
                    (
                        record.calories_per_100g,
                        record.protein_per_100g,
                        record.carbs_per_100g,
                        record.fat_per_100g,
                    ),
                    values,
                )

    def test_existing_records_gain_only_verified_optional_nutrients(self):
        expected = {
            "nasi_putih": (0.4, 1),
            "kol": (1.5, None),
            "tempe_goreng": (4.2, None),
            "tahu_goreng": (0.1, None),
            "timun": (0.3, None),
        }
        for food_id, values in expected.items():
            with self.subTest(food=food_id):
                record = self.by_id[food_id]
                self.assertEqual(record.fiber_per_100g, values[0])
                self.assertEqual(record.sodium_mg_per_100g, values[1])
                self.assertIsNone(record.added_sugar_per_100g)

    def test_database_grew_without_duplicates(self):
        ids = [record.id for record in self.matcher.records]
        self.assertEqual(len(ids), 20)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(record.is_verified for record in self.matcher.records), 14)


class Batch2CalculationTest(unittest.TestCase):
    def test_optional_nutrients_scale_and_keep_local_provenance(self):
        food = DetectedFood(
            name="apel",
            estimated_grams=150,
            identification_confidence=1.0,
            portion_confidence=1.0,
        )
        meal = asyncio.run(
            calculate_meal([food], get_matcher(), UsdaClient(api_key=""))
        )
        item = meal.items[0]
        self.assertEqual(item.status, COUNTED)
        self.assertEqual(item.source, SOURCE_LOCAL)
        self.assertAlmostEqual(item.nutrition.fiber, 3.9)
        self.assertAlmostEqual(item.nutrition.sodium, 3.0)
        self.assertIsNone(item.nutrition.added_sugar)
        self.assertAlmostEqual(meal.total.fiber, 3.9)


if __name__ == "__main__":
    unittest.main()
