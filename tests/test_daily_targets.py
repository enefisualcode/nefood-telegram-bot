"""Personal daily target calculation and /target command tests."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from services.daily_targets import (
    TargetCalculationError,
    calculate_daily_targets,
    resting_energy,
)
from services.profile_store import ProfileStore, UserProfile


def profile(**overrides) -> UserProfile:
    values = dict(
        telegram_user_id=1,
        age=30,
        gender="Laki-laki",
        height_cm=170,
        weight_kg=70,
        activity_level="Sedang",
        goal="Mempertahankan berat badan",
    )
    values.update(overrides)
    return UserProfile(**values)


def update(user_id: int):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


class EnergyCalculationTest(unittest.TestCase):
    def test_mifflin_st_jeor_male(self):
        # 10*70 + 6.25*170 - 5*30 + 5 = 1617.5
        self.assertAlmostEqual(resting_energy(profile()), 1617.5)

    def test_mifflin_st_jeor_female(self):
        female = profile(gender="Perempuan")
        # Same profile differs only by the published sex constant.
        self.assertAlmostEqual(resting_energy(female), 1451.5)
        self.assertLess(resting_energy(female), resting_energy(profile()))

    def test_high_activity_has_higher_target_than_low_activity(self):
        low = calculate_daily_targets(profile(activity_level="Sangat ringan"))
        high = calculate_daily_targets(profile(activity_level="Sangat aktif"))
        self.assertGreater(high.calories_kcal, low.calories_kcal)

    def test_goal_order_is_loss_maintain_gain(self):
        loss = calculate_daily_targets(profile(goal="Menurunkan berat badan"))
        maintain = calculate_daily_targets(profile(goal="Mempertahankan berat badan"))
        gain = calculate_daily_targets(profile(goal="Menaikkan berat badan"))
        self.assertLess(loss.calories_kcal, maintain.calories_kcal)
        self.assertLess(maintain.calories_kcal, gain.calories_kcal)

    def test_goal_adjustment_is_never_more_than_300_kcal(self):
        maintain = calculate_daily_targets(profile(activity_level="Sangat aktif"))
        loss = calculate_daily_targets(
            profile(activity_level="Sangat aktif", goal="Menurunkan berat badan")
        )
        gain = calculate_daily_targets(
            profile(activity_level="Sangat aktif", goal="Menaikkan berat badan")
        )
        # Targets are rounded to practical 10-kcal increments.
        self.assertLessEqual(maintain.calories_kcal - loss.calories_kcal, 300)
        self.assertLessEqual(gain.calories_kcal - maintain.calories_kcal, 300)

    def test_weight_loss_never_falls_below_resting_energy(self):
        target = calculate_daily_targets(
            profile(activity_level="Sangat ringan", goal="Menurunkan berat badan")
        )
        self.assertGreaterEqual(target.calories_kcal, target.resting_energy_kcal)


class NutrientTargetTest(unittest.TestCase):
    def test_targets_are_positive_and_limits_are_explicit(self):
        target = calculate_daily_targets(profile())
        self.assertGreater(target.protein_g, 0)
        self.assertGreater(target.carbs_g, 0)
        self.assertGreater(target.fat_g, 0)
        self.assertGreater(target.fiber_g, 0)
        self.assertGreater(target.added_sugar_max_g, 0)
        self.assertEqual(target.sodium_max_mg, 2000)

    def test_fiber_and_sugar_follow_energy_based_guidance(self):
        target = calculate_daily_targets(profile())
        self.assertAlmostEqual(target.fiber_g, target.calories_kcal / 1000 * 14, delta=1)
        self.assertAlmostEqual(target.added_sugar_max_g, target.calories_kcal * 0.10 / 4, delta=1)

    def test_macro_energy_stays_close_to_calorie_target(self):
        target = calculate_daily_targets(profile())
        macro_energy = target.protein_g * 4 + target.carbs_g * 4 + target.fat_g * 9
        self.assertAlmostEqual(macro_energy, target.calories_kcal, delta=12)

    def test_minor_is_not_given_an_adult_target(self):
        with self.assertRaises(TargetCalculationError):
            calculate_daily_targets(profile(age=17))


class TargetCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.temp.name) / "profiles.db")
        self.store_patch = patch("bot.get_profile_store", return_value=self.store)
        self.store_patch.start()

    async def asyncTearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    async def test_missing_profile_is_directed_to_setup(self):
        request = update(999)
        await bot.target_command(request, SimpleNamespace(user_data={}))
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("/setup", reply)

    async def test_target_uses_only_current_users_profile(self):
        self.store.save(profile(telegram_user_id=1, weight_kg=55))
        self.store.save(profile(telegram_user_id=2, weight_kg=110))
        request = update(1)

        await bot.target_command(request, SimpleNamespace(user_data={}))

        reply = request.message.reply_text.await_args.args[0]
        expected = calculate_daily_targets(profile(telegram_user_id=1, weight_kg=55))
        other = calculate_daily_targets(profile(telegram_user_id=2, weight_kg=110))
        self.assertIn(f"Protein: {expected.protein_g} g", reply)
        self.assertNotIn(f"Protein: {other.protein_g} g", reply)
        self.assertIn("maks.", reply)
        self.assertIn("bukan diagnosis atau resep medis", reply)

    async def test_minor_gets_safe_explanation(self):
        self.store.save(profile(telegram_user_id=17, age=17))
        request = update(17)
        await bot.target_command(request, SimpleNamespace(user_data={}))
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("18 tahun ke atas", reply)


if __name__ == "__main__":
    unittest.main()
