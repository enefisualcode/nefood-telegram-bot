"""Unit tests for food matching, USDA fallback, and nutrition calculation."""

import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.food_matcher import (
    EDIBLE,
    GROSS,
    NOT_APPLICABLE,
    PROVISIONAL,
    VERIFIED,
    FoodMatcher,
    FoodRecord,
    get_matcher,
    normalize,
    valid_factor,
)
from services.food_vision import DetectedFood
from services.nutrition_calculator import (
    COUNTED,
    SOURCE_LOCAL,
    SOURCE_USDA,
    UNMATCHED,
    UNVERIFIED,
    Nutrition,
    calculate_meal,
    resolve_edible_grams,
    scale,
)
from services.usda_food_data import (
    UsdaClient,
    UsdaError,
    UsdaQuery,
    is_acceptable,
    to_usda_food,
)


def run(coro):
    """calculate_meal is async; these tests stay synchronous."""
    return asyncio.run(coro)


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

# Gross-weight chicken with a VERIFIED edible portion factor. 0.70 is an
# arbitrary example value for exercising the arithmetic, not a sourced BDD figure.
BDD_CHICKEN = FoodRecord(
    id="ayam_goreng",
    name="Ayam goreng",
    aliases=[],
    calories_per_100g=200.0,
    protein_per_100g=20.0,
    carbs_per_100g=10.0,
    fat_per_100g=5.0,
    data_status=VERIFIED,
    edible_portion_factor=0.70,
    edible_portion_status=VERIFIED,
    weight_basis=GROSS,
    edible_portion_source="Example source",
    edible_portion_source_reference="Example reference",
)
# Same, but the factor is only provisional: it must never be applied.
BDD_PROVISIONAL = FoodRecord(
    id="ayam_goreng",
    name="Ayam goreng",
    aliases=[],
    calories_per_100g=200.0,
    protein_per_100g=20.0,
    carbs_per_100g=10.0,
    fat_per_100g=5.0,
    data_status=VERIFIED,
    edible_portion_factor=0.70,
    edible_portion_status=PROVISIONAL,
    weight_basis=GROSS,
)
# A verified factor on an already-edible weight: must not be applied again.
BDD_ALREADY_EDIBLE = FoodRecord(
    id="ayam_goreng",
    name="Ayam goreng",
    aliases=[],
    calories_per_100g=200.0,
    protein_per_100g=20.0,
    carbs_per_100g=10.0,
    fat_per_100g=5.0,
    data_status=VERIFIED,
    edible_portion_factor=0.70,
    edible_portion_status=VERIFIED,
    weight_basis=EDIBLE,
)

# A disabled client: no API key, so no HTTP is ever attempted.
NO_USDA = UsdaClient(api_key="")


def detected(name, grams):
    return DetectedFood(
        name=name,
        estimated_grams=grams,
        identification_confidence=0.9,
        portion_confidence=0.5,
    )


# --------------------------------------------------------------------------
# Fake USDA transport
# --------------------------------------------------------------------------

SEARCH_HIT = {
    "foods": [
        {
            "fdcId": 171448,
            "dataType": "SR Legacy",
            "description": "Chicken, broilers or fryers, meat and skin, cooked, fried, batter",
        }
    ]
}

DETAIL_HIT = {
    "fdcId": 171448,
    "dataType": "SR Legacy",
    "description": "Chicken, broilers or fryers, meat and skin, cooked, fried, batter",
    "foodNutrients": [
        {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 289.0},
        {"nutrient": {"number": "203", "unitName": "g"}, "amount": 22.54},
        {"nutrient": {"number": "205", "unitName": "g"}, "amount": 9.42},
        {"nutrient": {"number": "204", "unitName": "g"}, "amount": 17.35},
    ],
}


