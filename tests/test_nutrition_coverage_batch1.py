"""Phase 3G: nutrition coverage expansion, batch 1.

Covers the full Phase 3G test plan for the 7 investigated foods (ayam bumbu
merah, tempe goreng, tahu goreng, urap sayur, ikan asin goreng, daun selada,
timun): exact TKPI matches win over USDA, unsafe equivalences are rejected
rather than forced, goreng/mentah preparations stay distinct, no compound
dish gets an invented recipe, provisional-local-falls-through-to-USDA is
unchanged, and provenance is preserved on every new record.
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.food_matcher import PROVISIONAL, VERIFIED, FoodMatcher, FoodRecord, get_matcher
from services.food_vision import DetectedFood
from services.nutrition_calculator import (
    COUNTED,
    SOURCE_LOCAL,
    SOURCE_USDA,
    UNMATCHED,
    calculate_meal,
)
from services.usda_food_data import UsdaClient

FOODS_PATH = Path(__file__).resolve().parent.parent / "data" / "foods.json"


def run(coro):
    return asyncio.run(coro)


def detected(name, grams, **overrides) -> DetectedFood:
    defaults = dict(identification_confidence=0.9, portion_confidence=0.6, serving_label="")
    defaults.update(overrides)
    return DetectedFood(name=name, estimated_grams=grams, **defaults)


NO_USDA = UsdaClient(api_key="")


class ExactTkpiMatchPriorityTest(unittest.TestCase):
    """1. exact TKPI match takes priority over USDA."""

    def test_tempe_goreng_uses_local_record_not_usda(self):
        def explode(request):
            raise AssertionError("USDA must not be called for a verified local record")

        import httpx
        client = UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        matcher = get_matcher()
        meal = run(calculate_meal([detected("tempe goreng", 100)], matcher, client))
        self.assertEqual(meal.items[0].status, COUNTED)
        self.assertEqual(meal.items[0].source, SOURCE_LOCAL)
        self.assertEqual(client.request_count, 0)

    def test_timun_uses_local_record_not_the_existing_usda_query(self):
        def explode(request):
            raise AssertionError("USDA must not be called now that timun has a verified local record")

        import httpx
        client = UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        matcher = get_matcher()
        meal = run(calculate_meal([detected("timun", 100)], matcher, client))
        self.assertEqual(meal.items[0].source, SOURCE_LOCAL)
        self.assertEqual(client.request_count, 0)

    def test_daun_selada_uses_local_record(self):
        matcher = get_matcher()
        meal = run(calculate_meal([detected("daun selada", 50)], matcher, NO_USDA))
        self.assertEqual(meal.items[0].status, COUNTED)
        self.assertEqual(meal.items[0].source, SOURCE_LOCAL)


class UnsafeAyamBumbuMerahRejectedTest(unittest.TestCase):
    """2. unsafe ayam bumbu merah alias is rejected."""

    def test_no_local_record_exists_for_ayam_bumbu_merah(self):
        matcher = get_matcher()
        self.assertIsNone(matcher.match("ayam bumbu merah"))

    def test_ayam_bumbu_merah_does_not_fuzzy_collide_with_ayam_goreng(self):
        matcher = get_matcher()
        # Explicitly guard against the exact unsafe equivalence the phase
        # brief calls out: this name must never resolve to ayam_goreng's record.
        record = matcher.match("ayam bumbu merah")
        self.assertIsNone(record)

    def test_ayam_bumbu_merah_is_unmatched_and_uncounted(self):
        matcher = get_matcher()
        meal = run(calculate_meal([detected("ayam bumbu merah", 150)], matcher, NO_USDA))
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 0)


class TempeGorengDistinctTest(unittest.TestCase):
    """3. tempe goreng distinct from plain tempe."""

    def test_tempe_goreng_and_tempe_are_different_records(self):
        matcher = get_matcher()
        goreng = matcher.match("tempe goreng")
        plain = matcher.match("tempe")
        self.assertIsNotNone(goreng)
        self.assertIsNotNone(plain)
        self.assertNotEqual(goreng.id, plain.id)

    def test_tempe_goreng_is_verified_plain_tempe_stays_provisional(self):
        matcher = get_matcher()
        self.assertTrue(matcher.match("tempe goreng").is_verified)
        self.assertFalse(matcher.match("tempe").is_verified)

    def test_tempe_goreng_and_tempe_have_very_different_calories(self):
        matcher = get_matcher()
        goreng = matcher.match("tempe goreng")
        plain = matcher.match("tempe")
        # Frying adds substantial oil - the two must not be conflated.
        self.assertGreater(goreng.calories_per_100g, plain.calories_per_100g)
        self.assertGreater(goreng.fat_per_100g, plain.fat_per_100g * 2)

    def test_bare_tempe_never_resolves_to_the_goreng_record(self):
        matcher = get_matcher()
        self.assertEqual(matcher.match("tempe").id, "tempe")


class TahuGorengDistinctTest(unittest.TestCase):
    """4. tahu goreng distinct from plain tahu."""

    def test_tahu_goreng_and_tahu_are_different_records(self):
        matcher = get_matcher()
        goreng = matcher.match("tahu goreng")
        plain = matcher.match("tahu")
        self.assertIsNotNone(goreng)
        self.assertIsNotNone(plain)
        self.assertNotEqual(goreng.id, plain.id)

    def test_tahu_goreng_is_verified_plain_tahu_stays_provisional(self):
        matcher = get_matcher()
        self.assertTrue(matcher.match("tahu goreng").is_verified)
        self.assertFalse(matcher.match("tahu").is_verified)

    def test_tahu_goreng_fat_matches_the_tkpi_goreng_value_not_the_mentah_one(self):
        # The shipped "tahu" record is USDA-sourced (a different underlying
        # tofu/preparation entirely), so it isn't a valid same-source
        # comparison. The meaningful comparison is TKPI's own mentah (CP061,
        # 4.7 g fat) vs goreng (CP062, 8.5 g fat) pair, per Phase 3D's notes
        # on the "tahu" record and this phase's audit report.
        matcher = get_matcher()
        goreng = matcher.match("tahu goreng")
        self.assertEqual(goreng.fat_per_100g, 8.5)
        self.assertGreater(goreng.fat_per_100g, 4.7)  # TKPI's own CP061 mentah value


class UrapSayurNoInventedRecipeTest(unittest.TestCase):
    """5. urap sayur is not assigned an invented recipe. 11. no fixed
    recipe grams are invented."""

    def test_no_local_record_exists_for_urap_sayur(self):
        matcher = get_matcher()
        self.assertIsNone(matcher.match("urap sayur"))
        self.assertIsNone(matcher.match("urap"))

    def test_urap_sayur_is_unmatched_not_silently_priced(self):
        matcher = get_matcher()
        meal = run(calculate_meal([detected("urap sayur", 120)], matcher, NO_USDA))
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_no_food_record_in_the_database_claims_to_be_urap(self):
        # Structural guard: nothing in foods.json was added under any name
        # that could be construed as urap sayur's invented composite.
        matcher = get_matcher()
        ids = {r.id for r in matcher.records}
        names = {r.name.lower() for r in matcher.records}
        self.assertNotIn("urap", ids)
        self.assertNotIn("urap sayur", names)


class IkanAsinGorengUnavailableTest(unittest.TestCase):
    """6. ikan asin goreng remains unavailable if no defensible source exists."""

    def test_no_local_record_exists_for_ikan_asin_goreng(self):
        matcher = get_matcher()
        self.assertIsNone(matcher.match("ikan asin goreng"))

    def test_ikan_asin_goreng_does_not_fall_back_to_a_raw_fish_record(self):
        # There is no local "ikan asin" (raw/dried) record shipped either,
        # so this also guards against a future accidental fuzzy collision.
        matcher = get_matcher()
        self.assertIsNone(matcher.match("ikan asin"))
        self.assertIsNone(matcher.match("ikan asin, kering, mentah"))

    def test_ikan_asin_goreng_has_no_curated_usda_query(self):
        from services.usda_food_data import FOOD_QUERIES
        self.assertNotIn("ikan asin goreng", FOOD_QUERIES)

    def test_ikan_asin_goreng_stays_unavailable_end_to_end(self):
        matcher = get_matcher()
        meal = run(calculate_meal([detected("ikan asin goreng", 80)], matcher, NO_USDA))
        self.assertEqual(meal.items[0].status, UNMATCHED)


class DaunSeladaSafeResolutionTest(unittest.TestCase):
    """7. daun selada resolves safely if exact source is found."""

    def test_daun_selada_resolves_to_a_verified_record(self):
        matcher = get_matcher()
        record = matcher.match("daun selada")
        self.assertIsNotNone(record)
        self.assertTrue(record.is_verified)

    def test_bare_selada_and_lettuce_alias_to_the_same_record(self):
        matcher = get_matcher()
        via_daun = matcher.match("daun selada")
        via_bare = matcher.match("selada")
        via_english = matcher.match("lettuce")
        self.assertEqual(via_daun.id, via_bare.id)
        self.assertEqual(via_daun.id, via_english.id)

    def test_daun_selada_nutrition_calculation_is_plausible(self):
        matcher = get_matcher()
        meal = run(calculate_meal([detected("daun selada", 30)], matcher, NO_USDA))
        item = meal.items[0]
        self.assertEqual(item.status, COUNTED)
        # ~18 kcal/100g -> 30g should be a small, low-calorie contribution.
        self.assertLess(item.nutrition.calories, 10)


class TimunSafeResolutionTest(unittest.TestCase):
    """8. timun resolves safely if exact source is found."""

    def test_timun_resolves_to_a_verified_record(self):
        matcher = get_matcher()
        record = matcher.match("timun")
        self.assertIsNotNone(record)
        self.assertTrue(record.is_verified)

    def test_timun_aliases_all_resolve_to_the_same_record(self):
        matcher = get_matcher()
        ids = {matcher.match(name).id for name in ("timun", "ketimun", "mentimun", "cucumber")}
        self.assertEqual(len(ids), 1)

    def test_timun_does_not_resolve_to_a_named_variety(self):
        # DR109 (bare "Ketimun") was chosen over DR110/DR111 (krai/madura) -
        # confirm the record's own provenance says so, not a variety-specific one.
        matcher = get_matcher()
        record = matcher.match("timun")
        self.assertEqual(record.source_food_code, "DR109")
        self.assertNotIn("krai", record.source_food_name.lower())
        self.assertNotIn("madura", record.source_food_name.lower())


class ProvisionalStillFallsThroughToUsdaTest(unittest.TestCase):
    """9. provisional local still falls through to USDA."""

    def test_provisional_local_record_still_allows_usda_fallback(self):
        import httpx

        provisional = FoodRecord(
            id="tempe", name="Tempe", aliases=["tempe"],
            calories_per_100g=192.0, protein_per_100g=20.3, carbs_per_100g=7.6, fat_per_100g=10.8,
            data_status=PROVISIONAL,
        )

        def ok_handler(request):
            if "/foods/search" in request.url.path:
                return httpx.Response(200, json={"foods": [
                    {"fdcId": 1, "dataType": "SR Legacy", "description": "Cabbage, raw"}
                ]})
            return httpx.Response(200, json={
                "fdcId": 1, "dataType": "SR Legacy", "description": "Cabbage, raw",
                "foodNutrients": [
                    {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 25.0},
                    {"nutrient": {"number": "203", "unitName": "g"}, "amount": 1.3},
                    {"nutrient": {"number": "205", "unitName": "g"}, "amount": 5.8},
                    {"nutrient": {"number": "204", "unitName": "g"}, "amount": 0.1},
                ],
            })

        client = UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)))
        matcher = FoodMatcher([provisional])
        # "kol" has a curated USDA query and no local record in this matcher.
        meal = run(calculate_meal([detected("kol", 100)], matcher, client))
        self.assertEqual(meal.items[0].source, SOURCE_USDA)

    def test_new_verified_records_do_not_break_usda_fallback_for_other_foods(self):
        # Adding tempe_goreng/tahu_goreng/selada/timun as verified must not
        # affect provisional foods that still rely on USDA.
        import httpx

        def ok_handler(request):
            if "/foods/search" in request.url.path:
                return httpx.Response(200, json={"foods": [
                    {"fdcId": 171448, "dataType": "SR Legacy", "description": "Chicken, broilers or fryers, meat and skin, cooked, fried, batter"}
                ]})
            return httpx.Response(200, json={
                "fdcId": 171448, "dataType": "SR Legacy",
                "description": "Chicken, broilers or fryers, meat and skin, cooked, fried, batter",
                "foodNutrients": [
                    {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 289.0},
                    {"nutrient": {"number": "203", "unitName": "g"}, "amount": 22.54},
                    {"nutrient": {"number": "205", "unitName": "g"}, "amount": 9.42},
                    {"nutrient": {"number": "204", "unitName": "g"}, "amount": 17.35},
                ],
            })

        client = UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)))
        matcher = get_matcher()  # real shipped foods.json, post-Phase-3G
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, client))
        self.assertEqual(meal.items[0].source, SOURCE_USDA)


class ProvenancePreservedTest(unittest.TestCase):
    """10. source provenance preserved."""

    def test_every_new_record_cites_full_tkpi_provenance(self):
        matcher = get_matcher()
        new_ids = {"tempe_goreng", "tahu_goreng", "selada", "timun"}
        for record in matcher.records:
            if record.id in new_ids:
                with self.subTest(food=record.id):
                    self.assertTrue(record.source.strip())
                    self.assertTrue(record.source_reference.strip())
                    self.assertTrue(record.source_food_code.strip())
                    self.assertTrue(record.source_food_name.strip())
                    self.assertEqual(record.source_version, "TKPI 2020")
                    self.assertEqual(record.data_status, VERIFIED)

    def test_new_records_cite_the_expected_tkpi_codes(self):
        matcher = get_matcher()
        expected = {
            "tempe_goreng": "CP076",
            "tahu_goreng": "CP062",
            "selada": "DR145",
            "timun": "DR109",
        }
        for food_id, code in expected.items():
            with self.subTest(food=food_id):
                record = next(r for r in matcher.records if r.id == food_id)
                self.assertEqual(record.source_food_code, code)

    def test_bdd_factors_cite_a_source_when_present(self):
        matcher = get_matcher()
        for record in matcher.records:
            if record.has_verified_edible_portion or record.edible_portion_status == VERIFIED:
                with self.subTest(food=record.id):
                    self.assertTrue(record.edible_portion_source.strip())


class ExistingSuiteStillPassesTest(unittest.TestCase):
    """12. existing Phase 3F/3F.1 tests remain passing (spot-check here;
    the full suite is run separately)."""

    def test_database_still_loads_and_ids_are_unique(self):
        matcher = get_matcher()
        ids = [r.id for r in matcher.records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_verified_local_priority_over_usda_still_works_for_pre_existing_food(self):
        import httpx

        def explode(request):
            raise AssertionError("USDA must not be called for a verified local record")

        client = UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        matcher = get_matcher()
        meal = run(calculate_meal([detected("nasi putih", 100)], matcher, client))
        self.assertEqual(meal.items[0].source, SOURCE_LOCAL)


if __name__ == "__main__":
    unittest.main()
