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

Tugasmu: identifikasi makanan dan minuman yang TERLIHAT pada foto ini.

Aturan:
- Gunakan nama makanan dalam Bahasa Indonesia.
- Jangan mengarang makanan yang tidak terlihat pada foto.
- Deteksi beberapa makanan bila memang terlihat lebih dari satu.
- Perkirakan berat porsi dalam gram sebagai bilangan bulat. Perkiraan ini
  bersifat kasar dan hanya berdasarkan tampilan foto.
- identification_confidence (0-1): seberapa yakin kamu bahwa identitas
  makanan tersebut benar.
- portion_confidence (0-1): seberapa yakin kamu bahwa perkiraan beratnya
  akurat. Nilai ini biasanya LEBIH RENDAH daripada identification_confidence,
  terutama bila tidak ada acuan ukuran (piring, sendok, tangan) pada foto.
- serving_label: takaran singkat dan wajar dalam Bahasa Indonesia,
  misalnya "1 potong", "2 sdm", "1 iris", "1 mangkuk", "1 porsi kecil".
- Bila ragu, beri nilai confidence yang lebih rendah.
- JANGAN menghitung kalori, protein, karbohidrat, atau lemak.
- Bila tidak ada makanan yang dapat dikenali, kembalikan daftar foods kosong.
- notes bersifat opsional dan harus singkat (satu kalimat)."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
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

    return FoodAnalysis(foods=foods, notes=notes)


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
