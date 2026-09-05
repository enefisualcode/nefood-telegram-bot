"""Food recognition from a photo, using the Gemini API.

This module knows nothing about Telegram: it takes image bytes and returns
structured detection results. Phase 2 only detects foods and estimates
portions - no nutrition calculation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

PROMPT = """Kamu adalah asisten pengenalan makanan pada foto.

Tugasmu: identifikasi makanan dan minuman yang TERLIHAT pada foto ini, dan
sebutkan setiap komponen yang terlihat secara terpisah dalam daftar "foods".

Aturan dasar:
- Gunakan nama makanan dalam Bahasa Indonesia.
- Jangan mengarang makanan atau komponen yang tidak terlihat pada foto.
- Deteksi beberapa makanan/komponen bila memang terlihat lebih dari satu.
- Perkirakan berat porsi dalam gram sebagai bilangan bulat untuk SETIAP
  komponen. Perkiraan ini bersifat kasar dan hanya berdasarkan tampilan foto.
- identification_confidence (0-1): seberapa yakin kamu bahwa identitas
  makanan tersebut benar.
- portion_confidence (0-1): seberapa yakin kamu bahwa perkiraan beratnya
  akurat. Nilai ini biasanya LEBIH RENDAH daripada identification_confidence,
  terutama bila tidak ada acuan ukuran (piring, sendok, tangan) pada foto.
- serving_label: takaran singkat dan wajar dalam Bahasa Indonesia,
  misalnya "1 potong", "2 sdm", "1 iris", "1 mangkuk", "1 porsi kecil".
- Bila ragu, beri nilai confidence yang lebih rendah.
- JANGAN menghitung kalori, protein, karbohidrat, atau lemak.
- JANGAN menyertakan nilai BDD (bagian dapat dimakan) atau nilai gizi
  apa pun - tugasmu murni visual, bukan basis data gizi.
- Bila tidak ada makanan yang dapat dikenali, kembalikan daftar foods kosong.
- notes bersifat opsional dan harus singkat (satu kalimat).

Hidangan majemuk (compound dish) - dish_name dan dish_type:
- Bila foto menunjukkan hidangan Indonesia yang dikenal tersusun dari
  beberapa komponen (misalnya pecel lele, nasi uduk, nasi goreng, mie ayam,
  bakso, soto ayam, gado-gado, ketoprak, martabak telur, martabak manis):
  isi "dish_name" dengan nama hidangan itu, set "dish_type" = "compound",
  dan sebutkan SETIAP komponen yang benar-benar TERLIHAT sebagai entri
  terpisah di "foods" (misalnya untuk pecel lele: lele goreng, sambal, kol,
  timun, dan kemangi HANYA jika kemangi benar-benar terlihat pada foto -
  jangan sertakan komponen yang biasanya ada pada hidangan itu tetapi tidak
  terlihat di foto ini).
- JANGAN PERNAH menambahkan bahan resep tersembunyi yang tidak mungkin
  terlihat di foto (contoh: santan, minyak goreng, garam, jumlah butir
  telur di dalam adonan, gram daging cincang di dalam martabak). Sistem ini
  memperkirakan komponen yang TERLIHAT, bukan formulasi resep.
- Bila foto menunjukkan "nasi padang" (nasi dengan pilihan lauk yang sangat
  beragam): isi "dish_name" = "nasi padang", "dish_type" = "variable", dan
  sebutkan SETIAP lauk yang benar-benar terlihat sebagai entri terpisah di
  "foods" (misalnya nasi putih, rendang, sambal ijo, daun singkong) -
  JANGAN membuat satu entri generik "nasi padang".
- Bila foto menunjukkan martabak tetapi kamu TIDAK dapat memastikan apakah
  itu martabak telur (gurih) atau martabak manis: isi "dish_name" =
  "martabak", "dish_type" = "ambiguous", dan JANGAN menebak salah satu jenis.
  Bila ada bukti visual yang jelas (misalnya terlihat isian telur/daging vs.
  terlihat topping cokelat/keju manis), isi "dish_name" dengan jenis
  spesifiknya ("martabak telur" atau "martabak manis") dan "dish_type" =
  "compound".
