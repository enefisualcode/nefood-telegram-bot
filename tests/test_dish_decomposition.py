"""Phase 3F: vision-aware Indonesian dish decomposition tests.

Covers the full Phase 3F test plan: simple-food behaviour is unchanged,
compound dishes resolve through DishMatcher, only visible components ever
reach the nutrition pipeline, hidden ingredients/nutrition/BDD values from
Gemini are never trusted, nasi padang stays a variable category, bare
martabak stays ambiguous while telur/manis stay distinct, unknown dishes and
malformed vision output fail safely, and the confirmation gate + existing
local -> USDA -> unavailable pipeline are both untouched.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
from services.dish_decomposition import DecomposedMeal, decompose
from services.dish_matcher import DishMatcher, DishTemplate, get_matcher
from services.food_vision import (
    DISH_TYPE_AMBIGUOUS,
    DISH_TYPE_COMPOUND,
    DISH_TYPE_SIMPLE,
    DISH_TYPE_VARIABLE,
    DetectedFood,
    FoodAnalysis,
    _parse,
)
from services.nutrition_calculator import MealNutrition, calculate_meal
from services.usda_food_data import UsdaClient


def run(coro):
    return asyncio.run(coro)


def detected(name, grams, **overrides) -> DetectedFood:
    defaults = dict(identification_confidence=0.9, portion_confidence=0.6, serving_label="")
    defaults.update(overrides)
    return DetectedFood(name=name, estimated_grams=grams, **defaults)


MATCHER = get_matcher()  # the real, shipped dish_templates.json
NO_USDA = UsdaClient(api_key="")


class SimpleFoodUnchangedTest(unittest.TestCase):
    """1. simple food behavior remains unchanged."""

    def test_no_dish_name_decomposes_to_simple_with_untouched_foods(self):
        analysis = FoodAnalysis(foods=[detected("nasi putih", 100), detected("ayam goreng", 120)])
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_SIMPLE)
        self.assertTrue(decomposed.is_simple)
        self.assertEqual(decomposed.dish_name, "")
        self.assertEqual(decomposed.foods, analysis.foods)

    def test_format_analysis_simple_layout_is_unchanged(self):
        analysis = FoodAnalysis(foods=[detected("nasi putih", 100, serving_label="1 centong")])
        text = bot.format_analysis(analysis, dish_matcher=MATCHER)
        self.assertIn("🔍 Makanan terdeteksi:", text)
        self.assertIn("🍽 Nasi putih", text)
        self.assertIn("Porsi: ±100 g (1 centong)", text)
        self.assertIn("Identifikasi:", text)
        self.assertIn("Estimasi porsi:", text)
        self.assertNotIn(bot.VISIBLE_COMPONENTS_LABEL, text)


class PecelLeleCompoundResolutionTest(unittest.TestCase):
    """2. pecel lele can resolve to compound dish."""

    def test_pecel_lele_dish_name_resolves_to_compound(self):
        analysis = FoodAnalysis(
            foods=[detected("lele goreng", 120), detected("sambal", 25)],
            dish_name="pecel lele", dish_type="compound",
        )
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_COMPOUND)
        self.assertEqual(decomposed.dish_name, "Pecel lele")
        self.assertIsNotNone(decomposed.template)
        self.assertEqual(decomposed.template.id, "pecel_lele")


class OnlyVisibleComponentsTest(unittest.TestCase):
    """3. only visible components are returned. 4. template-only invisible
    component is not added. 5. hidden recipe ingredients are never invented."""

    def test_decomposed_foods_are_exactly_the_reported_foods(self):
        visible = [detected("lele goreng", 120), detected("sambal", 25), detected("kol", 20), detected("timun", 15)]
        analysis = FoodAnalysis(foods=visible, dish_name="pecel lele", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.foods, visible)
        self.assertEqual(len(decomposed.foods), 4)

    def test_missing_template_component_is_never_injected(self):
        # The pecel_lele template lists 5 core components (incl. kemangi);
        # Gemini only reported 4 (no kemangi visible in this photo).
        visible = [detected("lele goreng", 120), detected("sambal", 25), detected("kol", 20), detected("timun", 15)]
        analysis = FoodAnalysis(foods=visible, dish_name="pecel lele", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        names = {f.name for f in decomposed.foods}
        self.assertNotIn("kemangi", names)
        template_component_names = {c.typical_food for c in decomposed.template.components}
        self.assertIn("kemangi", template_component_names)  # the template DOES list it...
        self.assertNotIn("kemangi", names)                  # ...but it was never added to the result

    def test_empty_food_list_stays_empty_even_for_a_known_dish(self):
        analysis = FoodAnalysis(foods=[], dish_name="pecel lele", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.foods, [])

    def test_rendered_message_never_lists_an_invisible_component(self):
        visible = [detected("lele goreng", 120), detected("sambal", 25)]
        analysis = FoodAnalysis(foods=visible, dish_name="pecel lele", dish_type="compound")
        text = bot.format_analysis(analysis, dish_matcher=MATCHER)
        self.assertIn("Lele goreng", text)
        self.assertIn("Sambal", text)
        for invisible in ("Kol", "Timun", "Kemangi"):
            self.assertNotIn(invisible, text)

    def test_decomposition_module_has_no_code_path_that_expands_foods(self):
        """A template with more components than were detected must never
        cause the output list to grow relative to the input."""
        analysis = FoodAnalysis(foods=[detected("nasi uduk", 150)], dish_name="nasi uduk", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(len(decomposed.foods), 1)  # nasi_uduk template has 7 components; input had 1


class NasiPadangVariableTest(unittest.TestCase):
    """6. nasi padang is handled as variable meal."""

    def test_nasi_padang_is_variable_not_compound(self):
        visible = [detected("nasi putih", 150), detected("rendang", 100), detected("daun singkong", 40), detected("sambal", 20)]
        analysis = FoodAnalysis(foods=visible, dish_name="nasi padang", dish_type="variable")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_VARIABLE)
        self.assertEqual(decomposed.foods, visible)

    def test_nasi_padang_never_becomes_a_single_generic_entry(self):
        visible = [detected("nasi putih", 150), detected("rendang", 100)]
        analysis = FoodAnalysis(foods=visible, dish_name="nasi padang", dish_type="variable")
        decomposed = decompose(analysis, MATCHER)
        names = [f.name for f in decomposed.foods]
        self.assertNotIn("nasi padang", names)
        self.assertEqual(names, ["nasi putih", "rendang"])

    def test_nasi_padang_even_if_model_mislabels_dish_type_as_compound(self):
        # Classification comes from OUR DishMatcher, not the model's own
        # (possibly wrong) dish_type guess.
        analysis = FoodAnalysis(foods=[detected("nasi putih", 150)], dish_name="nasi padang", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_VARIABLE)


class BareMartabakAmbiguousTest(unittest.TestCase):
    """7. bare martabak remains ambiguous."""

    def test_bare_martabak_stays_ambiguous(self):
        analysis = FoodAnalysis(foods=[detected("martabak", 200)], dish_name="martabak", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_AMBIGUOUS)
        self.assertTrue(decomposed.needs_clarification)

    def test_ambiguous_martabak_is_never_silently_resolved_even_if_model_guesses(self):
        # Even if the model's dish_type says "compound", a bare "martabak"
        # dish_name must still classify as ambiguous.
        analysis = FoodAnalysis(foods=[detected("martabak", 200)], dish_name="martabak", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_AMBIGUOUS)

    def test_ambiguous_rendering_shows_a_clarification_note(self):
        analysis = FoodAnalysis(foods=[detected("martabak", 200)], dish_name="martabak", dish_type="compound")
        text = bot.format_analysis(analysis, dish_matcher=MATCHER)
        self.assertIn("❓", text)
        self.assertIn("belum dapat dipastikan", text)


class MartabakTelurManisDistinctTest(unittest.TestCase):
    """8. martabak telur/manis remain distinct."""

    def test_martabak_telur_resolves_to_its_own_compound_template(self):
        analysis = FoodAnalysis(foods=[detected("kulit martabak", 100), detected("telur", 60)], dish_name="martabak telur", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_COMPOUND)
        self.assertEqual(decomposed.template.id, "martabak_telur")

    def test_martabak_manis_resolves_to_a_different_compound_template(self):
        analysis = FoodAnalysis(foods=[detected("adonan martabak manis", 150)], dish_name="martabak manis", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_COMPOUND)
        self.assertEqual(decomposed.template.id, "martabak_manis")

    def test_telur_and_manis_never_share_a_template(self):
        telur = decompose(FoodAnalysis(foods=[], dish_name="martabak telur"), MATCHER)
        manis = decompose(FoodAnalysis(foods=[], dish_name="martabak manis"), MATCHER)
        self.assertNotEqual(telur.template.id, manis.template.id)


class UnknownDishFallsBackSafelyTest(unittest.TestCase):
    """9. unknown dish falls back safely."""

    def test_unrecognized_dish_name_falls_back_to_simple(self):
        analysis = FoodAnalysis(foods=[detected("hidangan misterius", 100)], dish_name="hidangan yang tidak dikenal", dish_type="compound")
        decomposed = decompose(analysis, MATCHER)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_SIMPLE)
        self.assertEqual(decomposed.foods, analysis.foods)

    def test_matcher_raising_does_not_propagate(self):
        broken_matcher = MagicMock()
        broken_matcher.match.side_effect = RuntimeError("boom")
        analysis = FoodAnalysis(foods=[detected("apa saja", 50)], dish_name="apa saja")
        decomposed = decompose(analysis, broken_matcher)
        self.assertEqual(decomposed.dish_type, DISH_TYPE_SIMPLE)
        self.assertEqual(decomposed.foods, analysis.foods)


class MalformedVisionOutputFailsSafelyTest(unittest.TestCase):
    """10. malformed vision output fails safely."""

    def test_missing_foods_key_still_raises_food_vision_error(self):
        from services.food_vision import FoodVisionError
        with self.assertRaises(FoodVisionError):
            _parse('{"notes": "no foods key at all"}')

    def test_invalid_dish_type_falls_back_to_simple_rather_than_raising(self):
        analysis = _parse('{"foods": [], "dish_name": "sesuatu", "dish_type": "not_a_real_type"}')
        self.assertEqual(analysis.dish_type, DISH_TYPE_SIMPLE)

    def test_non_object_json_still_raises(self):
        from services.food_vision import FoodVisionError
        with self.assertRaises(FoodVisionError):
            _parse("[1, 2, 3]")


class GeminiNutritionValuesIgnoredTest(unittest.TestCase):
    """11. Gemini nutrition values are ignored/rejected."""

    def test_nutrition_fields_on_a_food_item_are_dropped(self):
        payload = (
            '{"foods": [{"name": "nasi goreng", "estimated_grams": 200, '
            '"identification_confidence": 0.9, "portion_confidence": 0.7, '
            '"calories_per_100g": 999, "protein": 50, "fat": 40, "carbs": 80}]}'
        )
        analysis = _parse(payload)
        food = analysis.foods[0]
        self.assertFalse(hasattr(food, "calories_per_100g"))
        self.assertFalse(hasattr(food, "protein"))
        self.assertFalse(hasattr(food, "fat"))
        self.assertFalse(hasattr(food, "carbs"))
        self.assertEqual(food.name, "nasi goreng")

    def test_top_level_nutrition_fields_are_ignored(self):
        payload = '{"foods": [], "calories": 500, "total_kcal": 500}'
        analysis = _parse(payload)
        self.assertFalse(hasattr(analysis, "calories"))
        self.assertFalse(hasattr(analysis, "total_kcal"))


class GeminiBddValuesIgnoredTest(unittest.TestCase):
    """12. Gemini BDD values are ignored/rejected."""

    def test_bdd_field_on_a_food_item_is_dropped(self):
        payload = (
            '{"foods": [{"name": "kol", "estimated_grams": 30, '
            '"identification_confidence": 0.8, "portion_confidence": 0.6, '
            '"bdd_percent": 75, "edible_portion_factor": 0.75}]}'
        )
        analysis = _parse(payload)
        food = analysis.foods[0]
        self.assertFalse(hasattr(food, "bdd_percent"))
        self.assertFalse(hasattr(food, "edible_portion_factor"))

    def test_bdd_never_reaches_the_nutrition_calculation(self):
        # Even if the model tried to smuggle a BDD value in, resolve_food
        # only ever consults the LOCAL FoodRecord's own verified BDD - the
        # DetectedFood object it receives has no BDD field to read from.
        from services.food_matcher import FoodMatcher
        payload = (
            '{"foods": [{"name": "kol", "estimated_grams": 100, '
            '"identification_confidence": 0.8, "portion_confidence": 0.6, '
            '"bdd_percent": 1}]}'
        )
        analysis = _parse(payload)
        meal = run(calculate_meal(analysis.foods, FoodMatcher([]), NO_USDA))
        # No local/USDA record for "kol" in this empty matcher -> unmatched,
        # proving no BDD-based shortcut ever fired.
        self.assertEqual(meal.total.rounded().calories, 0)


class ConfirmationGateTest(unittest.TestCase):
    """13. existing confirmation step remains required.
    14. nutrition is not calculated before confirmation."""

    def _photo_update(self):
        photo_file = MagicMock()
        photo_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"fake-bytes"))
        photo = MagicMock()
        photo.get_file = AsyncMock(return_value=photo_file)
        message = MagicMock()
        message.photo = [photo]
        message.reply_text = AsyncMock()
        message.chat = MagicMock()
        message.chat.send_action = AsyncMock()
        update = MagicMock()
        update.message = message
        update.effective_user = MagicMock(id=1)
        return update

    def _callback_update(self, data):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.reply_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_user = MagicMock(id=1)
        return update

    def test_handle_photo_never_calculates_nutrition(self):
        analysis = FoodAnalysis(foods=[detected("nasi putih", 100)])
        update = self._photo_update()
        context = MagicMock()
        context.user_data = {}
        with patch("bot.analyze_food_image", new=AsyncMock(return_value=analysis)), \
             patch("bot.calculate_meal", new=AsyncMock()) as calc_mock:
            run(bot.handle_photo(update, context))
        calc_mock.assert_not_called()
        self.assertIs(context.user_data[bot.LAST_ANALYSIS_KEY], analysis)

    def test_correct_callback_does_not_calculate_nutrition(self):
        update = self._callback_update(bot.CORRECT_CALLBACK)
        context = MagicMock()
        context.user_data = {}
        with patch("bot.calculate_meal", new=AsyncMock()) as calc_mock:
            run(bot.handle_portion_callback(update, context))
        calc_mock.assert_not_called()

    def test_confirm_callback_without_stored_analysis_does_not_calculate(self):
        update = self._callback_update(bot.CONFIRM_CALLBACK)
        context = MagicMock()
        context.user_data = {}  # nothing stored - e.g. bot restarted
        with patch("bot.calculate_meal", new=AsyncMock()) as calc_mock:
            run(bot.handle_portion_callback(update, context))
        calc_mock.assert_not_called()


class ConfirmedComponentsUseExistingPipelineTest(unittest.TestCase):
    """15. confirmed components use existing nutrition pipeline."""

    def _callback_update(self):
        query = MagicMock()
        query.data = bot.CONFIRM_CALLBACK
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.reply_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_user = MagicMock(id=1)
        return update

    def test_confirm_calls_calculate_meal_with_exactly_the_stored_foods(self):
        visible = [detected("lele goreng", 120), detected("sambal", 25)]
        analysis = FoodAnalysis(foods=visible, dish_name="pecel lele", dish_type="compound")
        context = MagicMock()
        context.user_data = {bot.LAST_ANALYSIS_KEY: analysis}
        update = self._callback_update()

        with patch("bot.calculate_meal", new=AsyncMock(return_value=MealNutrition())) as calc_mock:
            run(bot.handle_portion_callback(update, context))

        calc_mock.assert_awaited_once()
        (foods_arg, *_rest), _kwargs = calc_mock.await_args
        self.assertEqual(foods_arg, visible)  # exactly what was shown, nothing added


class ExistingSourcePriorityStillWorksTest(unittest.TestCase):
    """16. provisional local still falls through to USDA."""

    def test_provisional_local_record_still_allows_usda_fallback(self):
        import httpx
        from services.food_matcher import PROVISIONAL, FoodMatcher, FoodRecord

        provisional = FoodRecord(
            id="ayam_goreng", name="Ayam goreng", aliases=["ayam goreng"],
            calories_per_100g=279.0, protein_per_100g=27.1, carbs_per_100g=8.4, fat_per_100g=15.2,
            data_status=PROVISIONAL,
        )

        def ok_handler(request):
            if "/foods/search" in request.url.path:
                return httpx.Response(200, json={"foods": [
                    {"fdcId": 171448, "dataType": "SR Legacy",
                     "description": "Chicken, broilers or fryers, meat and skin, cooked, fried, batter"}
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
        meal = run(calculate_meal([detected("ayam goreng", 100)], FoodMatcher([provisional]), client))
        self.assertEqual(meal.items[0].source, "usda")
        self.assertEqual(meal.total.rounded().calories, 289)


if __name__ == "__main__":
    unittest.main()
