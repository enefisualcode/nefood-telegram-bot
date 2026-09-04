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
from services.food_vision import FoodAnalysis, FoodVisionError, analyze_food_image
from services.nutrition_calculator import (
    SOURCE_USDA,
    UNMATCHED,
    UNVERIFIED,
    MealNutrition,
    calculate_meal,
)

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


def format_analysis(analysis: FoodAnalysis) -> str:
    """Render the detection result as the Telegram reply text."""
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id if user else "unknown"
    logger.info("photo received from user_id=%s", user_id)

    message = update.message
    await message.chat.send_action(ChatAction.TYPING)
    await message.reply_text(ANALYZING_MESSAGE)

    # The last PhotoSize is the highest resolution Telegram offers.
    photo = message.photo[-1]

    try:
        photo_file = await photo.get_file()
        image_bytes = bytes(await photo_file.download_as_bytearray())
    except TelegramError:
        logger.exception("Failed to download photo for user_id=%s", user_id)
        await message.reply_text(DOWNLOAD_FAILED_MESSAGE)
        return

    try:
        analysis = await analyze_food_image(image_bytes)
    except FoodVisionError:
        # Details are logged inside the service; never surface them to the user.
        logger.exception("Food analysis failed for user_id=%s", user_id)
        await message.reply_text(ANALYSIS_FAILED_MESSAGE)
        return

    if not analysis.foods:
        await message.reply_text(NO_FOOD_MESSAGE)
        return

    context.user_data[LAST_ANALYSIS_KEY] = analysis
    await message.reply_text(format_analysis(analysis), reply_markup=result_keyboard())


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

    try:
        meal = await calculate_meal(analysis.foods)
    except Exception:
        logger.exception("Nutrition calculation failed for user_id=%s", user.id if user else "unknown")
        await query.message.reply_text(NUTRITION_FAILED_MESSAGE)
        return

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
