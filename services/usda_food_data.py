"""USDA FoodData Central lookup.

Used only as a fallback for foods with no VERIFIED local record. Nutrition
values are always fetched live from the API - nothing here is hard-coded or
recalled, and no model is asked to supply nutrient values.

This module knows nothing about Telegram.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

import config

logger = logging.getLogger(__name__)

# Generic, non-branded datasets only. Branded records carry per-serving quirks
# and inconsistent descriptions, so we never fall back to them.
ALLOWED_DATA_TYPES = ("Foundation", "SR Legacy")

# FDC nutrient numbers are stable identifiers; names vary between datasets.
ENERGY_KCAL_NUMBER = "208"
PROTEIN_NUMBER = "203"
CARB_NUMBER = "205"
FAT_NUMBER = "204"


@dataclass(frozen=True)
class UsdaQuery:
    """A curated Indonesian -> USDA mapping.

    Token rules keep the fallback honest: a candidate must look like the food
    we asked for, and must not look like something we did not ask for.
    """

    query: str
    require_all: tuple[str, ...] = ()
    reject_any: tuple[str, ...] = ()


# Explicit mapping for the foods we actually need. Deliberately small - we do
# not translate arbitrary food names, and we never ask a model to choose.
FOOD_QUERIES: dict[str, UsdaQuery] = {
    "ayam goreng": UsdaQuery(
        query="chicken broilers or fryers meat and skin fried",
        require_all=("chicken", "fried"),
        reject_any=(
            "canned",
            "baby food",
            "soup",
            "gravy",
            "liver",
            "giblets",
            "skin only",
            "neck",
            "back",
        ),
    ),
    "timun": UsdaQuery(
        # Timun is eaten as lalapan with the skin on, so reject the peeled record.
        query="cucumber with peel raw",
        require_all=("cucumber", "with peel", "raw"),
        reject_any=("peeled", "pickle", "pickled", "sour", "dill", "sweet", "relish"),
    ),
    "kol": UsdaQuery(
        query="cabbage raw",
        require_all=("cabbage", "raw"),
        reject_any=(
            "chinese",
            "napa",
            "savoy",
            "red",
            "kimchi",
            "salad",
            "swamp",
            "mustard",
            "skunk",
        ),
    ),
    "kemangi": UsdaQuery(
        query="basil fresh",
        require_all=("basil",),
        reject_any=("dried", "ground", "seed", "sauce", "pesto"),
    ),
}


class UsdaError(RuntimeError):
    """Raised when a USDA lookup cannot produce a usable result."""


@dataclass(frozen=True)
class UsdaFood:
    """A USDA record with enough provenance to debug it later."""

    fdc_id: int
    description: str
    data_type: str
    calories_per_100g: float
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    query: str = ""
    retrieved_at: str = ""
    source: str = "USDA FoodData Central"


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def is_acceptable(description: str, data_type: str, mapping: UsdaQuery) -> bool:
    """Decide whether a candidate really is the food we asked for."""
    if data_type not in ALLOWED_DATA_TYPES:
        return False

    text = _normalize(description)
    if any(token in text for token in mapping.reject_any):
        return False
    return all(token in text for token in mapping.require_all)


def _nutrient_amounts(payload: dict) -> dict[str, float]:
    """Pull nutrient numbers out of either API response shape."""
    amounts: dict[str, float] = {}

    for entry in payload.get("foodNutrients", []) or []:
        if not isinstance(entry, dict):
            continue

        # /food/{id} nests the definition; /foods/search flattens it.
        nutrient = entry.get("nutrient")
        if isinstance(nutrient, dict):
            number = nutrient.get("number")
            unit = nutrient.get("unitName")
            value = entry.get("amount")
        else:
            number = entry.get("nutrientNumber")
            unit = entry.get("unitName")
            value = entry.get("value")

        if number is None or value is None:
            continue

        key = str(number).lstrip("0") or "0"

        # Energy is reported in both kcal and kJ; keep only kcal.
        if key == ENERGY_KCAL_NUMBER.lstrip("0"):
            if str(unit or "").strip().lower() not in ("kcal", ""):
                continue

        try:
            amounts.setdefault(key, float(value))
        except (TypeError, ValueError):
            continue

    return amounts


def _pick(amounts: dict[str, float], number: str) -> float | None:
    return amounts.get(number.lstrip("0") or "0")


def to_usda_food(payload: dict, query: str = "") -> UsdaFood:
    """Convert an API payload into our internal structure.

    Raises UsdaError when a required nutrient is absent - a missing nutrient
    is never filled in with a guess.
    """
    amounts = _nutrient_amounts(payload)

    values = {
        "calories_per_100g": _pick(amounts, ENERGY_KCAL_NUMBER),
        "protein_per_100g": _pick(amounts, PROTEIN_NUMBER),
        "carbs_per_100g": _pick(amounts, CARB_NUMBER),
        "fat_per_100g": _pick(amounts, FAT_NUMBER),
    }

    missing = sorted(name for name, value in values.items() if value is None)
    if missing:
        raise UsdaError(
            f"FDC {payload.get('fdcId')} is missing nutrients: {', '.join(missing)}"
        )

    return UsdaFood(
        fdc_id=int(payload.get("fdcId", 0)),
        description=str(payload.get("description", "")),
        data_type=str(payload.get("dataType", "")),
        query=query,
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **values,
    )


class UsdaClient:
    """Searches FoodData Central and caches results for the process lifetime."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key if api_key is not None else config.USDA_API_KEY
        self.base_url = (base_url or config.USDA_BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else config.USDA_TIMEOUT_SECONDS
        self._client = client
        # Keyed by the normalized Indonesian food name.
        self._cache: dict[str, UsdaFood | None] = {}
        self.request_count = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _redact(self, value: object) -> str:
        """httpx puts the full request URL - api_key included - in its errors."""
        text = str(value)
        if self.api_key:
            text = text.replace(self.api_key, "***")
        return re.sub(r"api_key=[^&\s'\"]+", "api_key=***", text)

    async def _get(self, path: str, params: dict) -> dict:
        params = {**params, "api_key": self.api_key}
        self.request_count += 1
        url = f"{self.base_url}{path}"

        if self._client is not None:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            return response.json()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def search(self, mapping: UsdaQuery) -> dict | None:
        """Return the first candidate that passes the safety rules."""
        payload = await self._get(
            "/foods/search",
            {
                "query": mapping.query,
                "pageSize": 10,
                "dataType": ",".join(ALLOWED_DATA_TYPES),
            },
        )

        for candidate in payload.get("foods", []) or []:
            description = str(candidate.get("description", ""))
            data_type = str(candidate.get("dataType", ""))
            if is_acceptable(description, data_type, mapping):
                return candidate
            logger.debug("Rejected USDA candidate %r (%s)", description, data_type)

        return None

    async def lookup(self, food_name: str) -> UsdaFood | None:
        """Look up one Indonesian food name. Returns None when unavailable.

        Never raises: any failure means "no data for this food", so one bad
        lookup cannot break the rest of the meal.
        """
        key = _normalize(food_name)

        if key in self._cache:
            logger.debug("USDA cache hit for %r", food_name)
            return self._cache[key]

        mapping = FOOD_QUERIES.get(key)
        if mapping is None:
            logger.info("No curated USDA query for %r", food_name)
            self._cache[key] = None
            return None

        if not self.enabled:
            logger.info("USDA_API_KEY not set; skipping fallback for %r", food_name)
            return None

        try:
            candidate = await self.search(mapping)
            if candidate is None:
                logger.info("No safe USDA match for %r", food_name)
                self._cache[key] = None
                return None

            # Re-fetch the full record: search results can omit nutrients.
            detail = await self._get(f"/food/{candidate['fdcId']}", {"format": "full"})
            food = to_usda_food(detail, query=mapping.query)
        except Exception as exc:
            # Timeouts, 429s, bad JSON, missing nutrients - all mean the same
            # thing to the caller. Not cached, so a transient failure can retry.
            logger.warning("USDA lookup failed for %r: %s", food_name, self._redact(exc))
            return None

        logger.info(
            "USDA matched %r -> FDC %s (%s, %s)",
            food_name,
            food.fdc_id,
            food.description,
            food.data_type,
        )
        self._cache[key] = food
        return food


_default_client: UsdaClient | None = None


def get_client() -> UsdaClient:
    """One client - and therefore one cache - per bot process."""
    global _default_client
    if _default_client is None:
        _default_client = UsdaClient()
    return _default_client
