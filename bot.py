"""Nutrition tracking Telegram bot.

Phase 2: recognises the foods visible in a photo. Nutrition calculation
is intentionally left for a later phase.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from services.dish_decomposition import DecomposedMeal, decompose
from services.dish_matcher import DishMatcher
from services.dish_matcher import get_matcher as get_dish_matcher
from services.food_vision import (
    DISH_TYPE_AMBIGUOUS,
    FoodAnalysis,
    FoodVisionError,
    analyze_food_image,
)
from services.image_processing import preprocess_image
from services.nutrition_calculator import (
    SOURCE_USDA,
    UNMATCHED,
    UNVERIFIED,
    MealNutrition,
    calculate_meal,
)
from services.perf import StageTimer, get_tracker
from services.usda_food_data import get_client as get_usda_client
from services.vision_cache import get_cache as get_vision_cache

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
)
# httpx logs every Telegram API call at INFO; too noisy for normal use.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

START_MESSAGE = (
    "👋 Halo! Saya bot pencatat nutrisi.\n\n"
    "Kirim foto makanan Anda dan saya akan mendeteksi makanan yang terlihat.\n"
    "Ketik /help untuk melihat daftar perintah."
)

HELP_MESSAGE = (
    "📖 Perintah yang tersedia:\n\n"
    "/start - Mulai menggunakan bot\n"
    "/help - Tampilkan pesan bantuan ini\n\n"
    "Kirim foto makanan untuk dideteksi."
)

ANALYZING_MESSAGE = "🔍 Sedang menganalisis makanan..."
ANALYZING_DETAIL_MESSAGE = "Mengenali jenis makanan dan memperkirakan porsinya."
RECOGNIZED_MESSAGE = "🍽️ Makanan berhasil dikenali.\nMenyiapkan hasil..."

NO_FOOD_MESSAGE = (
    "🤔 Maaf, saya tidak dapat mengenali makanan pada foto tersebut dengan jelas.\n\n"
    "Coba kirim foto yang lebih terang, lebih dekat, dan menampilkan makanan "
    "secara utuh."
)

ANALYSIS_FAILED_MESSAGE = (
    "⚠️ Maaf, analisis foto gagal saat ini. Silakan coba lagi beberapa saat lagi."
)

DOWNLOAD_FAILED_MESSAGE = (
    "⚠️ Maaf, foto gagal diunduh dari Telegram. Silakan coba kirim ulang."
)

PORTION_DISCLAIMER = "⚠️ Porsi hanya perkiraan dari foto."

VISIBLE_COMPONENTS_LABEL = "Yang terlihat:"

CONFIRM_CALLBACK = "portion:confirm"
CORRECT_CALLBACK = "portion:correct"

CONFIRMED_MESSAGE = "✅ Porsi dikonfirmasi."

# Key under which the latest detection is kept in Telegram's in-memory user_data.
LAST_ANALYSIS_KEY = "last_analysis"

NO_ANALYSIS_MESSAGE = (
    "🤔 Data makanan tidak ditemukan lagi. Silakan kirim ulang foto makanannya."
)

NUTRITION_DISCLAIMER = (
    "⚠️ Nilai nutrisi merupakan estimasi berdasarkan jenis makanan dan "
    "perkiraan porsi."
)

NO_NUTRITION_DATA_MESSAGE = (
    "⚠️ Belum ada data nutrisi terverifikasi untuk makanan yang terdeteksi, "
    "jadi total tidak dapat dihitung."
)

CORRECTION_MESSAGE = "✏️ Fitur koreksi porsi akan dibuat pada langkah berikutnya."

NUTRITION_FAILED_MESSAGE = (
    "⚠️ Maaf, perhitungan nutrisi gagal saat ini. Silakan coba lagi."
)

# Ordered high to low; the first threshold a value reaches wins.
CONFIDENCE_LABELS = (
    (0.85, "Sangat yakin"),
    (0.70, "Cukup yakin"),
    (0.50, "Sedang"),
)


def confidence_label(value: float) -> str:
    """Turn a 0-1 confidence into a human-readable Indonesian label."""
    for threshold, label in CONFIDENCE_LABELS:
        if value >= threshold:
            return label
    return "Rendah"


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Konfirmasi", callback_data=CONFIRM_CALLBACK)],
            [InlineKeyboardButton("✏️ Koreksi porsi", callback_data=CORRECT_CALLBACK)],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("/start from user_id=%s", user.id if user else "unknown")
    await update.message.reply_text(START_MESSAGE)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("/help from user_id=%s", user.id if user else "unknown")
    await update.message.reply_text(HELP_MESSAGE)


def _format_simple_analysis(analysis: FoodAnalysis) -> str:
    """The original (pre-Phase-3F) detection layout - unchanged on purpose."""
    lines = ["🔍 Makanan terdeteksi:", ""]

    for food in analysis.foods:
        portion = f"±{food.estimated_grams} g"
        if food.serving_label:
            portion += f" ({food.serving_label})"

        lines.append(f"🍽 {food.name.capitalize()}")
        lines.append(f"Porsi: {portion}")
        lines.append(f"Identifikasi: {confidence_label(food.identification_confidence)}")
        lines.append(f"Estimasi porsi: {confidence_label(food.portion_confidence)}")
        lines.append("")

    if analysis.notes:
        lines.append(f"📝 {analysis.notes}")
        lines.append("")

    lines.append(PORTION_DISCLAIMER)
    return "\n".join(lines)


def _format_dish_analysis(analysis: FoodAnalysis, decomposed: DecomposedMeal) -> str:
    """Compound/variable/ambiguous layout: dish header + only visible components.

    Never renders a template component that isn't in `decomposed.foods` -
    that list is always exactly what Gemini reported seeing.
    """
    lines = [f"🍽️ {decomposed.dish_name.capitalize()}", "", VISIBLE_COMPONENTS_LABEL]

    for food in decomposed.foods:
        portion = f"±{food.estimated_grams} g"
        if food.serving_label:
            portion += f" ({food.serving_label})"
        lines.append(f"• {food.name.capitalize()} — {portion}")

    lines.append("")

    if decomposed.dish_type == DISH_TYPE_AMBIGUOUS:
        lines.append(
            f"❓ Jenis {decomposed.dish_name.lower()} belum dapat dipastikan dari foto ini "
            "(perlu kejelasan lebih lanjut sebelum dihitung sebagai jenis tertentu)."
        )
        lines.append("")

    if analysis.notes:
        lines.append(f"📝 {analysis.notes}")
        lines.append("")

    lines.append(PORTION_DISCLAIMER)
    return "\n".join(lines)


def format_analysis(analysis: FoodAnalysis, dish_matcher: DishMatcher | None = None) -> str:
    """Render the detection result as the Telegram reply text.

    Simple detections (the common case, and everything before Phase 3F) are
    rendered exactly as before. A recognized compound/variable/ambiguous
    dish gets a dish-name header followed by only the components Gemini
    actually reported seeing - see services/dish_decomposition.py.
    """
    dish_matcher = dish_matcher or get_dish_matcher()
    decomposed = decompose(analysis, dish_matcher)

    if decomposed.is_simple:
        return _format_simple_analysis(analysis)

    return _format_dish_analysis(analysis, decomposed)


def build_progress_text() -> str:
    """The single progress message's first state - includes a rough,
    history-based duration estimate (never a hard-coded or exact promise)."""
    tracker = get_tracker()
    return "\n".join([ANALYZING_MESSAGE, ANALYZING_DETAIL_MESSAGE, "", tracker.estimate_message()])


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else "unknown"
    logger.info("photo received from user_id=%s", user_id)

    message = update.message
    await message.chat.send_action(ChatAction.TYPING)

    # One message, edited through each state, instead of a growing thread of
    # separate replies - see services/perf.py for the estimate behind it.
    progress_message = await message.reply_text(build_progress_text())

    timer = StageTimer()

    # The last PhotoSize is the highest resolution Telegram offers.
    photo = message.photo[-1]

    try:
        with timer.stage("photo_download"):
            photo_file = await photo.get_file()
            image_bytes = bytes(await photo_file.download_as_bytearray())
    except TelegramError:
        logger.exception("Failed to download photo for user_id=%s", user_id)
        await progress_message.edit_text(DOWNLOAD_FAILED_MESSAGE)
        return

    with timer.stage("image_preprocess"):
        processed = preprocess_image(image_bytes)
        vision_cache = get_vision_cache()
        cache_key = vision_cache.key_for(processed.image_bytes)

    # Technical detail for logs only - never shown to the user.
    logger.debug(
        "photo preprocess user_id=%s original=%dx%d(%dB) processed=%dx%d(%dB) resized=%s",
        user_id, processed.original_width, processed.original_height, processed.original_bytes,
        processed.processed_width, processed.processed_height, processed.processed_bytes,
        processed.was_resized,
    )

    try:
        with timer.stage("gemini"):
            cached = vision_cache.get(cache_key)
            if cached is not None:
                analysis = cached
            else:
                analysis = await analyze_food_image(processed.image_bytes, mime_type=processed.mime_type)
                vision_cache.put(cache_key, analysis)
    except FoodVisionError:
        # Details are logged inside the service; never surface them to the user.
        logger.exception("Food analysis failed for user_id=%s", user_id)
        await progress_message.edit_text(ANALYSIS_FAILED_MESSAGE)
        return

    if not analysis.foods:
        await progress_message.edit_text(NO_FOOD_MESSAGE)
        return

    await progress_message.edit_text(RECOGNIZED_MESSAGE)

    with timer.stage("decomposition"):
        result_text = format_analysis(analysis)

    context.user_data[LAST_ANALYSIS_KEY] = analysis
    await progress_message.edit_text(result_text, reply_markup=result_keyboard())

    # Only a fully successful analysis feeds the duration estimate - a
    # failed/aborted scan (any of the early returns above) never reaches
    # this line, so it can't corrupt what future users are shown.
    get_tracker().record(timer.total)
    logger.info("perf photo_analysis user_id=%s %s", user_id, timer.summary(total_label="total_analysis"))


def format_nutrition(meal: MealNutrition) -> str:
    """Render the calculated meal nutrition as the Telegram reply text."""
    lines = ["🍽 Estimasi nutrisi", ""]

    for item in meal.items:
        label = item.name.capitalize()

        if item.status == UNMATCHED:
            lines.append(f'⚠️ Data nutrisi untuk "{label}" belum tersedia.')
            lines.append("")
            continue

        if item.status == UNVERIFIED:
            lines.append(
                f'⚠️ Data nutrisi untuk "{label}" tersedia tetapi belum '
                "terverifikasi, sehingga belum digunakan dalam total."
            )
            lines.append("")
            continue

        value = item.nutrition.rounded()
        if item.edible_portion_applied:
            # Show both weights so the correction is visible but not technical.
            lines.append(label)
            lines.append(f"Perkiraan porsi terlihat: ±{item.estimated_gross_grams} g")
            lines.append(
                f"Perkiraan bagian dapat dimakan: ±{item.calculated_edible_grams} g"
            )
        else:
            lines.append(f"{label} — ±{item.estimated_gross_grams} g")
        lines.append(f"🔥 {value.calories:.0f} kcal")
        lines.append(f"🥩 Protein: {value.protein:.1f} g")
        lines.append(f"🍚 Karbo: {value.carbs:.1f} g")
        lines.append(f"🥑 Lemak: {value.fat:.1f} g")
        if item.source == SOURCE_USDA:
            lines.append("Sumber: USDA FoodData Central")
        lines.append("")

    if not meal.counted_items:
        lines.append(NO_NUTRITION_DATA_MESSAGE)
        return "\n".join(lines)

    total = meal.total.rounded()
    lines.append("━━━━━━━━━━")
    lines.append("TOTAL")
    lines.append(f"🔥 {total.calories:.0f} kcal")
    lines.append(f"🥩 Protein: {total.protein:.1f} g")
    lines.append(f"🍚 Karbo: {total.carbs:.1f} g")
    lines.append(f"🥑 Lemak: {total.fat:.1f} g")
    lines.append("")
    lines.append(NUTRITION_DISCLAIMER)

    return "\n".join(lines)


async def handle_portion_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the confirm / correct buttons shown under a detection result."""
    query = update.callback_query
    # Always answer, otherwise Telegram keeps showing a loading spinner.
    await query.answer()

    user = update.effective_user
    logger.info(
        "callback %s from user_id=%s", query.data, user.id if user else "unknown"
    )

    if query.data != CONFIRM_CALLBACK:
        await query.message.reply_text(CORRECTION_MESSAGE)
        return

    analysis = context.user_data.get(LAST_ANALYSIS_KEY)
    if analysis is None:
        # user_data is in-memory only, so it is lost on restart.
        await query.message.reply_text(NO_ANALYSIS_MESSAGE)
        return

    await query.message.reply_text(CONFIRMED_MESSAGE)

    timer = StageTimer()
    usda_client = get_usda_client()
    usda_seconds_before = usda_client.total_fetch_seconds
    try:
        with timer.stage("nutrition_resolution"):
            meal = await calculate_meal(analysis.foods, usda=usda_client)
    except Exception:
        logger.exception("Nutrition calculation failed for user_id=%s", user.id if user else "unknown")
        await query.message.reply_text(NUTRITION_FAILED_MESSAGE)
        return

    usda_seconds = usda_client.total_fetch_seconds - usda_seconds_before
    logger.info(
        "perf nutrition_resolution user_id=%s %s usda_lookup=%.2fs",
        user.id if user else "unknown",
        timer.summary(total_label="total_nutrition"),
        usda_seconds,
    )

    await query.message.reply_text(format_nutrition(meal))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any unhandled exception and tell the user something went wrong."""
    logger.error("Unhandled error while processing update", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Terjadi kesalahan. Silakan coba lagi."
            )
        except Exception:  # noqa: BLE001 - never let the notifier crash the handler
            logger.exception("Failed to send error message to the user")


def build_application() -> Application:
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(
        CallbackQueryHandler(
            handle_portion_callback, pattern=f"^({CONFIRM_CALLBACK}|{CORRECT_CALLBACK})$"
        )
    )
    application.add_error_handler(error_handler)

    return application


def main() -> None:
    try:
        config.validate()
    except config.ConfigError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)

    logger.info("Starting bot with model %s...", config.GEMINI_MODEL)
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
