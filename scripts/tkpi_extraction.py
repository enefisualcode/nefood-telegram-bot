"""Core data model and validation for the TKPI 2020 extraction pipeline.

Phase 3E. This module has one job: turn a raw transcribed row into a
TkpiCandidate with an honest extraction_status, and validate a whole batch of
candidates for structural problems (bad ranges, missing fields, duplicate
codes). It does not decide that a row is *correct* - only that it is
*well-formed enough to review*.

Two statuses are tracked separately, on purpose:

- extraction_status: parsed / needs_review / rejected - about the row's
  structural shape (did the transcription produce a usable row?).
- verification_status: unverified / spot_checked / officially_verified -
  about whether a human has independently checked the row's values against
  the rendered original document. A row can be "parsed" and "unverified" at
  the same time - parsing successfully never implies verification.

Nothing here reaches production. See scripts/tkpi_promotion.py for the
separate, explicit approval step required before any candidate can influence
data/foods.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Status vocabularies
# --------------------------------------------------------------------------

# extraction_status: about the shape of the transcription.
PARSED = "parsed"
NEEDS_REVIEW = "needs_review"
REJECTED = "rejected"
EXTRACTION_STATUSES = (PARSED, NEEDS_REVIEW, REJECTED)

# verification_status: about independent confirmation against the source.
UNVERIFIED = "unverified"
SPOT_CHECKED = "spot_checked"
OFFICIALLY_VERIFIED = "officially_verified"
VERIFICATION_STATUSES = (UNVERIFIED, SPOT_CHECKED, OFFICIALLY_VERIFIED)

# extraction_method: how the row's values were produced. Manual visual
# transcription is not automated OCR and must never be labelled as such.
MANUAL_VISUAL_TRANSCRIPTION = "manual_visual_transcription"
AUTOMATED_OCR = "automated_ocr"
EXTRACTION_METHODS = (MANUAL_VISUAL_TRANSCRIPTION, AUTOMATED_OCR)

# TKPI 2020 food codes observed in the source: two uppercase letters
# (food-group prefix, e.g. AP/AR/BR/DR) followed by three digits.
FOOD_CODE_PATTERN = re.compile(r"^[A-Z]{2}\d{3}$")

REQUIRED_FIELDS = (
    "source_food_code",
    "source_food_name",
    "calories_per_100g",
    "protein_per_100g",
    "fat_per_100g",
    "carbs_per_100g",
)


class TkpiExtractionError(RuntimeError):
    """Raised when the candidate file itself cannot be read."""


@dataclass(frozen=True)
class TkpiCandidate:
    """One row transcribed from the official TKPI 2020 source.

    Every field here is provenance, not just data - a row with no page
    reference or no extraction method is not trustworthy no matter what its
    numbers say.
    """

    source_food_code: str
    source_food_name: str
    calories_per_100g: float | None
    protein_per_100g: float | None
    fat_per_100g: float | None
    carbs_per_100g: float | None
    bdd_percent: float | None = None
    source: str = ""
    source_version: str = ""
    source_page: str = ""
    source_reference: str = ""
    extraction_method: str = MANUAL_VISUAL_TRANSCRIPTION
    extraction_status: str = NEEDS_REVIEW
    verification_status: str = UNVERIFIED
    extraction_notes: str = ""
    # Additive, optional: the TKPI source's own internal row identifier
    # (e.g. "KZGPI-1990"), when legible. Not one of the required fields.
    source_internal_row: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def valid_food_code(code) -> bool:
    return isinstance(code, str) and bool(FOOD_CODE_PATTERN.match(code.strip()))


def valid_bdd(value) -> bool:
    """BDD must be a number in [0, 100] when present. None is allowed."""
    if value is None:
        return True
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return 0 <= number <= 100


def _nonnegative(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number >= 0


def validate_row(raw: dict) -> tuple[str, list[str]]:
    """Decide the extraction_status for one raw transcribed row.

    Returns (status, problems). `problems` is a human-readable list of every
    issue found - even when the row is only downgraded to needs_review, the
    specific reason is preserved so a reviewer knows what to check.

    This never repairs a value. An out-of-range or missing field always
    lowers the status; it is never clamped or guessed into shape.
    """
    problems: list[str] = []

    missing = [f for f in REQUIRED_FIELDS if raw.get(f) is None or raw.get(f) == ""]
    if missing:
        problems.append(f"missing required field(s): {', '.join(missing)}")
        return REJECTED, problems

    if not valid_food_code(raw.get("source_food_code")):
        problems.append(f"implausible food code: {raw.get('source_food_code')!r}")
        return REJECTED, problems

    for field_name in ("calories_per_100g", "protein_per_100g", "fat_per_100g", "carbs_per_100g"):
        if not _nonnegative(raw.get(field_name)):
            problems.append(f"{field_name} must be a non-negative number, got {raw.get(field_name)!r}")

    if problems:
        return REJECTED, problems

    if not valid_bdd(raw.get("bdd_percent")):
        problems.append(f"bdd_percent out of range [0, 100]: {raw.get('bdd_percent')!r}")

    if raw.get("extraction_method") not in EXTRACTION_METHODS:
        problems.append(f"unknown extraction_method: {raw.get('extraction_method')!r}")

    if not str(raw.get("source_page", "")).strip():
        problems.append("missing source_page")

    if not str(raw.get("source_reference", "")).strip():
        problems.append("missing source_reference")

    if raw.get("uncertain"):
        problems.append(raw.get("uncertainty_reason") or "flagged uncertain by the transcriber")

    if problems:
        return NEEDS_REVIEW, problems

    return PARSED, problems


def find_duplicate_codes(candidates: list[TkpiCandidate]) -> dict[str, int]:
    """Return {code: count} for every source_food_code appearing more than once."""
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.source_food_code] = counts.get(candidate.source_food_code, 0) + 1
    return {code: n for code, n in counts.items() if n > 1}


# High-risk value screen (Phase 3E section F): rows meeting any of these
# thresholds deserve extra scrutiny during the PDF audit, regardless of
# whether they otherwise "parsed" cleanly.
def high_risk_reasons(candidate: TkpiCandidate) -> list[str]:
    reasons = []
    if candidate.calories_per_100g is not None and candidate.calories_per_100g == 0 \
            and candidate.protein_per_100g == 0 and candidate.fat_per_100g == 0 \
            and candidate.carbs_per_100g == 0:
        reasons.append("all four macros are zero")
    if candidate.protein_per_100g is not None and candidate.protein_per_100g > 40:
        reasons.append(f"protein {candidate.protein_per_100g} > 40 g/100g")
    if candidate.carbs_per_100g is not None and candidate.carbs_per_100g > 90:
        reasons.append(f"carbs {candidate.carbs_per_100g} > 90 g/100g")
    if candidate.fat_per_100g is not None and candidate.fat_per_100g > 50:
        reasons.append(f"fat {candidate.fat_per_100g} > 50 g/100g")
    if candidate.bdd_percent is not None and candidate.bdd_percent < 30:
        reasons.append(f"BDD {candidate.bdd_percent}% < 30%")
    if candidate.calories_per_100g is not None and candidate.calories_per_100g > 600:
        reasons.append(f"calories {candidate.calories_per_100g} > 600 kcal/100g (unusually high)")
    for name in ("protein_per_100g", "fat_per_100g", "carbs_per_100g"):
        value = getattr(candidate, name)
        if value is not None:
            text = f"{value}"
            decimals = text.split(".")[1] if "." in text else ""
            if len(decimals) > 1:
                reasons.append(f"{name} has more precision than the source table (1 decimal): {value}")
    return reasons


def candidate_from_raw(raw: dict) -> TkpiCandidate:
    """Build a TkpiCandidate from a raw transcribed dict, validating as it goes."""
    status, problems = validate_row(raw)
    notes = str(raw.get("extraction_notes", "")).strip()
    if problems:
        problem_text = "; ".join(problems)
        notes = f"{notes} [{problem_text}]".strip() if notes else f"[{problem_text}]"

    def _num(key):
        value = raw.get(key)
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return TkpiCandidate(
        source_food_code=str(raw.get("source_food_code", "")).strip(),
        source_food_name=str(raw.get("source_food_name", "")).strip(),
        calories_per_100g=_num("calories_per_100g"),
        protein_per_100g=_num("protein_per_100g"),
        fat_per_100g=_num("fat_per_100g"),
        carbs_per_100g=_num("carbs_per_100g"),
        bdd_percent=_num("bdd_percent"),
        source=str(raw.get("source", "")),
        source_version=str(raw.get("source_version", "")),
        source_page=str(raw.get("source_page", "")),
        source_reference=str(raw.get("source_reference", "")),
        extraction_method=str(raw.get("extraction_method", MANUAL_VISUAL_TRANSCRIPTION)),
        extraction_status=status,
        verification_status=str(raw.get("verification_status", UNVERIFIED)),
        extraction_notes=notes,
        source_internal_row=str(raw.get("source_internal_row", "")),
    )


def load_candidates(path: Path) -> list[TkpiCandidate]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TkpiExtractionError(f"candidate file not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise TkpiExtractionError(f"candidate file at {path} is not valid JSON") from exc

    candidates = []
    for raw in payload.get("candidates", []):
        candidates.append(
            TkpiCandidate(
                source_food_code=raw["source_food_code"],
                source_food_name=raw["source_food_name"],
                calories_per_100g=raw.get("calories_per_100g"),
                protein_per_100g=raw.get("protein_per_100g"),
                fat_per_100g=raw.get("fat_per_100g"),
                carbs_per_100g=raw.get("carbs_per_100g"),
                bdd_percent=raw.get("bdd_percent"),
                source=raw.get("source", ""),
                source_version=raw.get("source_version", ""),
                source_page=raw.get("source_page", ""),
                source_reference=raw.get("source_reference", ""),
                extraction_method=raw.get("extraction_method", MANUAL_VISUAL_TRANSCRIPTION),
                extraction_status=raw.get("extraction_status", NEEDS_REVIEW),
                verification_status=raw.get("verification_status", UNVERIFIED),
                extraction_notes=raw.get("extraction_notes", ""),
                source_internal_row=raw.get("source_internal_row", ""),
            )
        )
    return candidates


def save_candidates(path: Path, candidates: list[TkpiCandidate], metadata: dict) -> None:
    payload = {
        "_comment": (
            "TKPI 2020 extraction candidates (Phase 3E pilot). PARTIAL coverage only - "
            "this is NOT the complete TKPI 2020 dataset. A row here is a CANDIDATE: "
            "'parsed' describes structural shape, not correctness, and no row counts "
            "as production data until it is independently verified AND explicitly "
            "promoted via scripts/tkpi_promotion.py. Never read by the bot at runtime."
        ),
        "metadata": metadata,
        "candidates": [c.to_dict() for c in candidates],
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
