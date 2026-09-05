"""Phase 3F: connect vision output to dish templates, without inventing food.

This module answers exactly one question: "given what Gemini says it saw,
what dish label (if any) should the user see, and is that dish ambiguous?"
It never changes *which* foods go into the nutrition calculation - `foods`
on the returned DecomposedMeal is always exactly `analysis.foods`, untouched.
A dish template's components are read-only hints, used only to (a) produce a
friendlier label and (b) authoritatively classify a dish name as compound,
variable, or ambiguous - they are never used to add a component the model
did not report seeing.

Classification is deliberately NOT taken from `analysis.dish_type` (the
model's own self-report) - it is re-derived by looking `analysis.dish_name`
up in the DishMatcher, which is the single source of truth for which dishes
are ambiguous (bare "martabak") or variable (nasi padang). This means a
model mistake in dish_type can't misrepresent an ambiguous dish as resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.dish_matcher import DishMatcher, DishTemplate
from services.food_vision import (
    DISH_TYPE_AMBIGUOUS,
    DISH_TYPE_COMPOUND,
    DISH_TYPE_SIMPLE,
    DISH_TYPE_VARIABLE,
    DetectedFood,
    FoodAnalysis,
)


@dataclass(frozen=True)
class DecomposedMeal:
    """What to show the user, and how the detected foods were classified.

    `foods` is never anything other than a copy of the input analysis's
    food list - no component is ever added, removed, or resized here.
    """

    dish_name: str
    dish_type: str
    foods: list[DetectedFood] = field(default_factory=list)
    template: DishTemplate | None = None

    @property
    def is_simple(self) -> bool:
        return self.dish_type == DISH_TYPE_SIMPLE

    @property
    def needs_clarification(self) -> bool:
        """True when the dish name itself is ambiguous (e.g. bare 'martabak')."""
        return self.dish_type == DISH_TYPE_AMBIGUOUS


def decompose(analysis: FoodAnalysis, matcher: DishMatcher) -> DecomposedMeal:
    """Classify `analysis` against known dish templates.

    Falls back to a plain "simple" result - preserving the pre-Phase-3F
    behaviour - whenever there is no dish name, the name doesn't match any
    known template, or anything about the match looks unexpected. Unknown
    dishes must never break this call.
    """
    foods = list(analysis.foods)
    dish_name = (analysis.dish_name or "").strip()

    if not dish_name:
        return DecomposedMeal(dish_name="", dish_type=DISH_TYPE_SIMPLE, foods=foods)

    try:
        template = matcher.match(dish_name)
    except Exception:
        # A lookup failure must never take down decomposition - it just
        # means we can't add a dish label, so fall back to the plain list.
        return DecomposedMeal(dish_name="", dish_type=DISH_TYPE_SIMPLE, foods=foods)

    if template is None:
        # Not a dish we have a template for - Phase 2.5 behaviour: just the
        # plain detected foods, no compound-dish framing.
        return DecomposedMeal(dish_name="", dish_type=DISH_TYPE_SIMPLE, foods=foods)

    if template.is_ambiguous:
        dish_type = DISH_TYPE_AMBIGUOUS
    elif template.is_variable:
        dish_type = DISH_TYPE_VARIABLE
    else:
        dish_type = DISH_TYPE_COMPOUND

    return DecomposedMeal(dish_name=template.name, dish_type=dish_type, foods=foods, template=template)