def fake_usda(handler):
    """Build a UsdaClient whose HTTP calls are served by `handler`."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return UsdaClient(api_key="test-key", client=client)


def ok_handler(request):
    if "/foods/search" in request.url.path:
        return httpx.Response(200, json=SEARCH_HIT)
    return httpx.Response(200, json=DETAIL_HIT)


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
        meal = Nutrition(protein=0.44) + Nutrition(protein=0.44)
        self.assertEqual(meal.rounded().protein, 0.9)


class MealCalculationTest(unittest.TestCase):
    def setUp(self):
        self.matcher = FoodMatcher([RICE, CHICKEN])

    def test_full_meal_totals(self):
        meal = run(
            calculate_meal(
                [detected("nasi putih", 100), detected("ayam goreng", 100)],
                self.matcher,
                NO_USDA,
            )
        )
        total = meal.total.rounded()
        self.assertEqual(len(meal.counted_items), 2)
        self.assertEqual(total.calories, 409)  # 130 + 279
        self.assertEqual(total.protein, 29.8)  # 2.7 + 27.1
        self.assertEqual(total.fat, 15.5)

    def test_partial_meal_skips_unmatched_but_still_totals(self):
        meal = run(
            calculate_meal(
                [detected("nasi putih", 100), detected("kemangi", 10)],
                self.matcher,
                NO_USDA,
            )
        )
        self.assertEqual(len(meal.items), 2)
        self.assertEqual([i.name for i in meal.unmatched_items], ["kemangi"])
        self.assertEqual(meal.total.rounded().calories, 130)

    def test_all_unmatched_gives_zero_total(self):
        meal = run(calculate_meal([detected("kemangi", 10)], self.matcher, NO_USDA))
        self.assertEqual(meal.counted_items, [])
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_unmatched_item_keeps_name_and_grams(self):
        meal = run(calculate_meal([detected("Kemangi", 15)], self.matcher, NO_USDA))
        item = meal.unmatched_items[0]
        self.assertEqual(item.grams, 15)
        self.assertIsNone(item.nutrition)

    def test_empty_detection(self):
        meal = run(calculate_meal([], self.matcher, NO_USDA))
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
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, NO_USDA))
        item = meal.items[0]
        self.assertEqual(item.status, COUNTED)
        self.assertEqual(item.source, SOURCE_LOCAL)
        self.assertEqual(meal.total.rounded().calories, 279)

    def test_provisional_record_does_not_contribute(self):
        matcher = FoodMatcher([PROVISIONAL_CHICKEN])
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, NO_USDA))
        item = meal.items[0]
        self.assertEqual(item.status, UNVERIFIED)
        self.assertFalse(item.counted)
        self.assertIsNone(item.nutrition)
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_provisional_record_is_still_matched_and_kept(self):
        matcher = FoodMatcher([PROVISIONAL_CHICKEN])
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, NO_USDA))
        self.assertIs(meal.items[0].record, PROVISIONAL_CHICKEN)

    def test_unmatched_food_does_not_contribute(self):
        matcher = FoodMatcher([CHICKEN])
        meal = run(calculate_meal([detected("kemangi", 10)], matcher, NO_USDA))
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_mixed_verified_provisional_and_unmatched_meal(self):
        matcher = FoodMatcher([RICE, PROVISIONAL_CHICKEN])
        meal = run(
            calculate_meal(
                [
                    detected("nasi putih", 100),   # verified    -> counted
                    detected("ayam goreng", 140),  # provisional -> not counted
                    detected("sambal", 35),        # unmatched   -> not counted
                ],
                matcher,
                NO_USDA,
            )
        )
        self.assertEqual(
            [i.status for i in meal.items], [COUNTED, UNVERIFIED, UNMATCHED]
        )
        self.assertEqual([i.name for i in meal.counted_items], ["nasi putih"])
        self.assertEqual([i.name for i in meal.unverified_items], ["ayam goreng"])
        self.assertEqual([i.name for i in meal.unmatched_items], ["sambal"])
        total = meal.total.rounded()
        self.assertEqual(total.calories, 130)
        self.assertEqual(total.protein, 2.7)


class UsdaCandidateSafetyTest(unittest.TestCase):
    MAPPING = UsdaQuery(
        query="cabbage raw",
        require_all=("cabbage", "raw"),
        reject_any=("red", "napa"),
    )

    def test_accepts_matching_generic_record(self):
        self.assertTrue(is_acceptable("Cabbage, raw", "SR Legacy", self.MAPPING))

    def test_rejects_branded_data_type(self):
        self.assertFalse(is_acceptable("Cabbage, raw", "Branded", self.MAPPING))

    def test_rejects_excluded_variant(self):
        self.assertFalse(is_acceptable("Cabbage, red, raw", "SR Legacy", self.MAPPING))

    def test_rejects_missing_required_token(self):
        self.assertFalse(is_acceptable("Cabbage, cooked", "SR Legacy", self.MAPPING))


class UsdaNutrientParsingTest(unittest.TestCase):
    def test_parses_nested_detail_shape(self):
        food = to_usda_food(DETAIL_HIT, query="q")
        self.assertEqual(food.fdc_id, 171448)
        self.assertEqual(food.calories_per_100g, 289.0)
        self.assertEqual(food.protein_per_100g, 22.54)
        self.assertEqual(food.source, "USDA FoodData Central")
        self.assertTrue(food.retrieved_at)

    def test_parses_flat_search_shape(self):
        payload = {
            "fdcId": 1,
            "description": "X",
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrientNumber": "208", "unitName": "KCAL", "value": 100},
                {"nutrientNumber": "203", "unitName": "G", "value": 1},
                {"nutrientNumber": "205", "unitName": "G", "value": 2},
                {"nutrientNumber": "204", "unitName": "G", "value": 3},
            ],
        }
        self.assertEqual(to_usda_food(payload).calories_per_100g, 100)

    def test_ignores_kilojoule_energy(self):
        payload = {
            "fdcId": 1,
            "description": "X",
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrient": {"number": "208", "unitName": "kJ"}, "amount": 1210},
                {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 289},
                {"nutrient": {"number": "203", "unitName": "g"}, "amount": 1},
                {"nutrient": {"number": "205", "unitName": "g"}, "amount": 2},
                {"nutrient": {"number": "204", "unitName": "g"}, "amount": 3},
            ],
        }
        self.assertEqual(to_usda_food(payload).calories_per_100g, 289)

    def test_foundation_atwater_energy_is_used_when_208_is_absent(self):
        """Foundation records carry no 208; energy is 957/958 instead."""
        payload = {
            "fdcId": 2346406,
            "description": "Cucumber, with peel, raw",
            "dataType": "Foundation",
            "foodNutrients": [
                {"nutrient": {"number": "957", "unitName": "kcal"}, "amount": 15.9075},
                {"nutrient": {"number": "958", "unitName": "kcal"}, "amount": 13.934925},
                {"nutrient": {"number": "203", "unitName": "g"}, "amount": 0.625},
                {"nutrient": {"number": "205", "unitName": "g"}, "amount": 2.9525},
                {"nutrient": {"number": "204", "unitName": "g"}, "amount": 0.1775},
            ],
        }
        food = to_usda_food(payload)
        self.assertEqual(food.calories_per_100g, 15.9075)
        self.assertEqual(food.energy_nutrient_number, "957")

    def test_208_wins_over_atwater_when_both_present(self):
        payload = {
            "fdcId": 1,
            "description": "X",
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrient": {"number": "957", "unitName": "kcal"}, "amount": 11},
                {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 25},
                {"nutrient": {"number": "203", "unitName": "g"}, "amount": 1},
                {"nutrient": {"number": "205", "unitName": "g"}, "amount": 2},
                {"nutrient": {"number": "204", "unitName": "g"}, "amount": 3},
            ],
        }
        food = to_usda_food(payload)
        self.assertEqual(food.calories_per_100g, 25)
        self.assertEqual(food.energy_nutrient_number, "208")

    def test_missing_nutrient_raises_rather_than_guessing(self):
        payload = {
            "fdcId": 99,
            "description": "X",
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 100}
            ],
        }  # protein/carbs/fat absent
        with self.assertRaises(UsdaError):
            to_usda_food(payload)


class UsdaFallbackTest(unittest.TestCase):
    """Source priority: verified local -> USDA -> unavailable."""

    def test_verified_local_record_wins_and_usda_is_not_called(self):
        def explode(request):
            raise AssertionError("USDA must not be called for a verified record")

        client = fake_usda(explode)
        matcher = FoodMatcher([CHICKEN])
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, client))
        self.assertEqual(meal.items[0].source, SOURCE_LOCAL)
        self.assertEqual(client.request_count, 0)

    def test_provisional_local_record_allows_usda_fallback(self):
        client = fake_usda(ok_handler)
        matcher = FoodMatcher([PROVISIONAL_CHICKEN])
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, client))
        item = meal.items[0]
        self.assertEqual(item.status, COUNTED)
        self.assertEqual(item.source, SOURCE_USDA)
        self.assertEqual(item.usda.fdc_id, 171448)
        self.assertEqual(meal.total.rounded().calories, 289)

    def test_unmatched_local_food_uses_usda_fallback(self):
        client = fake_usda(ok_handler)
        matcher = FoodMatcher([])  # nothing local at all
        meal = run(calculate_meal([detected("ayam goreng", 200)], matcher, client))
        item = meal.items[0]
        self.assertEqual(item.source, SOURCE_USDA)
        self.assertEqual(meal.total.rounded().calories, 578)  # 289 * 2

    def test_usda_result_retains_provenance(self):
        client = fake_usda(ok_handler)
        meal = run(calculate_meal([detected("ayam goreng", 100)], FoodMatcher([]), client))
        usda = meal.items[0].usda
        self.assertEqual(usda.fdc_id, 171448)
        self.assertEqual(usda.data_type, "SR Legacy")
        self.assertIn("Chicken", usda.description)
        self.assertEqual(usda.source, "USDA FoodData Central")
        self.assertTrue(usda.query)
        self.assertTrue(usda.retrieved_at)

    def test_no_safe_usda_match_stays_unavailable(self):
        def unrelated(request):
            if "/foods/search" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "foods": [
                            {
                                "fdcId": 1,
                                "dataType": "SR Legacy",
                                "description": "Chicken, raw",  # not fried
                            }
                        ]
                    },
                )
            raise AssertionError("detail must not be fetched for a rejected candidate")

        client = fake_usda(unrelated)
        meal = run(calculate_meal([detected("ayam goreng", 100)], FoodMatcher([]), client))
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_food_without_curated_query_is_not_looked_up(self):
        def explode(request):
            raise AssertionError("no curated query, so no request should be made")

        client = fake_usda(explode)
        meal = run(calculate_meal([detected("sambal", 30)], FoodMatcher([]), client))
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(client.request_count, 0)


class UsdaFailureTest(unittest.TestCase):
    """A USDA failure must degrade one food, never the whole meal."""

    def _failing_meal(self, handler):
        client = fake_usda(handler)
        return run(
            calculate_meal(
                [detected("nasi putih", 100), detected("ayam goreng", 100)],
                FoodMatcher([RICE]),
                client,
            )
        )

    def test_timeout_does_not_break_meal(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        meal = self._failing_meal(timeout)
        self.assertEqual(meal.items[1].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 130)  # rice still counted

    def test_rate_limit_does_not_break_meal(self):
        def rate_limited(request):
            return httpx.Response(429, json={"error": "over rate limit"})

        meal = self._failing_meal(rate_limited)
        self.assertEqual(meal.items[1].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 130)

    def test_bad_json_does_not_break_meal(self):
        def bad_json(request):
            return httpx.Response(200, content=b"<html>not json</html>")

        meal = self._failing_meal(bad_json)
        self.assertEqual(meal.items[1].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 130)

    def test_missing_nutrients_does_not_break_meal(self):
        def missing(request):
            if "/foods/search" in request.url.path:
                return httpx.Response(200, json=SEARCH_HIT)
            return httpx.Response(
                200,
                json={
                    "fdcId": 171448,
                    "description": "Chicken, broilers or fryers, meat and skin, cooked, fried, batter",
                    "dataType": "SR Legacy",
                    "foodNutrients": [
                        {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 289}
                    ],
                },
            )

        meal = self._failing_meal(missing)
        self.assertEqual(meal.items[1].status, UNMATCHED)
        self.assertEqual(meal.total.rounded().calories, 130)

    def test_error_messages_never_leak_the_api_key(self):
        client = fake_usda(ok_handler)
        message = client._redact(
            "Client error '429' for url 'https://api.nal.usda.gov/fdc/v1/"
            "foods/search?query=x&api_key=test-key'"
        )
        self.assertNotIn("test-key", message)
        self.assertIn("api_key=***", message)

    def test_disabled_client_makes_no_requests(self):
        meal = run(calculate_meal([detected("ayam goreng", 100)], FoodMatcher([]), NO_USDA))
        self.assertEqual(meal.items[0].status, UNMATCHED)
        self.assertEqual(NO_USDA.request_count, 0)


class UsdaCacheTest(unittest.TestCase):
    def test_repeated_food_is_fetched_once(self):
        client = fake_usda(ok_handler)
        meal = run(
            calculate_meal(
                [detected("ayam goreng", 100), detected("ayam goreng", 50)],
                FoodMatcher([]),
                client,
            )
        )
        # Two requests for the first lookup (search + detail), none for the second.
        self.assertEqual(client.request_count, 2)
        self.assertEqual([i.source for i in meal.items], [SOURCE_USDA, SOURCE_USDA])
        self.assertEqual(meal.total.rounded().calories, 434)  # 289 + 144.5

    def test_negative_result_is_cached_too(self):
        calls = {"n": 0}

        def no_match(request):
            calls["n"] += 1
            return httpx.Response(200, json={"foods": []})

        client = fake_usda(no_match)
        run(
            calculate_meal(
                [detected("kol", 50), detected("kol", 25)], FoodMatcher([]), client
            )
        )
        self.assertEqual(calls["n"], 1)

    def test_transient_failure_is_not_cached(self):
        # Phase 3F.1: two occurrences of the *same* food within one meal are
        # now resolved concurrently and share a single in-flight USDA fetch
        # (see UsdaCacheTest.test_concurrent_identical_foods_share_one_fetch
        # in test_perf_and_progress.py) - so a transient failure there fails
        # both together, by design, instead of leaving room for one to
        # "retry" the other's request. What must still hold is that the
        # failure itself is never cached: a later, separate call is free to
        # retry fresh and succeed.
        calls = {"n": 0}

        def flaky(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("boom", request=request)
            if "/foods/search" in request.url.path:
                return httpx.Response(200, json=SEARCH_HIT)
            return httpx.Response(200, json=DETAIL_HIT)

        client = fake_usda(flaky)

        first = run(calculate_meal([detected("ayam goreng", 100)], FoodMatcher([]), client))
        self.assertEqual(first.items[0].status, UNMATCHED)

        second = run(calculate_meal([detected("ayam goreng", 100)], FoodMatcher([]), client))
        self.assertEqual(second.items[0].status, COUNTED)


class MixedSourceMealTest(unittest.TestCase):
    def test_local_plus_usda_plus_unavailable(self):
        client = fake_usda(ok_handler)
        meal = run(
            calculate_meal(
                [
                    detected("nasi putih", 100),   # verified local
                    detected("ayam goreng", 100),  # USDA fallback
                    detected("sambal", 30),        # nothing anywhere
                ],
                FoodMatcher([RICE]),
                client,
            )
        )
        self.assertEqual(
            [i.source for i in meal.items], [SOURCE_LOCAL, SOURCE_USDA, ""]
        )
        self.assertEqual([i.status for i in meal.items], [COUNTED, COUNTED, UNMATCHED])
        self.assertEqual(meal.total.rounded().calories, 419)  # 130 + 289


class FactorValidationTest(unittest.TestCase):
    def test_accepts_values_in_range(self):
        self.assertEqual(valid_factor(0.7), 0.7)
        self.assertEqual(valid_factor(1), 1.0)
        self.assertEqual(valid_factor("0.55"), 0.55)

    def test_rejects_out_of_range(self):
        for bad in (0, -0.1, 1.01, 2, -1):
            with self.subTest(value=bad):
                self.assertIsNone(valid_factor(bad))

    def test_rejects_non_numeric(self):
        for bad in (None, "", "abc", [], {}):
            with self.subTest(value=bad):
                self.assertIsNone(valid_factor(bad))

    def test_record_with_invalid_factor_is_not_treated_as_verified(self):
        record = FoodRecord(
            id="x", name="X", aliases=[], calories_per_100g=1.0, protein_per_100g=1.0,
            carbs_per_100g=1.0, fat_per_100g=1.0, data_status=VERIFIED,
            edible_portion_factor=1.5, edible_portion_status=VERIFIED,
            weight_basis=GROSS,
        )
        self.assertFalse(record.has_verified_edible_portion)


class EdiblePortionTest(unittest.TestCase):
    """Only a verified factor on a gross weight may change the grams."""

    def test_verified_factor_is_applied(self):
        grams, factor, applied = resolve_edible_grams(BDD_CHICKEN, 140)
        self.assertEqual(grams, 98)  # 140 * 0.70
        self.assertEqual(factor, 0.70)
        self.assertTrue(applied)

    def test_provisional_factor_is_not_applied(self):
        grams, factor, applied = resolve_edible_grams(BDD_PROVISIONAL, 140)
        self.assertEqual(grams, 140)
        self.assertIsNone(factor)
        self.assertFalse(applied)

    def test_edible_basis_is_not_adjusted_again(self):
        grams, factor, applied = resolve_edible_grams(BDD_ALREADY_EDIBLE, 140)
        self.assertEqual(grams, 140)
        self.assertFalse(applied)

    def test_no_record_leaves_grams_unchanged(self):
        grams, factor, applied = resolve_edible_grams(None, 140)
        self.assertEqual(grams, 140)
        self.assertFalse(applied)

    def test_record_without_factor_leaves_grams_unchanged(self):
        grams, factor, applied = resolve_edible_grams(CHICKEN, 140)
        self.assertEqual(grams, 140)
        self.assertFalse(applied)

    def test_edible_grams_round_half_up(self):
        # 145 * 0.7 = 101.5, which must round up to 102.
        grams, _, _ = resolve_edible_grams(BDD_CHICKEN, 145)
        self.assertEqual(grams, 102)
        self.assertIsInstance(grams, int)

    def test_factor_of_one_is_valid_and_changes_nothing(self):
        record = FoodRecord(
            id="x", name="X", aliases=[], calories_per_100g=1.0, protein_per_100g=1.0,
            carbs_per_100g=1.0, fat_per_100g=1.0, data_status=VERIFIED,
            edible_portion_factor=1.0, edible_portion_status=VERIFIED,
            weight_basis=GROSS,
        )
        grams, factor, applied = resolve_edible_grams(record, 140)
        self.assertEqual(grams, 140)
        self.assertTrue(applied)


class EdiblePortionCalculationTest(unittest.TestCase):
    """Nutrition must be calculated from the edible grams."""

    def test_nutrition_uses_edible_grams(self):
        meal = run(
            calculate_meal([detected("ayam goreng", 140)], FoodMatcher([BDD_CHICKEN]), NO_USDA)
        )
        item = meal.items[0]
        self.assertTrue(item.edible_portion_applied)
        self.assertEqual(item.calculated_edible_grams, 98)
        # 200 kcal/100 g * 98 g = 196, not 280 from the gross weight.
        self.assertEqual(item.nutrition.rounded().calories, 196)
        self.assertEqual(meal.total.rounded().calories, 196)

    def test_original_estimate_is_preserved(self):
        meal = run(
            calculate_meal([detected("ayam goreng", 140)], FoodMatcher([BDD_CHICKEN]), NO_USDA)
        )
        item = meal.items[0]
        self.assertEqual(item.estimated_gross_grams, 140)
        self.assertEqual(item.calculated_edible_grams, 98)
        self.assertEqual(item.edible_portion_factor, 0.70)

    def test_provisional_factor_does_not_change_totals(self):
        meal = run(
            calculate_meal(
                [detected("ayam goreng", 140)], FoodMatcher([BDD_PROVISIONAL]), NO_USDA
            )
        )
        item = meal.items[0]
        self.assertFalse(item.edible_portion_applied)
        self.assertEqual(item.calculated_edible_grams, 140)
        self.assertEqual(meal.total.rounded().calories, 280)  # full 140 g

    def test_no_factor_uses_original_grams(self):
        meal = run(
            calculate_meal([detected("ayam goreng", 100)], FoodMatcher([CHICKEN]), NO_USDA)
        )
        item = meal.items[0]
        self.assertFalse(item.edible_portion_applied)
        self.assertEqual(item.estimated_gross_grams, 100)
        self.assertEqual(item.calculated_edible_grams, 100)

    def test_provenance_survives_the_adjustment(self):
        meal = run(
            calculate_meal([detected("ayam goreng", 140)], FoodMatcher([BDD_CHICKEN]), NO_USDA)
        )
        record = meal.items[0].record
        self.assertEqual(record.edible_portion_source, "Example source")
        self.assertEqual(record.edible_portion_source_reference, "Example reference")
        self.assertEqual(record.edible_portion_status, VERIFIED)

    def test_bdd_applies_on_the_usda_path_too(self):
        """The factor describes the food, not the nutrition source."""
        client = fake_usda(ok_handler)
        # Provisional nutrition (so USDA is used) but a verified BDD factor.
        record = FoodRecord(
            id="ayam_goreng", name="Ayam goreng", aliases=[],
            calories_per_100g=1.0, protein_per_100g=1.0, carbs_per_100g=1.0,
            fat_per_100g=1.0, data_status=PROVISIONAL,
            edible_portion_factor=0.5, edible_portion_status=VERIFIED,
            weight_basis=GROSS,
        )
        meal = run(calculate_meal([detected("ayam goreng", 200)], FoodMatcher([record]), client))
        item = meal.items[0]
        self.assertEqual(item.source, SOURCE_USDA)
        self.assertTrue(item.edible_portion_applied)
        self.assertEqual(item.calculated_edible_grams, 100)
        self.assertEqual(meal.total.rounded().calories, 289)

    def test_mixed_meal_with_bdd_plain_and_unavailable(self):
        meal = run(
            calculate_meal(
                [
                    detected("ayam goreng", 140),  # verified + verified BDD
                    detected("nasi putih", 100),   # verified, no BDD
                    detected("sambal", 30),        # nothing at all
                ],
                FoodMatcher([BDD_CHICKEN, RICE]),
                NO_USDA,
            )
        )
        chicken, rice, sambal = meal.items
        self.assertTrue(chicken.edible_portion_applied)
        self.assertEqual(chicken.calculated_edible_grams, 98)
        self.assertFalse(rice.edible_portion_applied)
        self.assertEqual(rice.calculated_edible_grams, 100)
        self.assertEqual(sambal.status, UNMATCHED)
        self.assertFalse(sambal.edible_portion_applied)
        self.assertEqual(sambal.estimated_gross_grams, 30)
        # 196 (98 g chicken) + 130 (100 g rice)
        self.assertEqual(meal.total.rounded().calories, 326)


class TkpiProvenanceTest(unittest.TestCase):
    """Phase 3D: verified local (TKPI-style) records vs USDA fallback."""

    VERIFIED_TKPI = FoodRecord(
        id="kol",
        name="Kol",
        aliases=["kol", "kubis"],
        calories_per_100g=29.0,
        protein_per_100g=1.4,
        carbs_per_100g=5.3,
        fat_per_100g=0.2,
        source="Tabel Komposisi Pangan Indonesia (TKPI) 2019, Kementerian Kesehatan RI",
        source_reference="Kol merah, kol putih (kode DR114)",
        source_food_code="DR114",
        source_food_name="Kol merah, kol putih",
        source_version="TKPI 2019",
        data_status=VERIFIED,
    )

    def test_verified_local_record_takes_priority_over_usda(self):
        def explode(request):
            raise AssertionError("USDA must not be called when a verified local record exists")

        client = fake_usda(explode)
        matcher = FoodMatcher([self.VERIFIED_TKPI])
        meal = run(calculate_meal([detected("kol", 100)], matcher, client))
        item = meal.items[0]
        self.assertEqual(item.status, COUNTED)
        self.assertEqual(item.source, SOURCE_LOCAL)
        self.assertEqual(client.request_count, 0)

    def test_provisional_local_record_still_falls_through_to_usda(self):
        def cabbage_handler(request):
            if "/foods/search" in request.url.path:
                return httpx.Response(
                    200,
                    json={"foods": [{"fdcId": 169975, "dataType": "SR Legacy",
                                      "description": "Cabbage, raw"}]},
                )
            return httpx.Response(200, json={
                "fdcId": 169975, "dataType": "SR Legacy", "description": "Cabbage, raw",
                "foodNutrients": [
                    {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 25.0},
                    {"nutrient": {"number": "203", "unitName": "g"}, "amount": 1.28},
                    {"nutrient": {"number": "205", "unitName": "g"}, "amount": 5.8},
                    {"nutrient": {"number": "204", "unitName": "g"}, "amount": 0.1},
                ],
            })

        provisional = FoodRecord(
            id="kol", name="Kol", aliases=["kol"],
            calories_per_100g=29.0, protein_per_100g=1.4,
            carbs_per_100g=5.3, fat_per_100g=0.2,
            source_food_code="DR114", data_status=PROVISIONAL,
        )
        client = fake_usda(cabbage_handler)
        meal = run(calculate_meal([detected("kol", 100)], FoodMatcher([provisional]), client))
        self.assertEqual(meal.items[0].source, SOURCE_USDA)
        self.assertGreater(client.request_count, 0)

    def test_source_food_code_is_preserved(self):
        meal = run(
            calculate_meal([detected("kol", 100)], FoodMatcher([self.VERIFIED_TKPI]), NO_USDA)
        )
        self.assertEqual(meal.items[0].record.source_food_code, "DR114")

    def test_source_food_name_is_preserved(self):
        meal = run(
            calculate_meal([detected("kol", 100)], FoodMatcher([self.VERIFIED_TKPI]), NO_USDA)
        )
        self.assertEqual(meal.items[0].record.source_food_name, "Kol merah, kol putih")

    def test_source_version_is_preserved(self):
        meal = run(
            calculate_meal([detected("kol", 100)], FoodMatcher([self.VERIFIED_TKPI]), NO_USDA)
        )
        self.assertEqual(meal.items[0].record.source_version, "TKPI 2019")

    def test_provenance_fields_default_to_empty_for_backward_compatibility(self):
        """Existing records with no Phase 3D fields must keep working unchanged."""
        record = FoodRecord(
            id="x", name="X", aliases=[], calories_per_100g=1.0,
            protein_per_100g=1.0, carbs_per_100g=1.0, fat_per_100g=1.0,
            data_status=VERIFIED,
        )
        self.assertEqual(record.source_food_code, "")
        self.assertEqual(record.source_food_name, "")
        self.assertEqual(record.source_version, "")
        meal = run(calculate_meal([detected("X", 100)], FoodMatcher([record]), NO_USDA))
        self.assertEqual(meal.items[0].status, COUNTED)

    def test_verified_local_nutrition_calculation_is_deterministic(self):
        matcher = FoodMatcher([self.VERIFIED_TKPI])
        results = [
            run(calculate_meal([detected("kol", 140)], matcher, NO_USDA)).total.rounded().calories
            for _ in range(5)
        ]
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(results[0], 41)  # 29 * 1.4 = 40.6 -> 41

    def test_ambiguous_indonesian_food_remains_provisional(self):
        """A food with multiple non-equivalent source candidates must not be verified."""
        # Simulates e.g. tempe (mentah vs goreng) or ayam goreng (branded/regional
        # variants only): the record exists, but investigation could not defensibly
        # pick one candidate, so it must stay provisional.
        ambiguous = FoodRecord(
            id="tempe", name="Tempe", aliases=["tempe"],
            calories_per_100g=192.0, protein_per_100g=20.3,
            carbs_per_100g=7.6, fat_per_100g=10.8,
            data_status=PROVISIONAL,  # NOT promoted despite candidate data existing
        )
        meal = run(calculate_meal([detected("tempe", 100)], FoodMatcher([ambiguous]), NO_USDA))
        item = meal.items[0]
        self.assertEqual(item.status, UNVERIFIED)
        self.assertFalse(item.counted)
        self.assertEqual(meal.total.rounded().calories, 0)

    def test_verified_bdd_provenance_is_preserved_even_when_not_applied(self):
        """A verified BDD factor can be recorded for provenance without being
        activated, when weight_basis keeps it inert (gross/edible uncertainty)."""
        record = FoodRecord(
            id="kol", name="Kol", aliases=["kol"],
            calories_per_100g=29.0, protein_per_100g=1.4,
            carbs_per_100g=5.3, fat_per_100g=0.2,
            data_status=VERIFIED,
            edible_portion_factor=0.75,
            edible_portion_status=VERIFIED,
            weight_basis=EDIBLE,  # deliberately not GROSS
            edible_portion_source="TKPI 2019",
            edible_portion_source_reference="DR114, BDD 75%",
        )
        self.assertFalse(record.has_verified_edible_portion)  # never auto-applied
        meal = run(calculate_meal([detected("kol", 100)], FoodMatcher([record]), NO_USDA))
        item = meal.items[0]
        self.assertFalse(item.edible_portion_applied)
        self.assertEqual(item.calculated_edible_grams, 100)
        # But the provenance itself survives on the resolved record.
        self.assertEqual(item.record.edible_portion_status, VERIFIED)
        self.assertEqual(item.record.edible_portion_factor, 0.75)
        self.assertEqual(item.record.edible_portion_source_reference, "DR114, BDD 75%")

    def test_usda_fallback_behavior_is_unchanged_by_local_priority_foods(self):
        """Adding verified local records for some foods must not affect USDA
        fallback for foods that still have no local match at all."""
        client = fake_usda(ok_handler)
        matcher = FoodMatcher([self.VERIFIED_TKPI])  # only "kol" has a local record
        meal = run(calculate_meal([detected("ayam goreng", 100)], matcher, client))
        self.assertEqual(meal.items[0].source, SOURCE_USDA)
        self.assertEqual(meal.items[0].usda.fdc_id, 171448)


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

    def test_shipped_provisional_records_never_count_locally(self):
        """Provisional records must not contribute via the local path."""
        for record in self.matcher.records:
            if record.is_verified:
                continue
            meal = run(
                calculate_meal([detected(record.name, 100)], self.matcher, NO_USDA)
            )
            with self.subTest(food=record.id):
                self.assertNotEqual(meal.items[0].source, SOURCE_LOCAL)
                self.assertEqual(meal.total.rounded().calories, 0)

    def test_shipped_verified_records_cite_full_tkpi_provenance(self):
        # Phase 3D.1 audit: only nasi_putih and kol were independently confirmed
        # against the official Kemenkes PDF. "kemangi" was removed after the
        # audit found its DR039 code actually names a different plant
        # ("Daun kemang", not "Daun kemangi") - see data/foods.json history.
        verified_ids = {"nasi_putih", "kol"}
        for record in self.matcher.records:
            if record.id in verified_ids:
                with self.subTest(food=record.id):
                    self.assertEqual(record.data_status, VERIFIED)
                    self.assertTrue(record.source_food_code)
                    self.assertTrue(record.source_food_name)
                    self.assertTrue(record.source_version)

    def test_shipped_ambiguous_foods_remain_provisional(self):
        ambiguous_ids = {
            "ayam_goreng", "telur_rebus", "telur_goreng", "tempe", "tahu", "pisang",
        }
        for record in self.matcher.records:
            if record.id in ambiguous_ids:
                with self.subTest(food=record.id):
                    self.assertEqual(record.data_status, PROVISIONAL)

    def test_no_record_is_named_kemangi(self):
        """Regression guard: DR039 is "Daun kemang", not kemangi - no record
        should claim a verified kemangi match without an independently
        located official source."""
        for record in self.matcher.records:
            with self.subTest(food=record.id):
                self.assertNotEqual(normalize(record.name), "kemangi")

    def test_verified_bdd_factor_must_cite_a_source(self):
        for record in self.matcher.records:
            with self.subTest(food=record.id):
                if record.has_verified_edible_portion:
                    self.assertTrue(
                        record.edible_portion_source.strip(),
                        "a verified BDD factor must cite its source",
                    )

    def test_every_record_declares_a_valid_weight_basis(self):
        for record in self.matcher.records:
            with self.subTest(food=record.id):
                self.assertIn(record.weight_basis, (GROSS, EDIBLE))
                self.assertIn(
                    record.edible_portion_status,
                    (VERIFIED, PROVISIONAL, NOT_APPLICABLE),
                )

    def test_ids_are_unique(self):
        ids = [r.id for r in self.matcher.records]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
