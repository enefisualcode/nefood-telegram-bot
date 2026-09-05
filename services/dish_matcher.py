"""Match a detected name against Indonesian compound-dish decomposition hints.

Phase 3E foundation only. This module knows how to say "pecel lele is made
of lele goreng + sambal + kol + timun + kemangi (+ optionally rice)" - it
does NOT estimate grams or nutrition for any of that. A future image-based
estimator is expected to detect and match each component independently
(against services/food_matcher.py / USDA, exactly like any other food).

Not wired into bot.py or food_vision.py in this phase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from services.food_matcher import normalize

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "dish_templates.json"

FIXED_COMPONENTS = "fixed_components"
AMBIGUOUS = "ambiguous"
VARIABLE = "variable"
VARIABILITIES = (FIXED_COMPONENTS, AMBIGUOUS, VARIABLE)

# A dish template is a decomposition HINT, never a nutrition source. Any of
# these keys appearing anywhere in a dish's JSON is a hard error at load
# time, not just a lint warning - dish data must never carry fixed totals.
_FORBIDDEN_NUTRITION_KEYS = {
    "calories", "calories_per_100g", "kcal",
    "protein", "protein_per_100g",
    "fat", "fat_per_100g",
    "carbs", "carbs_per_100g", "carbohydrate", "carbohydrates",
    "grams", "portion_grams", "weight_grams",
}


class DishTemplateError(RuntimeError):
    """Raised when dish_templates.json cannot be loaded or is unsafe."""


def _assert_no_nutrition_fields(node, path: str = "dishes") -> None:
    """Recursively refuse any dish JSON that smuggles in a nutrition field."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).strip().lower() in _FORBIDDEN_NUTRITION_KEYS:
                raise DishTemplateError(
                    f"dish template at {path!r} contains a forbidden nutrition field {key!r} - "
                    "dish templates must never carry fixed nutrition totals"
                )
            _assert_no_nutrition_fields(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _assert_no_nutrition_fields(item, f"{path}[{index}]")


@dataclass(frozen=True)
class DishComponent:
    role: str
    typical_food: str
    optional: bool = False
    notes: str = ""


@dataclass(frozen=True)
class DishTemplate:
    id: str
    name: str
    aliases: list[str]
    variability: str
    components: list[DishComponent] = field(default_factory=list)
    possible_resolutions: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return self.variability == AMBIGUOUS

    @property
    def is_variable(self) -> bool:
        return self.variability == VARIABLE

    @property
    def is_compound(self) -> bool:
        """True for anything decomposable - i.e. not a single plain food."""
        return True


def _load_raw(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DishTemplateError(f"dish template file not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise DishTemplateError(f"dish template file at {path} is not valid JSON") from exc


def _build_template(raw: dict) -> DishTemplate:
    variability = str(raw.get("variability", "")).strip().lower()
    if variability not in VARIABILITIES:
        raise DishTemplateError(
            f"dish {raw.get('id')!r} has unknown variability {raw.get('variability')!r}"
        )

    components = [
        DishComponent(
            role=str(c.get("role", "")),
            typical_food=str(c.get("typical_food", "")),
            optional=bool(c.get("optional", False)),
            notes=str(c.get("notes", "")),
        )
        for c in raw.get("components", [])
    ]

    return DishTemplate(
        id=raw["id"],
        name=raw["name"],
        aliases=list(raw.get("aliases", [])),
        variability=variability,
        components=components,
        possible_resolutions=list(raw.get("possible_resolutions", [])),
        notes=str(raw.get("notes", "")),
    )


def load_templates(path: Path = DATA_FILE) -> list[DishTemplate]:
    payload = _load_raw(path)
    _assert_no_nutrition_fields(payload.get("dishes", []))
    return [_build_template(raw) for raw in payload.get("dishes", [])]


class DishMatcher:
    """Looks up dish templates by name or alias, like FoodMatcher but for compound dishes."""

    def __init__(self, templates: list[DishTemplate]):
        self.templates = templates
        self._by_key: dict[str, DishTemplate] = {}
        for template in templates:
            for name in [template.name, *template.aliases]:
                key = normalize(name)
                if key:
                    self._by_key.setdefault(key, template)

    @classmethod
    def from_file(cls, path: Path = DATA_FILE) -> "DishMatcher":
        return cls(load_templates(path))

    def match(self, name: str) -> DishTemplate | None:
        key = normalize(name)
        if not key:
            return None
        return self._by_key.get(key)

    def is_compound(self, name: str) -> bool:
        return self.match(name) is not None


_default_matcher: DishMatcher | None = None


def get_matcher() -> DishMatcher:
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = DishMatcher.from_file()
    return _default_matcher
