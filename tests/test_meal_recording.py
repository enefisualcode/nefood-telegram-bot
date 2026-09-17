"""Meal text input, correction, confirmation and persistent storage tests."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from services.food_vision import FoodAnalysis
from services.food_matcher import FoodRecord
from services.meal_input import MealInputError, parse_meal_text
from services.meal_store import MealStore
from services.nutrition_calculator import (
    COUNTED,
    UNMATCHED,
    FoodNutrition,
    MealNutrition,
    Nutrition,
)


def counted_meal(name="nasi putih", grams=150) -> MealNutrition:
    nutrition = Nutrition(calories=270, protein=4.5, carbs=59.7, fat=0.45)
    item = FoodNutrition(
        name=name,
        estimated_gross_grams=grams,
        calculated_edible_grams=grams,
        status=COUNTED,
        nutrition=nutrition,
        source="local",
    )
    return MealNutrition(items=[item], total=nutrition)


def text_update(user_id: int, text: str):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=message,
        effective_message=message,
    )


def callback_update(user_id: int, data: str):
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(
        data=data,
        message=message,
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        callback_query=query,
        effective_message=message,
    )


class MealTextParserTest(unittest.TestCase):
    def test_parses_food_with_grams(self):
        foods = parse_meal_text("nasi putih 150 gram")
        self.assertEqual([(f.name, f.estimated_grams) for f in foods], [("nasi putih", 150)])

    def test_parses_multiple_foods(self):
        foods = parse_meal_text("nasi putih 150 gram; ayam goreng 100 g")
        self.assertEqual(len(foods), 2)
        self.assertEqual(foods[1].estimated_grams, 100)

    def test_two_boiled_eggs_uses_explicit_conversion(self):
        food = parse_meal_text("2 telur rebus")[0]
        self.assertEqual(food.estimated_grams, 100)
        self.assertIn("2 butir", food.serving_label)

    def test_ambiguous_count_is_rejected(self):
        with self.assertRaises(MealInputError):
            parse_meal_text("2 ayam goreng")

    def test_missing_portion_is_rejected(self):
        with self.assertRaises(MealInputError):
            parse_meal_text("nasi putih")


class MealStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "nutrufood.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_saved_meal_survives_restart(self):
        MealStore(self.path).save(101, "text", counted_meal())
        restarted = MealStore(self.path)
        meals = restarted.get_for_user(101)
        self.assertEqual(len(meals), 1)
        self.assertEqual(meals[0].input_source, "text")
        self.assertEqual(meals[0].items[0].food_name, "nasi putih")
        self.assertEqual(meals[0].items[0].calories, 270)

    def test_two_users_cannot_read_each_others_meals(self):
        store = MealStore(self.path)
        store.save(101, "photo", counted_meal("nasi putih"))
        store.save(202, "text", counted_meal("kol", 50))
        self.assertEqual(store.get_for_user(101)[0].items[0].food_name, "nasi putih")
        self.assertEqual(store.get_for_user(202)[0].items[0].food_name, "kol")
        self.assertEqual(store.get_for_user(999), [])

    def test_unavailable_nutrition_is_stored_as_null_not_zero(self):
        unavailable = FoodNutrition(
            name="urap sayur",
            estimated_gross_grams=100,
            calculated_edible_grams=100,
            status=UNMATCHED,
        )
        store = MealStore(self.path)
        store.save(101, "text", MealNutrition(items=[unavailable]))
        item = store.get_for_user(101)[0].items[0]
        self.assertEqual(item.nutrition_status, UNMATCHED)
        self.assertIsNone(item.calories)
        self.assertIsNone(item.protein)

    def test_nutrition_source_and_reference_are_preserved(self):
        record = FoodRecord(
            id="nasi_putih",
            name="Nasi putih",
            aliases=["nasi putih"],
            calories_per_100g=180,
            protein_per_100g=3,
            carbs_per_100g=39.8,
            fat_per_100g=0.3,
            source="TKPI 2020",
            source_reference="AP001",
            data_status="verified",
        )
        nutrition = Nutrition(calories=180, protein=3, carbs=39.8, fat=0.3)
        item = FoodNutrition(
            name="nasi putih",
            estimated_gross_grams=100,
            calculated_edible_grams=100,
            status=COUNTED,
            nutrition=nutrition,
            source="local",
            record=record,
        )
        store = MealStore(self.path)
        store.save(101, "photo", MealNutrition(items=[item], total=nutrition))
        stored = store.get_for_user(101)[0].items[0]
        self.assertEqual(stored.nutrition_source, "TKPI 2020")
        self.assertEqual(stored.source_reference, "AP001")


class MealFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MealStore(Path(self.temp.name) / "nutrufood.db")
        self.store_patch = patch("bot.get_meal_store", return_value=self.store)
        self.store_patch.start()

    async def asyncTearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    async def test_text_input_creates_review_without_saving(self):
        request = text_update(101, "nasi putih 150 gram")
        context = SimpleNamespace(user_data={})
        meal = counted_meal()
        with patch("bot.calculate_meal", new=AsyncMock(return_value=meal)):
            await bot.handle_meal_text(request, context)
        self.assertIs(context.user_data[bot.MEAL_NUTRITION_KEY], meal)
        self.assertEqual(context.user_data[bot.MEAL_INPUT_SOURCE_KEY], "text")
        self.assertEqual(self.store.get_for_user(101), [])
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("Simpan catatan", reply)
        self.assertIn("🍽 Ringkasan Makanan", reply)
        self.assertIn("📊 Estimasi Nutrisi", reply)

    async def test_correction_changes_name_and_portion(self):
        request = text_update(101, "ayam goreng 100 gram")
        context = SimpleNamespace(
            user_data={
                bot.MEAL_AWAITING_CORRECTION_KEY: True,
                bot.MEAL_INPUT_SOURCE_KEY: "photo",
            }
        )
        with patch("bot.calculate_meal", new=AsyncMock(return_value=counted_meal())) as calc:
            await bot.handle_meal_text(request, context)
        foods = calc.await_args.args[0]
        self.assertEqual(foods[0].name, "ayam goreng")
        self.assertEqual(foods[0].estimated_grams, 100)
        self.assertEqual(context.user_data[bot.MEAL_INPUT_SOURCE_KEY], "photo")

    async def test_save_button_persists_confirmed_meal(self):
        context = SimpleNamespace(
            user_data={
                bot.MEAL_NUTRITION_KEY: counted_meal(),
                bot.MEAL_INPUT_SOURCE_KEY: "text",
                bot.LAST_ANALYSIS_KEY: FoodAnalysis(foods=[]),
            }
        )
        request = callback_update(101, bot.MEAL_SAVE_CALLBACK)
        await bot.handle_meal_review_callback(request, context)
        self.assertEqual(len(self.store.get_for_user(101)), 1)
        self.assertNotIn(bot.MEAL_NUTRITION_KEY, context.user_data)

    async def test_cancel_button_does_not_save(self):
        context = SimpleNamespace(
            user_data={bot.MEAL_NUTRITION_KEY: counted_meal(), bot.MEAL_INPUT_SOURCE_KEY: "photo"}
        )
        request = callback_update(101, bot.MEAL_CANCEL_CALLBACK)
        await bot.handle_meal_review_callback(request, context)
        self.assertEqual(self.store.get_for_user(101), [])
        self.assertEqual(context.user_data, {})

    async def test_correction_button_waits_for_replacement_text(self):
        context = SimpleNamespace(user_data={bot.MEAL_NUTRITION_KEY: counted_meal()})
        request = callback_update(101, bot.MEAL_CORRECT_CALLBACK)
        await bot.handle_meal_review_callback(request, context)
        self.assertTrue(context.user_data[bot.MEAL_AWAITING_CORRECTION_KEY])
        self.assertEqual(self.store.get_for_user(101), [])


if __name__ == "__main__":
    unittest.main()
