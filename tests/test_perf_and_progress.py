"""Phase 3F.1: performance profiling + progress UX tests.

Covers the full Phase 3F.1 test plan: the progress message appears
immediately and is edited rather than spammed, duration estimates only show
once there's real history and a failed scan never pollutes that history,
image resizing behaves safely (aspect ratio preserved, small images left
alone), exactly one Gemini request happens per photo (with a working
image-analysis cache), DishMatcher stays purely local, USDA lookups can run
concurrently without duplicating identical requests or letting one failure
sink the others, the USDA cache still works, source priority is unchanged,
and "nutrition unavailable" stays explicit rather than silently remapped.
"""

import asyncio
import inspect
import io
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
from services.dish_matcher import DishMatcher, get_matcher as get_dish_matcher
from services.dish_decomposition import decompose
from services.food_matcher import FoodMatcher, VERIFIED, get_matcher as get_food_matcher
from services.food_vision import DetectedFood, FoodAnalysis
from services.image_processing import MAX_DIMENSION, preprocess_image
from services.nutrition_calculator import (
    COUNTED,
    SOURCE_LOCAL,
    UNMATCHED,
    calculate_meal,
)
from services.perf import DurationTracker, NO_ESTIMATE_MESSAGE
from services.usda_food_data import UsdaClient
from services.vision_cache import VisionCache


def run(coro):
    return asyncio.run(coro)


def detected(name, grams, **overrides) -> DetectedFood:
    defaults = dict(identification_confidence=0.9, portion_confidence=0.6, serving_label="")
    defaults.update(overrides)
    return DetectedFood(name=name, estimated_grams=grams, **defaults)


