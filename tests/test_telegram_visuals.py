"""Visual formatting and Telegram main-menu tests."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from services.profile_store import ProfileStore, UserProfile
from services.meal_store import MealStore
from services.telegram_visuals import format_progress


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
        effective_message=message,
        callback_query=query,
    )


class ProgressBarTest(unittest.TestCase):
    def test_normal_progress_has_correct_percentage(self):
        self.assertEqual(format_progress(74, 100), "███████░░░ 74%")

    def test_limit_marks_attention_from_eighty_percent(self):
        self.assertTrue(format_progress(80, 100, kind="limit").endswith("80% ⚠️"))
        self.assertTrue(format_progress(99, 100, kind="limit").endswith("99% ⚠️"))

    def test_limit_marks_exceeded_at_one_hundred_percent(self):
        self.assertTrue(format_progress(100, 100, kind="limit").endswith("100% ⛔"))

    def test_target_reached_is_positive_not_limit_warning(self):
        progress = format_progress(110, 100, kind="target")
        self.assertTrue(progress.endswith("110% ✓"))
        self.assertNotIn("⛔", progress)

    def test_missing_value_creates_no_fake_progress(self):
        self.assertIsNone(format_progress(None, 100))


class MainMenuTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.temp.name) / "profiles.db")
        self.meals = MealStore(Path(self.temp.name) / "profiles.db")
        self.store_patch = patch("bot.get_profile_store", return_value=self.store)
        self.meal_patch = patch("bot.get_meal_store", return_value=self.meals)
        self.store_patch.start()
        self.meal_patch.start()

    async def asyncTearDown(self):
        self.meal_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    async def test_start_shows_all_working_menu_choices(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        request = SimpleNamespace(
            effective_user=SimpleNamespace(id=1), message=message, effective_message=message
        )
        await bot.start(request, SimpleNamespace(user_data={}))
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(
            labels, ["🍽 Catat Makan", "📊 Hari Ini", "🎯 Target", "👤 Profil", "❓ Bantuan"]
        )

    async def test_record_button_starts_existing_text_flow(self):
        request = callback_update(1, bot.MENU_RECORD_CALLBACK)
        context = SimpleNamespace(user_data={})
        await bot.handle_main_menu_callback(request, context)
        request.callback_query.answer.assert_awaited_once()
        self.assertTrue(context.user_data[bot.MEAL_AWAITING_CORRECTION_KEY])
        self.assertIn("Kirim makanan", request.effective_message.reply_text.await_args.args[0])

    async def test_profile_button_shows_current_users_profile(self):
        self.store.save(
            UserProfile(1, 30, "Laki-laki", 170, 70, "Sedang", "Mempertahankan berat badan")
        )
        request = callback_update(1, bot.MENU_PROFILE_CALLBACK)
        await bot.handle_main_menu_callback(request, SimpleNamespace(user_data={}))
        reply = request.effective_message.reply_text.await_args.args[0]
        markup = request.effective_message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIn("⚖️ Berat: 70 kg", reply)
        self.assertEqual(markup.inline_keyboard[0][0].text, "✏️ Ubah Profil")

    async def test_edit_profile_button_starts_existing_setup_flow(self):
        request = callback_update(1, bot.PROFILE_EDIT_CALLBACK)
        context = SimpleNamespace(user_data={})
        state = await bot.setup_profile_callback(request, context)
        self.assertEqual(state, bot.PROFILE_AGE)
        self.assertEqual(context.user_data[bot.PROFILE_DRAFT_KEY], {})
        self.assertIn("Berapa umur", request.effective_message.reply_text.await_args.args[0])

    async def test_target_today_and_help_buttons_route_to_existing_features(self):
        self.store.save(
            UserProfile(1, 30, "Laki-laki", 170, 70, "Sedang", "Mempertahankan berat badan")
        )
        for callback, expected in (
            (bot.MENU_TARGET_CALLBACK, "🎯 Target Harian Anda"),
            (bot.MENU_TODAY_CALLBACK, "Belum ada makanan"),
            (bot.MENU_HELP_CALLBACK, "📖 Perintah yang tersedia"),
        ):
            request = callback_update(1, callback)
            await bot.handle_main_menu_callback(request, SimpleNamespace(user_data={}))
            self.assertIn(expected, request.effective_message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
