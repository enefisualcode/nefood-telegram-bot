"""Nutrition tracking Telegram bot.

Phase 2: recognises the foods visible in a photo. Nutrition calculation
is intentionally left for a later phase.
"""

import logging
from datetime import datetime, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
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
from services.daily_recap import JAKARTA, build_daily_recap
from services.meal_input import MealInputError, parse_meal_text
from services.meal_store import get_meal_store
from services.nutrition_calculator import (
    SOURCE_USDA,
    UNMATCHED,
    UNVERIFIED,
    MealNutrition,
    calculate_meal,
)
from services.nutrition_advice import choose_daily_advice
from services.nutrition_warnings import build_nutrition_warnings
from services.perf import StageTimer, get_tracker
from services.daily_targets import TargetCalculationError, calculate_daily_targets
from services.profile_store import UserProfile, get_profile_store
from services.telegram_visuals import format_number, format_progress
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
    "👋 Selamat datang di NutruFood\n\n"
    "Catat makanan lewat foto atau teks, lalu lihat konsumsi dan target harian Anda.\n\n"
    "Pilih menu di bawah untuk mulai."
)

HELP_MESSAGE = (
    "📖 Perintah yang tersedia:\n\n"
    "/start - Mulai menggunakan bot\n"
    "/help - Tampilkan pesan bantuan ini\n\n"
    "/profil - Lihat profil pribadi\n"
    "/setup - Isi atau perbarui profil\n"
    "/target - Lihat target kalori dan nutrisi harian\n"
    "/hariini - Lihat rekap konsumsi hari ini\n"
    "/catat - Catat makanan melalui teks\n"
    "/batal - Batalkan pengisian profil\n\n"
    "Kirim foto makanan untuk dideteksi."
)

PROFILE_DRAFT_KEY = "profile_draft"
(
    PROFILE_AGE,
    PROFILE_GENDER,
    PROFILE_HEIGHT,
    PROFILE_WEIGHT,
    PROFILE_ACTIVITY,
    PROFILE_GOAL,
    PROFILE_CONFIRM,
) = range(7)

GENDER_OPTIONS = ("Laki-laki", "Perempuan")
ACTIVITY_OPTIONS = (
    "Sangat ringan",
    "Ringan",
    "Sedang",
    "Aktif",
    "Sangat aktif",
)
GOAL_OPTIONS = (
    "Menurunkan berat badan",
    "Mempertahankan berat badan",
    "Menaikkan berat badan",
)

PROFILE_CALLBACK_PREFIX = "profile_choice"
PROFILE_EDIT_CALLBACK = "profile:edit"
MENU_CALLBACK_PREFIX = "menu:"
MENU_RECORD_CALLBACK = "menu:record"
MENU_TODAY_CALLBACK = "menu:today"
MENU_TARGET_CALLBACK = "menu:target"
MENU_PROFILE_CALLBACK = "menu:profile"
MENU_HELP_CALLBACK = "menu:help"

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
MEAL_SAVE_CALLBACK = "meal:save"
MEAL_CORRECT_CALLBACK = "meal:correct"
MEAL_CANCEL_CALLBACK = "meal:cancel"

CONFIRMED_MESSAGE = "✅ Porsi dikonfirmasi."

# Key under which the latest detection is kept in Telegram's in-memory user_data.
LAST_ANALYSIS_KEY = "last_analysis"
MEAL_NUTRITION_KEY = "meal_nutrition"
MEAL_INPUT_SOURCE_KEY = "meal_input_source"
MEAL_AWAITING_CORRECTION_KEY = "meal_awaiting_correction"

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

CORRECTION_MESSAGE = (
    "✏️ Kirim daftar makanan yang benar beserta beratnya.\n\n"
    "Contoh:\nayam goreng 100 gram\nnasi putih 150 gram\n\n"
    "Pisahkan beberapa makanan dengan baris baru atau tanda titik koma."
)

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


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🍽 Catat Makan", callback_data=MENU_RECORD_CALLBACK),
                InlineKeyboardButton("📊 Hari Ini", callback_data=MENU_TODAY_CALLBACK),
            ],
            [
                InlineKeyboardButton("🎯 Target", callback_data=MENU_TARGET_CALLBACK),
                InlineKeyboardButton("👤 Profil", callback_data=MENU_PROFILE_CALLBACK),
            ],
            [InlineKeyboardButton("❓ Bantuan", callback_data=MENU_HELP_CALLBACK)],
        ]
    )