def make_png_bytes(width: int, height: int) -> bytes:
    """A tiny flat-color synthetic image - fine when only dimensions matter."""
    image = Image.new("RGB", (width, height), color=(120, 60, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_photo_bytes(width: int, height: int) -> bytes:
    """A synthetic *photo-like* JPEG with real entropy (matching what
    Telegram actually sends - photos are always re-encoded as JPEG), so
    resizing it down produces a realistically smaller file. A flat-color
    image compresses so well already that a resized JPEG of it can end up
    *larger* than the tiny original - a test artifact, not a real bug."""
    noise = Image.effect_noise((width, height), 40)
    image = Image.merge("RGB", (noise, noise, noise))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Telegram mock helpers
# --------------------------------------------------------------------------

def photo_update(image_bytes: bytes):
    photo_file = MagicMock()
    photo_file.download_as_bytearray = AsyncMock(return_value=bytearray(image_bytes))
    photo = MagicMock()
    photo.get_file = AsyncMock(return_value=photo_file)
    message = MagicMock()
    message.photo = [photo]
    message.chat = MagicMock()
    message.chat.send_action = AsyncMock()

    progress_message = MagicMock()
    progress_message.edit_text = AsyncMock()
    message.reply_text = AsyncMock(return_value=progress_message)

    update = MagicMock()
    update.message = message
    update.effective_user = MagicMock(id=1)
    return update, message, progress_message


SIMPLE_ANALYSIS = FoodAnalysis(foods=[detected("nasi putih", 100)])
SAMPLE_IMAGE = make_png_bytes(200, 150)


# --------------------------------------------------------------------------
# 1-2: progress message behaviour
# --------------------------------------------------------------------------

class ProgressMessageTest(unittest.TestCase):
    def test_progress_message_sent_before_download_is_even_attempted(self):
        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        context = MagicMock()
        context.user_data = {}

        # Make the download itself fail, so if the progress message were
        # sent *after* a successful download we'd never see it either.
        update.message.photo[0].get_file = AsyncMock(side_effect=RuntimeError("network down"))
        from telegram.error import TelegramError
        update.message.photo[0].get_file = AsyncMock(side_effect=TelegramError("down"))

        run(bot.handle_photo(update, context))

        message.reply_text.assert_awaited_once()
        sent_text = message.reply_text.await_args.args[0]
        self.assertIn(bot.ANALYZING_MESSAGE, sent_text)
        progress_message.edit_text.assert_awaited_once_with(bot.DOWNLOAD_FAILED_MESSAGE)

    def test_happy_path_sends_exactly_one_message_and_edits_the_rest(self):
        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        context = MagicMock()
        context.user_data = {}

        fresh_cache = VisionCache()
        with patch("bot.analyze_food_image", new=AsyncMock(return_value=SIMPLE_ANALYSIS)), \
             patch("bot.get_vision_cache", return_value=fresh_cache):
            run(bot.handle_photo(update, context))

        message.reply_text.assert_awaited_once()  # never spammed with new messages
        self.assertGreaterEqual(progress_message.edit_text.await_count, 2)  # recognized -> final result
        final_call = progress_message.edit_text.await_args
        self.assertIn("reply_markup", final_call.kwargs)


# --------------------------------------------------------------------------
# 3-4: duration estimate behaviour
# --------------------------------------------------------------------------

class DurationEstimateTest(unittest.TestCase):
    def test_no_estimate_with_fewer_than_three_samples(self):
        tracker = DurationTracker()
        tracker.record(5.0)
        tracker.record(6.0)
        self.assertFalse(tracker.has_enough_history)
        self.assertIsNone(tracker.estimate_range())
        self.assertEqual(tracker.estimate_message(), NO_ESTIMATE_MESSAGE)

    def test_progress_text_shows_plain_wait_message_with_no_history(self):
        with patch("bot.get_tracker", return_value=DurationTracker()):
            text = bot.build_progress_text()
        self.assertIn(NO_ESTIMATE_MESSAGE, text)
        self.assertNotIn("⏱️", text)

    def test_estimate_appears_after_enough_samples(self):
        tracker = DurationTracker()
        for seconds in (8.0, 9.5, 11.0):
            tracker.record(seconds)
        self.assertTrue(tracker.has_enough_history)
        low, high = tracker.estimate_range()
        self.assertEqual(low, 8.0)
        self.assertEqual(high, 11.0)
        self.assertIn("⏱️", tracker.estimate_message())
        self.assertIn("Biasanya sekitar", tracker.estimate_message())

    def test_progress_text_shows_numeric_range_with_enough_history(self):
        tracker = DurationTracker()
        for seconds in (8.0, 9.0, 12.0):
            tracker.record(seconds)
        with patch("bot.get_tracker", return_value=tracker):
            text = bot.build_progress_text()
        self.assertIn("⏱️", text)
        self.assertIn("8", text)
        self.assertIn("12", text)

    def test_estimate_never_claims_an_exact_promise(self):
        tracker = DurationTracker()
        for seconds in (8.0, 9.0, 10.0):
            tracker.record(seconds)
        message = tracker.estimate_message()
        self.assertNotIn("tepat", message.lower())
        self.assertNotIn("dijamin", message.lower())


class FailedScansDoNotCorruptHistoryTest(unittest.TestCase):
    def test_food_vision_error_does_not_record_a_duration(self):
        from services.food_vision import FoodVisionError

        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        context = MagicMock()
        context.user_data = {}
        tracker = DurationTracker()

        with patch("bot.get_tracker", return_value=tracker), \
             patch("bot.analyze_food_image", new=AsyncMock(side_effect=FoodVisionError("boom"))), \
             patch("bot.get_vision_cache", return_value=VisionCache()):
            run(bot.handle_photo(update, context))

        self.assertEqual(tracker.sample_count, 0)
        progress_message.edit_text.assert_awaited_with(bot.ANALYSIS_FAILED_MESSAGE)

    def test_no_food_detected_does_not_record_a_duration(self):
        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        context = MagicMock()
        context.user_data = {}
        tracker = DurationTracker()
        empty_analysis = FoodAnalysis(foods=[])

        with patch("bot.get_tracker", return_value=tracker), \
             patch("bot.analyze_food_image", new=AsyncMock(return_value=empty_analysis)), \
             patch("bot.get_vision_cache", return_value=VisionCache()):
            run(bot.handle_photo(update, context))

        self.assertEqual(tracker.sample_count, 0)

    def test_download_failure_does_not_record_a_duration(self):
        from telegram.error import TelegramError

        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        update.message.photo[0].get_file = AsyncMock(side_effect=TelegramError("down"))
        context = MagicMock()
        context.user_data = {}
        tracker = DurationTracker()

        with patch("bot.get_tracker", return_value=tracker):
            run(bot.handle_photo(update, context))

        self.assertEqual(tracker.sample_count, 0)

    def test_successful_scan_does_record_a_duration(self):
        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        context = MagicMock()
        context.user_data = {}
        tracker = DurationTracker()

        with patch("bot.get_tracker", return_value=tracker), \
             patch("bot.analyze_food_image", new=AsyncMock(return_value=SIMPLE_ANALYSIS)), \
             patch("bot.get_vision_cache", return_value=VisionCache()):
            run(bot.handle_photo(update, context))

        self.assertEqual(tracker.sample_count, 1)


# --------------------------------------------------------------------------
# 6-8: image preprocessing
# --------------------------------------------------------------------------

class ImagePreprocessingTest(unittest.TestCase):
    def test_oversized_image_is_resized_down_to_the_limit(self):
        original = make_photo_bytes(2400, 1600)
        result = preprocess_image(original)
        self.assertTrue(result.was_resized)
        self.assertLessEqual(max(result.processed_width, result.processed_height), MAX_DIMENSION)
        self.assertLess(len(result.image_bytes), len(original))

    def test_small_image_is_not_enlarged_or_reencoded(self):
        original = make_png_bytes(320, 240)
        result = preprocess_image(original)
        self.assertFalse(result.was_resized)
        self.assertEqual(result.processed_width, 320)
        self.assertEqual(result.processed_height, 240)
        self.assertEqual(result.image_bytes, original)  # byte-for-byte unchanged

    def test_image_exactly_at_the_limit_is_left_alone(self):
        original = make_png_bytes(MAX_DIMENSION, 800)
        result = preprocess_image(original)
        self.assertFalse(result.was_resized)

    def test_aspect_ratio_is_preserved_when_resizing(self):
        original = make_png_bytes(2400, 1200)  # 2:1
        result = preprocess_image(original)
        self.assertEqual(result.processed_width, MAX_DIMENSION)
        self.assertAlmostEqual(result.processed_width / result.processed_height, 2.0, delta=0.02)

    def test_aspect_ratio_is_preserved_for_portrait_photos(self):
        original = make_png_bytes(1200, 2400)  # 1:2
        result = preprocess_image(original)
        self.assertEqual(result.processed_height, MAX_DIMENSION)
        self.assertAlmostEqual(result.processed_height / result.processed_width, 2.0, delta=0.02)

    def test_records_original_and_processed_sizes(self):
        original = make_png_bytes(2000, 1000)
        result = preprocess_image(original)
        self.assertEqual(result.original_width, 2000)
        self.assertEqual(result.original_height, 1000)
        self.assertEqual(result.original_bytes, len(original))
        self.assertGreater(result.processed_bytes, 0)

    def test_undecodable_bytes_pass_through_unchanged_rather_than_crashing(self):
        garbage = b"not an image at all"
        result = preprocess_image(garbage)
        self.assertEqual(result.image_bytes, garbage)


# --------------------------------------------------------------------------
# 9: one Gemini request per photo (+ cache)
# --------------------------------------------------------------------------

class OneGeminiRequestTest(unittest.TestCase):
    def test_exactly_one_gemini_call_for_a_single_photo(self):
        update, message, progress_message = photo_update(SAMPLE_IMAGE)
        context = MagicMock()
        context.user_data = {}
        mock_analyze = AsyncMock(return_value=SIMPLE_ANALYSIS)

        with patch("bot.analyze_food_image", new=mock_analyze), \
             patch("bot.get_vision_cache", return_value=VisionCache()):
            run(bot.handle_photo(update, context))

        mock_analyze.assert_awaited_once()

    def test_identical_photo_twice_reuses_the_cached_analysis(self):
        shared_cache = VisionCache()
        mock_analyze = AsyncMock(return_value=SIMPLE_ANALYSIS)

        for _ in range(2):
            update, message, progress_message = photo_update(SAMPLE_IMAGE)
            context = MagicMock()
            context.user_data = {}
            with patch("bot.analyze_food_image", new=mock_analyze), \
                 patch("bot.get_vision_cache", return_value=shared_cache):
                run(bot.handle_photo(update, context))

        mock_analyze.assert_awaited_once()  # second photo was a cache hit

    def test_different_photos_each_still_get_their_own_gemini_call(self):
        shared_cache = VisionCache()
        mock_analyze = AsyncMock(return_value=SIMPLE_ANALYSIS)
        other_image = make_png_bytes(64, 64)

        for image_bytes in (SAMPLE_IMAGE, other_image):
            update, message, progress_message = photo_update(image_bytes)
            context = MagicMock()
            context.user_data = {}
            with patch("bot.analyze_food_image", new=mock_analyze), \
                 patch("bot.get_vision_cache", return_value=shared_cache):
                run(bot.handle_photo(update, context))

        self.assertEqual(mock_analyze.await_count, 2)


# --------------------------------------------------------------------------
# 10: DishMatcher stays local/deterministic
# --------------------------------------------------------------------------

class DishMatcherStaysLocalTest(unittest.TestCase):
    def test_match_is_a_plain_synchronous_function(self):
        self.assertFalse(inspect.iscoroutinefunction(DishMatcher.match))

    def test_decompose_is_a_plain_synchronous_function(self):
        self.assertFalse(inspect.iscoroutinefunction(decompose))

    def test_dish_matcher_module_imports_no_network_or_model_client(self):
        import services.dish_matcher as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("google.genai", "import httpx", "import openai"):
            self.assertNotIn(forbidden, source)

    def test_decomposition_does_not_touch_the_network_even_with_a_real_matcher(self):
        # No mocking of any HTTP/genai client here at all - if decompose()
        # tried to reach out, there'd be nothing to answer it and this
        # would hang or raise. It resolves immediately.
        matcher = get_dish_matcher()
        analysis = FoodAnalysis(foods=[detected("lele goreng", 100)], dish_name="pecel lele")
        start = time.perf_counter()
        decomposed = decompose(analysis, matcher)
        self.assertLess(time.perf_counter() - start, 0.05)
        self.assertEqual(decomposed.dish_name, "Pecel lele")


# --------------------------------------------------------------------------
# 11-12: USDA concurrency
# --------------------------------------------------------------------------

SEARCH_HIT_TEMPLATE = {
    "cabbage": {"fdcId": 1, "dataType": "SR Legacy", "description": "Cabbage, raw"},
    "cucumber": {"fdcId": 2, "dataType": "Foundation", "description": "Cucumber, with peel, raw"},
}

DETAIL_TEMPLATE = {
    1: {
        "fdcId": 1, "dataType": "SR Legacy", "description": "Cabbage, raw",
        "foodNutrients": [
            {"nutrient": {"number": "208", "unitName": "kcal"}, "amount": 25.0},
            {"nutrient": {"number": "203", "unitName": "g"}, "amount": 1.28},
            {"nutrient": {"number": "205", "unitName": "g"}, "amount": 5.8},
            {"nutrient": {"number": "204", "unitName": "g"}, "amount": 0.1},
        ],
    },
    2: {
        "fdcId": 2, "dataType": "Foundation", "description": "Cucumber, with peel, raw",
        "foodNutrients": [
            {"nutrient": {"number": "957", "unitName": "kcal"}, "amount": 15.0},
            {"nutrient": {"number": "203", "unitName": "g"}, "amount": 0.6},
            {"nutrient": {"number": "205", "unitName": "g"}, "amount": 3.0},
            {"nutrient": {"number": "204", "unitName": "g"}, "amount": 0.1},
        ],
    },
}


def make_slow_client(delay: float, fail_food: str | None = None) -> UsdaClient:
    """A fake USDA transport where every request takes `delay` seconds -
    used to prove concurrent lookups overlap instead of queueing."""

    async def handler(request):
        await asyncio.sleep(delay)
        if fail_food and fail_food in str(request.url):
            raise httpx.ReadTimeout("boom", request=request)
        if "/foods/search" in request.url.path:
            query = request.url.params.get("query", "")
            key = "cabbage" if "cabbage" in query else "cucumber"
            return httpx.Response(200, json={"foods": [SEARCH_HIT_TEMPLATE[key]]})
        fdc_id = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=DETAIL_TEMPLATE[fdc_id])

    transport = httpx.MockTransport(handler)
    return UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=transport))


