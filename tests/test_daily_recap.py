"""Daily recap aggregation, timezone and /hariini tests."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import bot
from services.daily_recap import JAKARTA, build_daily_recap, jakarta_day_bounds
from services.meal_store import MealStore
from services.nutrition_calculator import COUNTED, UNMATCHED, FoodNutrition, MealNutrition, Nutrition
from services.profile_store import ProfileStore, UserProfile


NOW = datetime(2026, 9, 17, 12, 0, tzinfo=JAKARTA)


def known_meal(name="nasi putih", grams=100, calories=180) -> MealNutrition:
    nutrition = Nutrition(calories=calories, protein=3, carbs=39.8, fat=0.3)
    item = FoodNutrition(
        name=name,
        estimated_gross_grams=grams,
        calculated_edible_grams=grams,
        status=COUNTED,
        nutrition=nutrition,
        source="local",
    )
    return MealNutrition(items=[item], total=nutrition)


def unknown_meal(name="urap sayur", grams=100) -> MealNutrition:
    return MealNutrition(
        items=[
            FoodNutrition(
                name=name,
                estimated_gross_grams=grams,
                calculated_edible_grams=grams,
                status=UNMATCHED,
            )
        ]
    )


def profile(user_id=1) -> UserProfile:
    return UserProfile(
        telegram_user_id=user_id,
        age=30,
        gender="Laki-laki",
        height_cm=170,
        weight_kg=70,
        activity_level="Ringan",
        goal="Mempertahankan berat badan",
    )


class DailyRecapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "nutrufood.db"
        self.store = MealStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_one_food_today(self):
        self.store.save(1, "text", known_meal(), NOW.replace(hour=8))
        recap = build_daily_recap(1, self.store, NOW)
        self.assertEqual(recap.total_items, 1)
        self.assertEqual(recap.calories, 180)
        self.assertEqual(recap.entries[0].eaten_at_local.strftime("%H:%M"), "08:00")

    def test_multiple_foods_same_day_are_summed(self):
        self.store.save(1, "text", known_meal(calories=180), NOW.replace(hour=8))
        self.store.save(1, "photo", known_meal("kol", 50, 15), NOW.replace(hour=12, minute=30))
        recap = build_daily_recap(1, self.store, NOW)
        self.assertEqual(recap.total_items, 2)
        self.assertEqual(recap.calories, 195)
        self.assertEqual(recap.protein, 6)

    def test_other_day_is_excluded(self):
        self.store.save(1, "text", known_meal("kemarin"), NOW - timedelta(days=1))
        self.store.save(1, "text", known_meal("hari ini"), NOW)
        recap = build_daily_recap(1, self.store, NOW)
        self.assertEqual([entry.name for entry in recap.entries], ["hari ini"])

    def test_two_users_do_not_share_recap(self):
        self.store.save(1, "text", known_meal("milik satu"), NOW)
        self.store.save(2, "text", known_meal("milik dua"), NOW)
        self.assertEqual(build_daily_recap(1, self.store, NOW).entries[0].name, "milik satu")
        self.assertEqual(build_daily_recap(2, self.store, NOW).entries[0].name, "milik dua")

    def test_unavailable_nutrition_is_none_not_zero(self):
        self.store.save(1, "text", unknown_meal(), NOW)
        recap = build_daily_recap(1, self.store, NOW)
        self.assertEqual(recap.total_items, 1)
        self.assertEqual(recap.known_nutrition_items, 0)
        self.assertIsNone(recap.calories)
        self.assertIsNone(recap.protein)

    def test_partial_nutrition_is_explicit(self):
        self.store.save(1, "text", known_meal(), NOW)
        self.store.save(1, "text", unknown_meal(), NOW)
        recap = build_daily_recap(1, self.store, NOW)
        self.assertTrue(recap.has_partial_nutrition)
        self.assertEqual(recap.known_nutrition_items, 1)
        self.assertEqual(recap.total_items, 2)

    def test_restart_does_not_remove_recap(self):
        self.store.save(1, "photo", known_meal(), NOW)
        restarted = MealStore(self.path)
        self.assertEqual(build_daily_recap(1, restarted, NOW).calories, 180)

    def test_jakarta_day_boundary_is_used(self):
        start, end, local_date = jakarta_day_bounds(NOW)
        self.assertEqual(start, datetime(2026, 9, 16, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 17, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(str(local_date), "2026-09-17")


class TodayCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "nutrufood.db"
        self.meals = MealStore(path)
        self.profiles = ProfileStore(path)
        self.meal_patch = patch("bot.get_meal_store", return_value=self.meals)
        self.profile_patch = patch("bot.get_profile_store", return_value=self.profiles)
        self.meal_patch.start()
        self.profile_patch.start()

    async def asyncTearDown(self):
        self.profile_patch.stop()
        self.meal_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def update(user_id=1):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    async def test_missing_profile_points_to_setup(self):
        request = self.update()
        await bot.today_command(request, SimpleNamespace(user_data={}))
        self.assertIn("/setup", request.message.reply_text.await_args.args[0])

    async def test_empty_today_has_friendly_message(self):
        self.profiles.save(profile())
        request = self.update()
        await bot.today_command(request, SimpleNamespace(user_data={}))
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("Belum ada makanan", reply)
        self.assertIn("/catat", reply)

    async def test_command_compares_known_total_with_target(self):
        self.profiles.save(profile())
        self.meals.save(1, "text", known_meal(), datetime.now(timezone.utc))
        request = self.update()
        await bot.today_command(request, SimpleNamespace(user_data={}))
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("180 /", reply)
        self.assertIn("Nasi putih", reply)
        self.assertIn("Asia/Jakarta", reply)
        self.assertEqual(reply.count("💡 Saran hari ini:"), 1)

    async def test_command_labels_unknown_nutrition_instead_of_showing_zero(self):
        self.profiles.save(profile())
        self.meals.save(1, "text", unknown_meal(), datetime.now(timezone.utc))
        request = self.update()
        await bot.today_command(request, SimpleNamespace(user_data={}))
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("belum tersedia / target", reply)
        self.assertIn("tidak dianggap nol", reply)


if __name__ == "__main__":
    unittest.main()