def profile_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("✏️ Ubah Profil", callback_data=PROFILE_EDIT_CALLBACK)]]
    rows.extend(main_menu_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(rows)


def _message_for(update: Update):
    """Return the usable message for both commands and inline-button callbacks."""
    return getattr(update, "effective_message", None) or update.message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("/start from user_id=%s", user.id if user else "unknown")
    await _message_for(update).reply_text(START_MESSAGE, reply_markup=main_menu_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("/help from user_id=%s", user.id if user else "unknown")
    await _message_for(update).reply_text(HELP_MESSAGE, reply_markup=main_menu_keyboard())


def _inline_choice_keyboard(group: str, options: tuple[str, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    option,
                    callback_data=f"{PROFILE_CALLBACK_PREFIX}:{group}:{index}",
                )
            ]
            for index, option in enumerate(options)
        ]
    )


def meal_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Simpan", callback_data=MEAL_SAVE_CALLBACK)],
            [
                InlineKeyboardButton("✏️ Koreksi", callback_data=MEAL_CORRECT_CALLBACK),
                InlineKeyboardButton("❌ Batal", callback_data=MEAL_CANCEL_CALLBACK),
            ],
        ]
    )


def _profile_text(profile: UserProfile) -> str:
    height = f"{profile.height_cm:g}"
    weight = f"{profile.weight_kg:g}"
    return "\n".join(
        [
            "👤 Profil NutruFood",
            "",
            f"🎂 Umur: {profile.age} tahun",
            f"{'🚹' if profile.gender == 'Laki-laki' else '🚺'} Jenis kelamin: {profile.gender}",
            f"📏 Tinggi: {height} cm",
            f"⚖️ Berat: {weight} kg",
            f"🏃 Aktivitas: {profile.activity_level}",
            f"🎯 Tujuan: {profile.goal}",
        ]
    )


def _draft_profile(user_id: int, draft: dict) -> UserProfile:
    return UserProfile(telegram_user_id=user_id, **draft)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    profile = get_profile_store().get(user.id)
    if profile is not None:
        await _message_for(update).reply_text(_profile_text(profile), reply_markup=profile_keyboard())
        return ConversationHandler.END

    await _message_for(update).reply_text(
        "Profil Anda belum tersedia. Mari isi sekarang.\n\nBerapa umur Anda? "
        "Masukkan angka dalam tahun (13–120)."
    )
    context.user_data[PROFILE_DRAFT_KEY] = {}
    return PROFILE_AGE