- Bila foto hanya menunjukkan makanan sederhana tanpa hidangan majemuk yang
  dikenali, biarkan "dish_name" kosong dan "dish_type" = "simple"."""

DISH_TYPE_SIMPLE = "simple"
DISH_TYPE_COMPOUND = "compound"
DISH_TYPE_VARIABLE = "variable"
DISH_TYPE_AMBIGUOUS = "ambiguous"
DISH_TYPES = (DISH_TYPE_SIMPLE, DISH_TYPE_COMPOUND, DISH_TYPE_VARIABLE, DISH_TYPE_AMBIGUOUS)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "dish_name": {"type": "string"},
        "dish_type": {"type": "string", "enum": list(DISH_TYPES)},
        "foods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "estimated_grams": {"type": "integer"},
                    "identification_confidence": {"type": "number"},
                    "portion_confidence": {"type": "number"},
                    "serving_label": {"type": "string"},
                },
                "required": [
                    "name",
                    "estimated_grams",
                    "identification_confidence",
                    "portion_confidence",
                ],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["foods"],
}


class FoodVisionError(RuntimeError):
    """Raised when the image could not be analysed."""


@dataclass(frozen=True)
class DetectedFood:
    name: str
    estimated_grams: int
    identification_confidence: float
    portion_confidence: float
    serving_label: str = ""


@dataclass(frozen=True)
class FoodAnalysis:
    foods: list[DetectedFood]
    notes: str = ""
    # Phase 3F: the model's own guess at an overarching dish name/type, e.g.
    # dish_name="pecel lele", dish_type="compound". `foods` above already
    # contains every visible component regardless of dish_type - these two
    # fields are presentation hints only, and are NEVER trusted on their own
    # for classification (see services/dish_decomposition.py, which
    # re-derives the authoritative dish_type from services/dish_matcher.py
    # instead of taking the model's dish_type at face value). Empty
    # dish_name means "no recognized compound dish" - the common case.
    dish_name: str = ""
    dish_type: str = DISH_TYPE_SIMPLE


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Create the Gemini client lazily so importing this module is side-effect free."""
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise FoodVisionError("GEMINI_API_KEY is not configured")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _unit_float(raw: Any, default: float = 0.0) -> float:
    """Read a 0-1 confidence, clamping anything odd the model might send."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return min(max(value, 0.0), 1.0)


def _coerce_food(raw: Any) -> DetectedFood | None:
    """Turn one raw model entry into a DetectedFood, or None if unusable."""
    if not isinstance(raw, dict):
        return None

    name = str(raw.get("name", "")).strip()
    if not name:
        return None

    try:
        grams = int(round(float(raw.get("estimated_grams", 0))))
    except (TypeError, ValueError):
        grams = 0

    return DetectedFood(
        name=name,
        estimated_grams=max(grams, 0),
        identification_confidence=_unit_float(raw.get("identification_confidence")),
        portion_confidence=_unit_float(raw.get("portion_confidence")),
        serving_label=str(raw.get("serving_label") or "").strip(),
    )


def _parse(text: str) -> FoodAnalysis:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FoodVisionError("model did not return valid JSON") from exc

    if not isinstance(payload, dict):
        raise FoodVisionError("model returned JSON that is not an object")

    raw_foods = payload.get("foods")
    if not isinstance(raw_foods, list):
        raise FoodVisionError("model response is missing a 'foods' list")

    foods = [food for food in map(_coerce_food, raw_foods) if food is not None]
    notes = str(payload.get("notes") or "").strip()

    # dish_name/dish_type are read defensively: only these two whitelisted
    # fields are ever pulled from the payload for this purpose, so a model
    # response that (against its instructions) included nutrition-shaped
    # keys anywhere has nothing to attach them to - DetectedFood and
    # FoodAnalysis simply have no such fields to populate.
    dish_name = str(payload.get("dish_name") or "").strip()
    dish_type = str(payload.get("dish_type") or "").strip().lower()
    if dish_type not in DISH_TYPES:
        dish_type = DISH_TYPE_SIMPLE

    return FoodAnalysis(foods=foods, notes=notes, dish_name=dish_name, dish_type=dish_type)


async def analyze_food_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> FoodAnalysis:
    """Detect the foods visible in an image.

    Raises FoodVisionError on configuration problems, API failures, or
    unparseable model output.
    """
    client = _get_client()

    try:
        response = await client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.2,
            ),
        )
    except Exception as exc:  # the SDK raises several unrelated error types
        logger.exception("Gemini API call failed")
        raise FoodVisionError("Gemini API request failed") from exc

    text = (response.text or "").strip()
    if not text:
        raise FoodVisionError("model returned an empty response")

    analysis = _parse(text)
    logger.info("Detected %d food item(s)", len(analysis.foods))
    return analysis
