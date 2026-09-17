"""User profile conversation and persistent storage tests."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from services.profile_store import ProfileStore, UserProfile


def make_profile(user_id: int, **overrides) -> UserProfile:
    values = dict(
        telegram_user_id=user_id,
        age=30,
        gender="Laki-laki",
        height_cm=170,
        weight_kg=65.5,
        activity_level="Sedang",
        goal="Mempertahankan berat badan",
    )
    values.update(overrides)
    return UserProfile(**values)


def update(user_id: int, text: str = ""):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=message,
    )


def context():
    return SimpleNamespace(user_data={})


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
    )


class ProfileStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "profiles.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_survives_store_recreation_simulating_restart(self):
        ProfileStore(self.path).save(make_profile(101))

        restarted_store = ProfileStore(self.path)
        loaded = restarted_store.get(101)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.age, 30)
        self.assertEqual(loaded.weight_kg, 65.5)

    def test_two_users_are_kept_separate(self):
        store = ProfileStore(self.path)
        store.save(make_profile(101, weight_kg=60))
        store.save(make_profile(202, gender="Perempuan", weight_kg=72))

        self.assertEqual(store.get(101).weight_kg, 60)
        self.assertEqual(store.get(202).weight_kg, 72)
        self.assertEqual(store.get(202).gender, "Perempuan")

    def test_saving_again_updates_only_that_user(self):
        store = ProfileStore(self.path)
        store.save(make_profile(101, weight_kg=60))
        store.save(make_profile(202, weight_kg=72))
        store.save(make_profile(101, weight_kg=61, goal="Menaikkan berat badan"))

        self.assertEqual(store.get(101).weight_kg, 61)
        self.assertEqual(store.get(101).goal, "Menaikkan berat badan")
        self.assertEqual(store.get(202).weight_kg, 72)

    def test_unknown_user_has_no_profile(self):
        self.assertIsNone(ProfileStore(self.path).get(999))


class ProfileConversationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.temp.name) / "profiles.db")
        self.store_patch = patch("bot.get_profile_store", return_value=self.store)
        self.store_patch.start()

    async def asyncTearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    async def test_complete_setup_is_saved_only_after_confirmation(self):
        ctx = context()

        self.assertEqual(await bot.setup_profile(update(101), ctx), bot.PROFILE_AGE)
        self.assertEqual(await bot.receive_age(update(101, "30"), ctx), bot.PROFILE_GENDER)
        self.assertEqual(
            await bot.receive_gender(update(101, "Laki-laki"), ctx), bot.PROFILE_HEIGHT
        )
        self.assertEqual(await bot.receive_height(update(101, "170"), ctx), bot.PROFILE_WEIGHT)
        self.assertEqual(await bot.receive_weight(update(101, "65,5"), ctx), bot.PROFILE_ACTIVITY)
        self.assertEqual(await bot.receive_activity(update(101, "Sedang"), ctx), bot.PROFILE_GOAL)
        self.assertEqual(
            await bot.receive_goal(update(101, "Mempertahankan berat badan"), ctx),
            bot.PROFILE_CONFIRM,
        )
        self.assertIsNone(self.store.get(101))

        result = await bot.confirm_profile(update(101, "Ya, simpan"), ctx)

        self.assertEqual(result, bot.ConversationHandler.END)
        self.assertEqual(self.store.get(101).weight_kg, 65.5)
        self.assertNotIn(bot.PROFILE_DRAFT_KEY, ctx.user_data)

    async def test_profile_command_reads_only_current_users_profile(self):
        self.store.save(make_profile(101, weight_kg=60))
        self.store.save(make_profile(202, weight_kg=82))
        request = update(101)

        result = await bot.profile_command(request, context())

        self.assertEqual(result, bot.ConversationHandler.END)
        reply = request.message.reply_text.await_args.args[0]
        self.assertIn("60 kg", reply)
        self.assertNotIn("82 kg", reply)

    async def test_profile_command_starts_setup_when_profile_is_missing(self):
        request = update(303)
        ctx = context()

        result = await bot.profile_command(request, ctx)

        self.assertEqual(result, bot.PROFILE_AGE)
        self.assertEqual(ctx.user_data[bot.PROFILE_DRAFT_KEY], {})

    async def test_invalid_values_are_rejected(self):
        ctx = context()
        await bot.setup_profile(update(101), ctx)

        self.assertEqual(await bot.receive_age(update(101, "abc"), ctx), bot.PROFILE_AGE)
        self.assertEqual(await bot.receive_age(update(101, "12"), ctx), bot.PROFILE_AGE)
        await bot.receive_age(update(101, "30"), ctx)
        await bot.receive_gender(update(101, "Laki-laki"), ctx)
        self.assertEqual(await bot.receive_height(update(101, "300"), ctx), bot.PROFILE_HEIGHT)

    async def test_gender_prompt_uses_visible_inline_buttons(self):
        ctx = context()
        await bot.setup_profile(update(101), ctx)
        request = update(101, "30")

        await bot.receive_age(request, ctx)

        markup = request.message.reply_text.await_args.kwargs["reply_markup"]
        labels = [row[0].text for row in markup.inline_keyboard]
        self.assertEqual(labels, ["Laki-laki", "Perempuan"])

    async def test_pria_text_alias_is_accepted(self):
        ctx = context()
        await bot.setup_profile(update(101), ctx)
        await bot.receive_age(update(101, "30"), ctx)
        result = await bot.receive_gender(update(101, "Pria"), ctx)
        self.assertEqual(result, bot.PROFILE_HEIGHT)
        self.assertEqual(ctx.user_data[bot.PROFILE_DRAFT_KEY]["gender"], "Laki-laki")

    async def test_inline_buttons_complete_the_choice_steps(self):
        ctx = context()
        await bot.setup_profile(update(101), ctx)
        await bot.receive_age(update(101, "30"), ctx)

        self.assertEqual(
            await bot.choose_gender(
                callback_update(101, "profile_choice:gender:0"), ctx
            ),
            bot.PROFILE_HEIGHT,
        )
        await bot.receive_height(update(101, "170"), ctx)
        await bot.receive_weight(update(101, "65"), ctx)
        self.assertEqual(
            await bot.choose_activity(
                callback_update(101, "profile_choice:activity:2"), ctx
            ),
            bot.PROFILE_GOAL,
        )
        self.assertEqual(
            await bot.choose_goal(callback_update(101, "profile_choice:goal:1"), ctx),
            bot.PROFILE_CONFIRM,
        )
        result = await bot.choose_confirmation(
            callback_update(101, "profile_choice:confirm:0"), ctx
        )
        self.assertEqual(result, bot.ConversationHandler.END)
        self.assertIsNotNone(self.store.get(101))

    async def test_cancel_does_not_replace_existing_profile(self):
        self.store.save(make_profile(101, weight_kg=60))
        ctx = context()
        await bot.setup_profile(update(101), ctx)
        await bot.receive_age(update(101, "40"), ctx)

        result = await bot.cancel_profile(update(101, "/batal"), ctx)

        self.assertEqual(result, bot.ConversationHandler.END)
        self.assertEqual(self.store.get(101).weight_kg, 60)
        self.assertEqual(self.store.get(101).age, 30)

    async def test_confirm_without_draft_fails_safely(self):
        result = await bot.confirm_profile(update(101, "Ya, simpan"), context())
        self.assertEqual(result, bot.ConversationHandler.END)
        self.assertIsNone(self.store.get(101))


if __name__ == "__main__":
    unittest.main()