class UsdaConcurrencyTest(unittest.TestCase):
    def test_two_different_foods_are_looked_up_concurrently_not_sequentially(self):
        client = make_slow_client(delay=0.05)
        matcher = FoodMatcher([])

        start = time.perf_counter()
        meal = run(calculate_meal([detected("kol", 50), detected("timun", 40)], matcher, client))
        elapsed = time.perf_counter() - start

        self.assertEqual([i.status for i in meal.items], [COUNTED, COUNTED])
        # Sequential would take ~4 * 0.05s = 0.20s (2 requests per food);
        # concurrent should take ~2 * 0.05s = 0.10s. Generous margin for CI jitter.
        self.assertLess(elapsed, 0.17)

    def test_concurrent_identical_foods_share_one_fetch(self):
        client = make_slow_client(delay=0.02)
        matcher = FoodMatcher([])

        meal = run(calculate_meal([detected("kol", 50), detected("kol", 80)], matcher, client))

        self.assertEqual([i.status for i in meal.items], [COUNTED, COUNTED])
        # 1 search + 1 detail = 2 requests total, not 4 - the second "kol"
        # joined the first's in-flight fetch instead of firing its own.
        self.assertEqual(client.request_count, 2)

    def test_one_failure_does_not_affect_the_other_food_when_run_concurrently(self):
        client = make_slow_client(delay=0.02, fail_food="cabbage")
        matcher = FoodMatcher([])

        meal = run(calculate_meal([detected("kol", 50), detected("timun", 40)], matcher, client))

        by_name = {item.name: item for item in meal.items}
        self.assertEqual(by_name["kol"].status, UNMATCHED)
        self.assertEqual(by_name["timun"].status, COUNTED)