async def setup_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[PROFILE_DRAFT_KEY] = {}
    await update.message.reply_text(
        "Mari isi profil NutruFood Anda. Profil lama baru akan diganti setelah "
        "Anda mengonfirmasi semua data.\n\nBerapa umur Anda? "
        "Masukkan angka dalam tahun (13–120).",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PROFILE_AGE


async def setup_profile_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    context.user_data[PROFILE_DRAFT_KEY] = {}
    await query.message.reply_text(
        "Mari isi profil NutruFood Anda. Profil lama baru akan diganti setelah "
        "Anda mengonfirmasi semua data.\n\nBerapa umur Anda? "
        "Masukkan angka dalam tahun (13–120)."
    )
    return PROFILE_AGE


async def receive_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        value = int(update.message.text.strip())
    except (AttributeError, ValueError):
        value = 0
    if not 13 <= value <= 120:
        await update.message.reply_text("Umur belum valid. Masukkan angka 13–120.")
        return PROFILE_AGE
    context.user_data[PROFILE_DRAFT_KEY]["age"] = value
    await update.message.reply_text(
        "Pilih jenis kelamin:",
        reply_markup=_inline_choice_keyboard("gender", GENDER_OPTIONS),
    )
    return PROFILE_GENDER


async def receive_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_value = update.message.text.strip().lower()
    gender_aliases = {
        "pria": "Laki-laki",
        "laki laki": "Laki-laki",
        "laki-laki": "Laki-laki",
        "wanita": "Perempuan",
        "perempuan": "Perempuan",
    }
    value = gender_aliases.get(raw_value, update.message.text.strip().capitalize())
    if value not in GENDER_OPTIONS:
        await update.message.reply_text(
            "Silakan pilih Laki-laki atau Perempuan melalui tombol di bawah.",
            reply_markup=_inline_choice_keyboard("gender", GENDER_OPTIONS),
        )
        return PROFILE_GENDER
    context.user_data[PROFILE_DRAFT_KEY]["gender"] = value
    await update.message.reply_text(
        "Berapa tinggi badan Anda dalam sentimeter? Contoh: 170",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PROFILE_HEIGHT


def _parse_decimal(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except (AttributeError, ValueError):
        return None


async def receive_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _parse_decimal(update.message.text)
    if value is None or not 50 <= value <= 250:
        await update.message.reply_text("Tinggi belum valid. Masukkan angka 50–250 cm.")
        return PROFILE_HEIGHT
    context.user_data[PROFILE_DRAFT_KEY]["height_cm"] = value
    await update.message.reply_text("Berapa berat badan Anda dalam kilogram? Contoh: 65,5")
    return PROFILE_WEIGHT


async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _parse_decimal(update.message.text)
    if value is None or not 20 <= value <= 500:
        await update.message.reply_text("Berat belum valid. Masukkan angka 20–500 kg.")
        return PROFILE_WEIGHT
    context.user_data[PROFILE_DRAFT_KEY]["weight_kg"] = value
    await update.message.reply_text(
        "Pilih tingkat aktivitas Anda:\n\n"
        "Sangat ringan: hampir tidak berolahraga\n"
        "Ringan: olahraga ringan 1–3 hari/minggu\n"
        "Sedang: olahraga 3–5 hari/minggu\n"
        "Aktif: olahraga berat 6–7 hari/minggu\n"
        "Sangat aktif: aktivitas fisik sangat berat",
        reply_markup=_inline_choice_keyboard("activity", ACTIVITY_OPTIONS),
    )
    return PROFILE_ACTIVITY


async def receive_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip().capitalize()
    if value not in ACTIVITY_OPTIONS:
        await update.message.reply_text(
            "Silakan pilih salah satu tingkat aktivitas yang tersedia.",
            reply_markup=_inline_choice_keyboard("activity", ACTIVITY_OPTIONS),
        )
        return PROFILE_ACTIVITY
    context.user_data[PROFILE_DRAFT_KEY]["activity_level"] = value
    await update.message.reply_text(
        "Apa tujuan Anda?", reply_markup=_inline_choice_keyboard("goal", GOAL_OPTIONS)
    )
    return PROFILE_GOAL


async def receive_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip().capitalize()
    if value not in GOAL_OPTIONS:
        await update.message.reply_text(
            "Silakan pilih salah satu tujuan yang tersedia.",
            reply_markup=_choice_keyboard(GOAL_OPTIONS),
        )
        return PROFILE_GOAL
    context.user_data[PROFILE_DRAFT_KEY]["goal"] = value
    profile = _draft_profile(update.effective_user.id, context.user_data[PROFILE_DRAFT_KEY])
    await update.message.reply_text(
        _profile_text(profile) + "\n\nApakah data ini sudah benar?",
        reply_markup=_inline_choice_keyboard("confirm", ("Ya, simpan", "Tidak, batalkan")),
    )
    return PROFILE_CONFIRM


async def confirm_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer == "ya, simpan":
        draft = context.user_data.get(PROFILE_DRAFT_KEY)
        if not draft:
            await update.message.reply_text(
                "Data pengisian tidak ditemukan. Silakan mulai lagi dengan /setup.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return ConversationHandler.END
        profile = _draft_profile(update.effective_user.id, draft)
        get_profile_store().save(profile)
        context.user_data.pop(PROFILE_DRAFT_KEY, None)
        await update.message.reply_text(
            "✅ Profil berhasil disimpan secara permanen. Gunakan /profil untuk melihatnya.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END
    if answer == "tidak, batalkan":
        return await cancel_profile(update, context)
    await update.message.reply_text(
        "Pilih “Ya, simpan” atau “Tidak, batalkan”.",
        reply_markup=_inline_choice_keyboard("confirm", ("Ya, simpan", "Tidak, batalkan")),
    )
    return PROFILE_CONFIRM


async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(PROFILE_DRAFT_KEY, None)
    await update.message.reply_text(
        "Pengisian profil dibatalkan. Profil lama, jika ada, tidak berubah.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def _profile_callback_value(data: str, group: str, options: tuple[str, ...]) -> str | None:
    prefix = f"{PROFILE_CALLBACK_PREFIX}:{group}:"
    if not data.startswith(prefix):
        return None
    try:
        return options[int(data.removeprefix(prefix))]
    except (ValueError, IndexError):
        return None


async def choose_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = _profile_callback_value(query.data, "gender", GENDER_OPTIONS)
    if value is None:
        await query.message.reply_text("Pilihan tidak dikenali. Silakan pilih kembali.")
        return PROFILE_GENDER
    context.user_data[PROFILE_DRAFT_KEY]["gender"] = value
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Berapa tinggi badan Anda dalam sentimeter? Contoh: 170")
    return PROFILE_HEIGHT


async def choose_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = _profile_callback_value(query.data, "activity", ACTIVITY_OPTIONS)
    if value is None:
        await query.message.reply_text("Pilihan tidak dikenali. Silakan pilih kembali.")
        return PROFILE_ACTIVITY
    context.user_data[PROFILE_DRAFT_KEY]["activity_level"] = value
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "Apa tujuan Anda?", reply_markup=_inline_choice_keyboard("goal", GOAL_OPTIONS)
    )
    return PROFILE_GOAL


async def choose_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = _profile_callback_value(query.data, "goal", GOAL_OPTIONS)
    if value is None:
        await query.message.reply_text("Pilihan tidak dikenali. Silakan pilih kembali.")
        return PROFILE_GOAL
    context.user_data[PROFILE_DRAFT_KEY]["goal"] = value
    await query.edit_message_reply_markup(reply_markup=None)
    profile = _draft_profile(update.effective_user.id, context.user_data[PROFILE_DRAFT_KEY])
    await query.message.reply_text(
        _profile_text(profile) + "\n\nApakah data ini sudah benar?",
        reply_markup=_inline_choice_keyboard("confirm", ("Ya, simpan", "Tidak, batalkan")),
    )
    return PROFILE_CONFIRM


async def choose_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = _profile_callback_value(
        query.data, "confirm", ("Ya, simpan", "Tidak, batalkan")
    )
    if value is None:
        await query.message.reply_text("Pilihan tidak dikenali. Silakan pilih kembali.")
        return PROFILE_CONFIRM
    await query.edit_message_reply_markup(reply_markup=None)
    if value == "Tidak, batalkan":
        context.user_data.pop(PROFILE_DRAFT_KEY, None)
        await query.message.reply_text(
            "Pengisian profil dibatalkan. Profil lama, jika ada, tidak berubah."
        )
        return ConversationHandler.END
    draft = context.user_data.get(PROFILE_DRAFT_KEY)
    if not draft:
        await query.message.reply_text(
            "Data pengisian tidak ditemukan. Silakan mulai lagi dengan /setup."
        )
        return ConversationHandler.END
    get_profile_store().save(_draft_profile(update.effective_user.id, draft))
    context.user_data.pop(PROFILE_DRAFT_KEY, None)
    await query.message.reply_text(
        "✅ Profil berhasil disimpan secara permanen. Gunakan /profil untuk melihatnya."
    )
    return ConversationHandler.END


async def target_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    profile = get_profile_store().get(user.id)
    if profile is None:
        await _message_for(update).reply_text(
            "Profil Anda belum tersedia. Gunakan /setup terlebih dahulu agar target dapat dihitung."
        )
        return

    try:
        target = calculate_daily_targets(profile)
    except TargetCalculationError as exc:
        await _message_for(update).reply_text(
            f"Target belum dapat dihitung: {exc}\n\n"
            "Perbarui profil dengan /setup atau konsultasikan kebutuhan khusus dengan tenaga kesehatan."
        )
        return

    goal_icon = {
        "Menurunkan berat badan": "📉",
        "Mempertahankan berat badan": "⚖️",
        "Menaikkan berat badan": "📈",
    }.get(target.goal, "🎯")
    await _message_for(update).reply_text(
        "\n".join(
            [
                "🎯 Target Harian Anda",
                "",
                f"🔥 Kalori: {target.calories_kcal:,} kcal".replace(",", "."),
                f"🥩 Protein: {target.protein_g} g",
                f"🍚 Karbohidrat: {target.carbs_g} g",
                f"🥑 Lemak: {target.fat_g} g",
                f"🥬 Serat: {target.fiber_g} g",
                "",
                "Batas harian:",
                f"🍬 Gula tambahan/bebas: maks. {target.added_sugar_max_g} g",
                f"🧂 Sodium: maks. {target.sodium_max_mg:,} mg".replace(",", "."),
                "",
                "Tujuan:",
                f"{goal_icon} {target.goal}",
                "",
                "ℹ️ Angka ini merupakan estimasi umum berdasarkan profil Anda, "
                "bukan diagnosis atau resep medis.",
            ]
        ),
        reply_markup=main_menu_keyboard(),
    )


def _recap_value(value: float | None, target: int, unit: str) -> str:
    if value is None:
        return f"belum tersedia / target {target:,} {unit}".replace(",", ".")
    consumed = f"{value:.0f}"
    return f"{consumed} / {target:,} {unit}".replace(",", ".")


def _recap_progress_lines(
    emoji: str,
    label: str,
    value: float | None,
    reference: int,
    unit: str,
    *,
    kind: str = "target",
) -> list[str]:
    if value is None:
        return [f"{emoji} {label}", "Data belum tersedia"]
    progress = format_progress(value, reference, kind=kind)
    if progress is None:  # Guard for type safety; value was checked above.
        return [f"{emoji} {label}", "Data belum tersedia"]
    return [
        f"{emoji} {label}",
        f"{format_number(value)} / {format_number(reference)} {unit}",
        progress,
    ]


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    profile = get_profile_store().get(user.id)
    if profile is None:
        await _message_for(update).reply_text(
            "Profil Anda belum tersedia. Gunakan /setup terlebih dahulu untuk melihat rekap personal."
        )
        return
    try:
        target = calculate_daily_targets(profile)
    except TargetCalculationError as exc:
        await _message_for(update).reply_text(
            f"Rekap belum dapat dibandingkan dengan target: {exc}\n\n"
            "Perbarui profil melalui /setup."
        )
        return

    recap = build_daily_recap(user.id, get_meal_store())
    if not recap.entries:
        await _message_for(update).reply_text(
            "📊 Belum ada makanan yang disimpan hari ini.\n\n"
            "Kirim foto makanan atau gunakan /catat untuk mulai mencatat.",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = ["📊 NutruFood Hari Ini"]
    nutrient_blocks = [
        _recap_progress_lines("🔥", "Kalori", recap.calories, target.calories_kcal, "kcal"),
        _recap_progress_lines("🥩", "Protein", recap.protein, target.protein_g, "g"),
        _recap_progress_lines("🍚", "Karbohidrat", recap.carbs, target.carbs_g, "g"),
        _recap_progress_lines("🥑", "Lemak", recap.fat, target.fat_g, "g"),
    ]
    if recap.fiber is not None:
        nutrient_blocks.append(
            _recap_progress_lines("🥬", "Serat", recap.fiber, target.fiber_g, "g")
        )
    if recap.added_sugar is not None:
        nutrient_blocks.append(
            _recap_progress_lines(
                "🍬", "Gula tambahan", recap.added_sugar,
                target.added_sugar_max_g, "g", kind="limit"
            )
        )
    if recap.sodium is not None:
        nutrient_blocks.append(
            _recap_progress_lines(
                "🧂", "Sodium", recap.sodium, target.sodium_max_mg,
                "mg", kind="limit"
            )
        )
    for block in nutrient_blocks:
        lines.extend(["", *block])

    now_local = datetime.now(timezone.utc).astimezone(JAKARTA)
    warnings = build_nutrition_warnings(recap, target, now_local)
    if warnings:
        warning_icons = {
            "sodium": "🧂", "sugar": "🍬", "calories": "🔥",
            "fat": "🥑", "protein": "🥩", "fiber": "🥬",
        }
        lines.extend(["", "⚠️ Perhatian Hari Ini", ""])
        for warning in warnings:
            category = warning.code.split("_", 1)[0]
            lines.append(f"{warning_icons.get(category, '•')} {warning.message}")
    if recap.has_partial_nutrition:
        lines.extend(
            [
                "",
                f"⚠️ Total nutrisi hanya mencakup {recap.known_nutrition_items} dari "
                f"{recap.total_items} makanan. Makanan tanpa data tidak dianggap nol.",
            ]
        )
    elif recap.known_nutrition_items == 0:
        lines.extend(
            [
                "",
                "⚠️ Nutrisi makanan hari ini belum tersedia; makanan tetap tercatat dan tidak dianggap nol.",
            ]
        )

    lines.extend(["", "🍽 Makanan Hari Ini", ""])
    for entry in recap.entries:
        suffix = "" if entry.nutrition_available else " — nutrisi belum tersedia"
        lines.append(f"• {entry.eaten_at_local:%H:%M}  {entry.name.capitalize()} — {entry.grams} g{suffix}")
    advice = choose_daily_advice(recap, target, now_local)
    lines.extend(["", "💡 Saran Hari Ini", "", advice.message])
    lines.extend(["", "Zona waktu: Asia/Jakarta"])
    await _message_for(update).reply_text("\n".join(lines), reply_markup=main_menu_keyboard())


async def record_meal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[MEAL_AWAITING_CORRECTION_KEY] = True
    context.user_data[MEAL_INPUT_SOURCE_KEY] = "text"
    await _message_for(update).reply_text(
        "🍽️ Kirim makanan dan porsinya.\n\n"
        "Contoh:\nnasi putih 150 gram\n2 telur rebus\n\n"
        "Untuk beberapa makanan, pisahkan dengan baris baru atau tanda titik koma."
    )


async def cancel_meal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    had_draft = any(
        key in context.user_data
        for key in (LAST_ANALYSIS_KEY, MEAL_NUTRITION_KEY, MEAL_AWAITING_CORRECTION_KEY)
    )
    _clear_meal_draft(context)
    message = (
        "❌ Pencatatan makanan dibatalkan."
        if had_draft
        else "Tidak ada pencatatan makanan yang sedang berlangsung."
    )
    await update.message.reply_text(message)


async def handle_main_menu_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route visible menu buttons to the existing product flows."""
    query = update.callback_query
    await query.answer()

    if query.data == MENU_RECORD_CALLBACK:
        await record_meal_command(update, context)
    elif query.data == MENU_TODAY_CALLBACK:
        await today_command(update, context)
    elif query.data == MENU_TARGET_CALLBACK:
        await target_command(update, context)
    elif query.data == MENU_HELP_CALLBACK:
        await help_command(update, context)
    elif query.data == MENU_PROFILE_CALLBACK:
        profile = get_profile_store().get(update.effective_user.id)
        if profile is None:
            await query.message.reply_text(
                "Profil Anda belum tersedia. Tekan tombol Isi Profil untuk mulai.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✏️ Isi Profil", callback_data=PROFILE_EDIT_CALLBACK)]]
                ),
            )
        else:
            await profile_command(update, context)


def profile_conversation() -> ConversationHandler:
    text = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[
            CommandHandler("profil", profile_command),
            CommandHandler("setup", setup_profile),
            CallbackQueryHandler(setup_profile_callback, pattern=f"^{PROFILE_EDIT_CALLBACK}$"),
        ],
        states={
            PROFILE_AGE: [MessageHandler(text, receive_age)],
            PROFILE_GENDER: [
                CallbackQueryHandler(choose_gender, pattern=f"^{PROFILE_CALLBACK_PREFIX}:gender:"),
                MessageHandler(text, receive_gender),
            ],
            PROFILE_HEIGHT: [MessageHandler(text, receive_height)],
            PROFILE_WEIGHT: [MessageHandler(text, receive_weight)],
            PROFILE_ACTIVITY: [
                CallbackQueryHandler(choose_activity, pattern=f"^{PROFILE_CALLBACK_PREFIX}:activity:"),
                MessageHandler(text, receive_activity),
            ],
            PROFILE_GOAL: [
                CallbackQueryHandler(choose_goal, pattern=f"^{PROFILE_CALLBACK_PREFIX}:goal:"),
                MessageHandler(text, receive_goal),
            ],
            PROFILE_CONFIRM: [
                CallbackQueryHandler(choose_confirmation, pattern=f"^{PROFILE_CALLBACK_PREFIX}:confirm:"),
                MessageHandler(text, confirm_profile),
            ],
        },
        fallbacks=[CommandHandler("batal", cancel_profile), CommandHandler("setup", setup_profile)],
    )


def _format_simple_analysis(analysis: FoodAnalysis) -> str:
    """Compact detection list; confidence remains internal for diagnostics."""
    lines = ["🍽 Makanan Terdeteksi", ""]

    for index, food in enumerate(analysis.foods, start=1):
        portion = f"±{food.estimated_grams} g"
        if food.serving_label:
            portion += f" ({food.serving_label})"

        lines.append(f"{index}. {food.name.capitalize()} — {portion}")

    if analysis.notes:
        lines.append("")
        lines.append(f"📝 {analysis.notes}")

    lines.append("")
    lines.append(PORTION_DISCLAIMER)
    return "\n".join(lines)


def _format_dish_analysis(analysis: FoodAnalysis, decomposed: DecomposedMeal) -> str:
    """Compound/variable/ambiguous layout: dish header + only visible components.

    Never renders a template component that isn't in `decomposed.foods` -
    that list is always exactly what Gemini reported seeing.
    """
    lines = ["🍽 Makanan Terdeteksi", "", f"{decomposed.dish_name.capitalize()}:"]

    for index, food in enumerate(decomposed.foods, start=1):
        portion = f"±{food.estimated_grams} g"
        if food.serving_label:
            portion += f" ({food.serving_label})"
        lines.append(f"{index}. {food.name.capitalize()} — {portion}")

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
    context.user_data[MEAL_INPUT_SOURCE_KEY] = "photo"
    context.user_data.pop(MEAL_NUTRITION_KEY, None)
    context.user_data.pop(MEAL_AWAITING_CORRECTION_KEY, None)
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


def format_meal_review(meal: MealNutrition) -> str:
    """Show the final food list and only nutrition values we can verify."""
    lines = ["🍽 Ringkasan Makanan", ""]
    for index, item in enumerate(meal.items, start=1):
        lines.append(f"{index}. {item.name.capitalize()} — {item.estimated_gross_grams} g")
        if item.status == UNMATCHED:
            lines.append("  Nutrisi: belum tersedia")
        elif item.status == UNVERIFIED:
            lines.append("  Nutrisi: belum terverifikasi (tidak dihitung)")

    lines.extend(["", "📊 Estimasi Nutrisi", ""])
    if meal.counted_items:
        total = meal.total.rounded()
        lines.extend(
            [
                f"🔥 Kalori: {total.calories:.0f} kcal",
                f"🥩 Protein: {total.protein:.1f} g",
                f"🍚 Karbohidrat: {total.carbs:.1f} g",
                f"🥑 Lemak: {total.fat:.1f} g",
            ]
        )
    else:
        lines.append("⚠️ Belum ada data nutrisi terverifikasi untuk dihitung.")
    lines.extend(["", "Simpan catatan makan ini?"])
    return "\n".join(lines)


async def _calculate_and_show_review(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    analysis: FoodAnalysis,
) -> None:
    try:
        meal = await calculate_meal(analysis.foods, usda=get_usda_client())
    except Exception:
        logger.exception("Nutrition calculation failed while preparing meal review")
        await update.effective_message.reply_text(NUTRITION_FAILED_MESSAGE)
        return
    context.user_data[LAST_ANALYSIS_KEY] = analysis
    context.user_data[MEAL_NUTRITION_KEY] = meal
    context.user_data.pop(MEAL_AWAITING_CORRECTION_KEY, None)
    await update.effective_message.reply_text(
        format_meal_review(meal), reply_markup=meal_review_keyboard()
    )


async def handle_meal_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_correction = bool(context.user_data.get(MEAL_AWAITING_CORRECTION_KEY))
    try:
        foods = parse_meal_text(update.message.text or "")
    except MealInputError as exc:
        await update.message.reply_text(
            f"⚠️ {exc}\n\n"
            "Gunakan format seperti: nasi putih 150 gram. Ketik /batal untuk membatalkan."
        )
        return

    if not is_correction:
        context.user_data[MEAL_INPUT_SOURCE_KEY] = "text"
    await _calculate_and_show_review(update, context, FoodAnalysis(foods=foods))


def _clear_meal_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        LAST_ANALYSIS_KEY,
        MEAL_NUTRITION_KEY,
        MEAL_INPUT_SOURCE_KEY,
        MEAL_AWAITING_CORRECTION_KEY,
    ):
        context.user_data.pop(key, None)


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
        if context.user_data.get(LAST_ANALYSIS_KEY) is None:
            await query.message.reply_text(NO_ANALYSIS_MESSAGE)
            return
        context.user_data[MEAL_AWAITING_CORRECTION_KEY] = True
        await query.message.reply_text(CORRECTION_MESSAGE)
        return

    analysis = context.user_data.get(LAST_ANALYSIS_KEY)
    if analysis is None:
        # user_data is in-memory only, so it is lost on restart.
        await query.message.reply_text(NO_ANALYSIS_MESSAGE)
        return

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

    context.user_data[MEAL_NUTRITION_KEY] = meal
    await query.message.reply_text(
        format_meal_review(meal), reply_markup=meal_review_keyboard()
    )


async def handle_meal_review_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == MEAL_CANCEL_CALLBACK:
        _clear_meal_draft(context)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "❌ Catatan makan dibatalkan dan tidak disimpan.",
            reply_markup=main_menu_keyboard(),
        )
        return

    meal = context.user_data.get(MEAL_NUTRITION_KEY)
    if meal is None:
        await query.message.reply_text(
            "Data makanan tidak ditemukan lagi. Silakan kirim foto atau masukkan makanan kembali."
        )
        return

    if query.data == MEAL_CORRECT_CALLBACK:
        context.user_data[MEAL_AWAITING_CORRECTION_KEY] = True
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(CORRECTION_MESSAGE)
        return

    input_source = context.user_data.get(MEAL_INPUT_SOURCE_KEY, "text")
    try:
        meal_id = get_meal_store().save(update.effective_user.id, input_source, meal)
    except Exception:
        logger.exception("Failed to save meal for user_id=%s", update.effective_user.id)
        await query.message.reply_text("⚠️ Catatan makan gagal disimpan. Silakan coba lagi.")
        return

    _clear_meal_draft(context)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ Catatan makan berhasil disimpan. ID catatan: {meal_id}.",
        reply_markup=main_menu_keyboard(),
    )


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
    application.add_handler(profile_conversation())
    application.add_handler(CommandHandler("target", target_command))
    application.add_handler(CommandHandler("hariini", today_command))
    application.add_handler(CommandHandler("catat", record_meal_command))
    application.add_handler(CommandHandler("batal", cancel_meal_command))
    application.add_handler(
        CallbackQueryHandler(handle_main_menu_callback, pattern=f"^{MENU_CALLBACK_PREFIX}")
    )
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(
        CallbackQueryHandler(
            handle_portion_callback, pattern=f"^({CONFIRM_CALLBACK}|{CORRECT_CALLBACK})$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_meal_review_callback,
            pattern=f"^({MEAL_SAVE_CALLBACK}|{MEAL_CORRECT_CALLBACK}|{MEAL_CANCEL_CALLBACK})$",
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_meal_text))
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
