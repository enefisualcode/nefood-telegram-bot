"""Phase 3E: Indonesian compound-dish foundation tests.

Covers points 11-15 of the Phase 3E test plan: compound dishes carry no
fixed nutrition, pecel lele is compound, bare martabak is ambiguous,
martabak telur/manis are distinct, and nasi padang is variable.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.dish_matcher import (
    AMBIGUOUS,
    FIXED_COMPONENTS,
    VARIABLE,
    DishMatcher,
    DishTemplateError,
    _assert_no_nutrition_fields,
    get_matcher,
    load_templates,
)


class NoFixedNutritionTest(unittest.TestCase):
    """11. compound dishes contain no fixed nutrition."""

    def test_shipped_templates_load_without_raising(self):
        templates = load_templates()
        self.assertGreater(len(templates), 0)

    def test_loader_rejects_a_smuggled_nutrition_field(self):
        bad_payload = {
            "dishes": [
                {
                    "id": "x", "name": "X", "aliases": [], "variability": "fixed_components",
                    "components": [{"role": "protein", "typical_food": "y", "calories_per_100g": 200}],
                }
            ]
        }
        with self.assertRaises(DishTemplateError):
            _assert_no_nutrition_fields(bad_payload["dishes"])

    def test_loader_rejects_a_top_level_nutrition_field(self):
        bad_payload = {"dishes": [{"id": "x", "name": "X", "kcal": 100}]}
        with self.assertRaises(DishTemplateError):
            _assert_no_nutrition_fields(bad_payload["dishes"])

    def test_no_shipped_dish_or_component_has_a_gram_amount(self):
        import json
        from services.dish_matcher import DATA_FILE
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        # Will raise if any forbidden key is present anywhere in the file.
        _assert_no_nutrition_fields(payload.get("dishes", []))

    def test_dish_template_objects_expose_no_nutrition_attributes(self):
        matcher = get_matcher()
        for template in matcher.templates:
            self.assertFalse(hasattr(template, "calories_per_100g"))
            self.assertFalse(hasattr(template, "protein_per_100g"))
            for component in template.components:
                self.assertFalse(hasattr(component, "grams"))
                self.assertFalse(hasattr(component, "calories_per_100g"))


class PecelLeleCompoundTest(unittest.TestCase):
    """12. pecel lele is compound."""

    def setUp(self):
        self.matcher = get_matcher()

    def test_pecel_lele_matches_and_is_compound(self):
        template = self.matcher.match("pecel lele")
        self.assertIsNotNone(template)
        self.assertTrue(template.is_compound)

    def test_pecel_lele_lists_its_expected_components(self):
        template = self.matcher.match("Pecel Lele")
        foods = {c.typical_food for c in template.components}
        for expected in ("lele goreng", "sambal", "kol", "timun", "kemangi"):
            self.assertIn(expected, foods)

    def test_pecel_lele_is_not_variable_or_ambiguous(self):
        template = self.matcher.match("pecel lele")
        self.assertEqual(template.variability, FIXED_COMPONENTS)


class BareMartabakAmbiguousTest(unittest.TestCase):
    """13. bare martabak ambiguous."""

    def setUp(self):
        self.matcher = get_matcher()

    def test_bare_martabak_is_ambiguous(self):
        template = self.matcher.match("martabak")
        self.assertIsNotNone(template)
        self.assertEqual(template.variability, AMBIGUOUS)
        self.assertTrue(template.is_ambiguous)

    def test_bare_martabak_lists_both_possible_resolutions(self):
        template = self.matcher.match("martabak")
        self.assertIn("martabak_telur", template.possible_resolutions)
        self.assertIn("martabak_manis", template.possible_resolutions)

    def test_bare_martabak_has_no_components_of_its_own(self):
        template = self.matcher.match("martabak")
        self.assertEqual(template.components, [])


class MartabakTelurManisDistinctTest(unittest.TestCase):
    """14. martabak telur/manis distinct."""

    def setUp(self):
        self.matcher = get_matcher()

    def test_martabak_telur_and_manis_are_different_templates(self):
        telur = self.matcher.match("martabak telur")
        manis = self.matcher.match("martabak manis")
        self.assertIsNotNone(telur)
        self.assertIsNotNone(manis)
        self.assertNotEqual(telur.id, manis.id)

    def test_martabak_telur_and_manis_have_disjoint_core_components(self):
        telur = self.matcher.match("martabak telur")
        manis = self.matcher.match("martabak manis")
        telur_foods = {c.typical_food for c in telur.components}
        manis_foods = {c.typical_food for c in manis.components}
        self.assertIn("telur", telur_foods)
        self.assertNotIn("telur", manis_foods)

    def test_alias_terang_bulan_resolves_to_martabak_manis(self):
        template = self.matcher.match("terang bulan")
        self.assertEqual(template.id, "martabak_manis")

    def test_bare_martabak_does_not_alias_to_either_specific_variant(self):
        bare = self.matcher.match("martabak")
        telur = self.matcher.match("martabak telur")
        self.assertNotEqual(bare.id, telur.id)


class NasiPadangVariableTest(unittest.TestCase):
    """15. nasi padang variable."""

    def setUp(self):
        self.matcher = get_matcher()

    def test_nasi_padang_is_marked_variable(self):
        template = self.matcher.match("nasi padang")
        self.assertIsNotNone(template)
        self.assertEqual(template.variability, VARIABLE)
        self.assertTrue(template.is_variable)

    def test_nasi_padang_lauk_component_is_marked_optional_and_example_only(self):
        template = self.matcher.match("nasi padang")
        lauk = [c for c in template.components if c.role == "lauk"]
        self.assertEqual(len(lauk), 1)
        self.assertTrue(lauk[0].optional)


class DishMatcherBasicsTest(unittest.TestCase):
    def setUp(self):
        self.matcher = get_matcher()

    def test_unknown_name_does_not_match(self):
        self.assertIsNone(self.matcher.match("rendang daging sapi khas minang"))

    def test_is_compound_true_for_known_dish(self):
        self.assertTrue(self.matcher.is_compound("bakso"))

    def test_is_compound_false_for_unknown_name(self):
        self.assertFalse(self.matcher.is_compound("sate ayam madura"))

    def test_case_and_space_insensitive_matching(self):
        self.assertIsNotNone(self.matcher.match("  MIE AYAM  "))

    def test_every_dish_id_is_unique(self):
        ids = [t.id for t in self.matcher.templates]
        self.assertEqual(len(ids), len(set(ids)))

    def test_gado_gado_and_ketoprak_both_present_and_distinct(self):
        gado = self.matcher.match("gado-gado")
        ketoprak = self.matcher.match("ketoprak")
        self.assertIsNotNone(gado)
        self.assertIsNotNone(ketoprak)
        self.assertNotEqual(gado.id, ketoprak.id)

    def test_all_expected_dishes_are_present(self):
        expected = {
            "pecel_lele", "nasi_uduk", "martabak_telur", "martabak_manis",
            "nasi_goreng", "mie_ayam", "bakso", "soto_ayam", "gado_gado",
            "ketoprak", "nasi_padang", "martabak",
        }
        ids = {t.id for t in self.matcher.templates}
        self.assertEqual(expected, ids)


if __name__ == "__main__":
    unittest.main()
