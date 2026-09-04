"""Match a detected food name against the local curated food database.

Nutrition values never come from the vision model - they come from
data/foods.json, which cites a source for every record.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "foods.json"

# Only accept a fuzzy hit when the names are very close, so that unrelated
# foods (e.g. "tahu" vs "tahi") are never silently matched.
FUZZY_CUTOFF = 0.87


class FoodDatabaseError(RuntimeError):
    """Raised when the food database cannot be loaded."""


VERIFIED = "verified"
PROVISIONAL = "provisional"
NOT_APPLICABLE = "not_applicable"

# How estimated_grams should be read for a food.
GROSS = "gross"  # whole visible weight; may need an edible-portion correction
EDIBLE = "edible"  # already the edible mass


def valid_factor(value) -> float | None:
    """An edible portion factor must be a number in (0, 1]."""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 < factor <= 1:
        return None
    return factor


@dataclass(frozen=True)
class FoodRecord:
    id: str
    name: str
    aliases: list[str]
    calories_per_100g: float
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    source: str = ""
    source_reference: str = ""
    # Anything not explicitly verified is untrusted, so this defaults to
    # PROVISIONAL rather than VERIFIED.
    data_status: str = PROVISIONAL

    # Fine-grained provenance for the *nutrition* source (Phase 3D). Optional
    # and additive - existing records with only source/source_reference keep
    # working unchanged.
    source_food_code: str = ""       # e.g. TKPI code "DR039"
    source_food_name: str = ""       # official name as published by the source
    source_version: str = ""         # e.g. "TKPI 2019"

    # Edible portion (BDD - Bagian yang Dapat Dimakan). Tracked separately
    # from the nutrition values: a record can have verified nutrition and an
    # unverified BDD factor, or the reverse.
    edible_portion_factor: float | None = None
    edible_portion_status: str = NOT_APPLICABLE
    weight_basis: str = EDIBLE
    edible_portion_source: str = ""
    edible_portion_source_reference: str = ""

    @property
    def is_verified(self) -> bool:
        """Only verified records may be used in user-facing nutrition values."""
        return self.data_status == VERIFIED

    @property
    def has_verified_edible_portion(self) -> bool:
        """Only a verified factor on a gross-weight food may adjust grams."""
        return (
            self.weight_basis == GROSS
            and self.edible_portion_status == VERIFIED
            and valid_factor(self.edible_portion_factor) is not None
        )


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, and collapse whitespace."""
    text = name.strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _edible_portion_fields(raw: dict) -> dict:
    """Read the BDD fields, discarding anything that fails validation.

    An out-of-range factor is dropped rather than clamped - a bad factor is a
    data error, and silently "fixing" it would fabricate a correction.
    """
    factor = valid_factor(raw.get("edible_portion_factor"))
    status = str(raw.get("edible_portion_status", NOT_APPLICABLE)).strip().lower()
    basis = str(raw.get("weight_basis", EDIBLE)).strip().lower()

    if raw.get("edible_portion_factor") is not None and factor is None:
        logger.warning(
            "Ignoring invalid edible_portion_factor %r for %r",
            raw.get("edible_portion_factor"),
            raw.get("id"),
        )
        status = NOT_APPLICABLE

    if status not in (VERIFIED, PROVISIONAL, NOT_APPLICABLE):
        logger.warning(
            "Unknown edible_portion_status %r for %r; treating as provisional",
            status,
            raw.get("id"),
        )
        status = PROVISIONAL

    if basis not in (GROSS, EDIBLE):
        logger.warning(
            "Unknown weight_basis %r for %r; treating as edible", basis, raw.get("id")
        )
        basis = EDIBLE

    return {
        "edible_portion_factor": factor,
        "edible_portion_status": status,
        "weight_basis": basis,
        "edible_portion_source": str(raw.get("edible_portion_source", "")),
        "edible_portion_source_reference": str(
            raw.get("edible_portion_source_reference", "")
        ),
    }


def _load_records(path: Path) -> list[FoodRecord]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FoodDatabaseError(f"food database not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise FoodDatabaseError(f"food database at {path} is not valid JSON") from exc

    records = []
    for raw in payload.get("foods", []):
        try:
            records.append(
                FoodRecord(
                    id=raw["id"],
                    name=raw["name"],
                    aliases=list(raw.get("aliases", [])),
                    calories_per_100g=float(raw["calories_per_100g"]),
                    protein_per_100g=float(raw["protein_per_100g"]),
                    carbs_per_100g=float(raw["carbs_per_100g"]),
                    fat_per_100g=float(raw["fat_per_100g"]),
                    source=raw.get("source", ""),
                    source_reference=raw.get("source_reference", ""),
                    source_food_code=str(raw.get("source_food_code", "")),
                    source_food_name=str(raw.get("source_food_name", "")),
                    source_version=str(raw.get("source_version", "")),
                    data_status=str(raw.get("data_status", PROVISIONAL)).strip().lower(),
                    **_edible_portion_fields(raw),
                )
            )
        except (KeyError, TypeError, ValueError):
            # A malformed record is skipped rather than guessed at.
            logger.warning("Skipping malformed food record: %r", raw.get("id", raw))

    return records


class FoodMatcher:
    """Looks up food records by name or alias."""

    def __init__(self, records: list[FoodRecord]):
        self.records = records
        self._by_key: dict[str, FoodRecord] = {}
        for record in records:
            for name in [record.name, *record.aliases]:
                key = normalize(name)
                if key:
                    self._by_key.setdefault(key, record)

    @classmethod
    def from_file(cls, path: Path = DATA_FILE) -> "FoodMatcher":
        return cls(_load_records(path))

    def match(self, name: str) -> FoodRecord | None:
        """Return the matching record, or None when the food is unknown."""
        key = normalize(name)
        if not key:
            return None

        record = self._by_key.get(key)
        if record is not None:
            return record

        close = difflib.get_close_matches(key, self._by_key.keys(), n=1, cutoff=FUZZY_CUTOFF)
        if close:
            logger.info("Fuzzy matched %r to %r", name, close[0])
            return self._by_key[close[0]]

        logger.info("No nutrition record for %r", name)
        return None


_default_matcher: FoodMatcher | None = None


def get_matcher() -> FoodMatcher:
    """Load the database once and reuse it."""
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = FoodMatcher.from_file()
    return _default_matcher