# --------------------------------------------------------------------------
# 13: USDA cache remains effective
# --------------------------------------------------------------------------

class UsdaCacheStillEffectiveTest(unittest.TestCase):
    def test_repeated_lookup_across_separate_calls_hits_the_cache(self):
        client = make_slow_client(delay=0.0)
        matcher = FoodMatcher([])

        run(calculate_meal([detected("kol", 50)], matcher, client))
        self.assertEqual(client.request_count, 2)

        run(calculate_meal([detected("kol", 30)], matcher, client))
        self.assertEqual(client.request_count, 2)  # no new requests - cache hit


# --------------------------------------------------------------------------
# 14: source priority unchanged
# --------------------------------------------------------------------------

class SourcePriorityUnchangedTest(unittest.TestCase):
    def test_verified_local_record_still_wins_with_no_network_call(self):
        from services.food_matcher import FoodRecord

        verified = FoodRecord(
            id="kol", name="Kol", aliases=["kol"],
            calories_per_100g=29.0, protein_per_100g=1.4, carbs_per_100g=5.3, fat_per_100g=0.2,
            data_status=VERIFIED,
        )

        def explode(request):
            raise AssertionError("USDA must not be called for a verified local record")

        client = UsdaClient(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        meal = run(calculate_meal([detected("kol", 100)], FoodMatcher([verified]), client))

        self.assertEqual(meal.items[0].source, SOURCE_LOCAL)
        self.assertEqual(client.request_count, 0)


# --------------------------------------------------------------------------
# 15: "nutrition unavailable" stays explicit, never silently remapped
# --------------------------------------------------------------------------

class NutritionUnavailableStaysExplicitTest(unittest.TestCase):
    """Regression names from the real-world test run (section H): none of
    these must be silently matched to an unrelated existing record.

    "daun selada" was in this list at the time this test was written
    (Phase 3F.1) but is deliberately excluded now: Phase 3G investigated it
    against the official TKPI PDF, found an exact identity match (DR145
    "Selada, segar"), and promoted it to a verified local record - see
    test_nutrition_coverage_batch1.py. That is the intended outcome, not a
    regression: this test is about names that have no defensible source,
    not about permanently keeping coverage incomplete."""

    REAL_WORLD_UNMAPPED_FOODS = (
        "ayam bumbu merah",
        "urap sayur",
        "ikan asin goreng",
    )

    def test_unmapped_real_world_foods_are_explicitly_unavailable(self):
        matcher = get_food_matcher()  # the real shipped foods.json
        no_usda = UsdaClient(api_key="")
        for name in self.REAL_WORLD_UNMAPPED_FOODS:
            with self.subTest(food=name):
                meal = run(calculate_meal([detected(name, 100)], matcher, no_usda))
                self.assertEqual(meal.items[0].status, UNMATCHED)

    def test_ayam_bumbu_merah_is_never_silently_matched_to_ayam_goreng(self):
        matcher = get_food_matcher()
        record = matcher.match("ayam bumbu merah")
        self.assertIsNone(record)

    def test_format_nutrition_shows_the_unavailable_message(self):
        no_usda = UsdaClient(api_key="")
        matcher = get_food_matcher()
        meal = run(calculate_meal([detected("ayam bumbu merah", 100)], matcher, no_usda))
        text = bot.format_nutrition(meal)
        self.assertIn("belum tersedia", text)


if __name__ == "__main__":
    unittest.main()
