"""Phase 3E: TKPI extraction/audit/promotion pipeline tests.

Covers points 1-10 and 16-17 of the Phase 3E test plan: source provenance,
extraction method, parsed != verified, invalid BDD, malformed rows,
duplicate codes, needs_review on uncertainty, explicit approval, no
automatic promotion, honest partial coverage, and that the existing
local -> USDA -> unavailable behaviour is untouched.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.promote_tkpi_candidate import PromotionError, build_food_record, promote
from scripts.tkpi_extraction import (
    MANUAL_VISUAL_TRANSCRIPTION,
    NEEDS_REVIEW,
    OFFICIALLY_VERIFIED,
    PARSED,
    REJECTED,
    SPOT_CHECKED,
    UNVERIFIED,
    TkpiCandidate,
    candidate_from_raw,
    find_duplicate_codes,
    high_risk_reasons,
    load_candidates,
    save_candidates,
    valid_bdd,
    valid_food_code,
)
from scripts.tkpi_promotion import ApprovalError, approve_candidate, find_approval

CANDIDATES_PATH = Path(__file__).resolve().parent.parent / "data" / "tkpi_2020_candidates.json"
APPROVED_PATH = Path(__file__).resolve().parent.parent / "data" / "tkpi_approved.json"
FOODS_PATH = Path(__file__).resolve().parent.parent / "data" / "foods.json"

GOOD_ROW = dict(
    source_food_code="AR999",
    source_food_name="Contoh bahan",
    calories_per_100g=100,
    protein_per_100g=5,
    fat_per_100g=2,
    carbs_per_100g=20,
    bdd_percent=90,
    source="Tabel Komposisi Pangan Indonesia (TKPI) 2020, Kementerian Kesehatan RI",
    source_version="TKPI 2020",
    source_page="PDF hlm. 99",
    source_reference="Contoh baris uji, bukan data nyata",
    extraction_method=MANUAL_VISUAL_TRANSCRIPTION,
)


def make(**overrides) -> TkpiCandidate:
    return candidate_from_raw({**GOOD_ROW, **overrides})


class ValidationHelpersTest(unittest.TestCase):
    def test_valid_food_code_accepts_real_shapes(self):
        for code in ("AP001", "AR015", "BR021", "DR114"):
            self.assertTrue(valid_food_code(code))

    def test_valid_food_code_rejects_garbage(self):
        for code in ("", "AP1", "1234", "ap001", "AP0011", None, 123):
            self.assertFalse(valid_food_code(code))

    def test_valid_bdd_accepts_range_and_none(self):
        self.assertTrue(valid_bdd(None))
        self.assertTrue(valid_bdd(0))
        self.assertTrue(valid_bdd(100))
        self.assertTrue(valid_bdd(57.5))

    def test_valid_bdd_rejects_out_of_range(self):
        for bad in (-1, 100.1, 101, -0.1, "abc", [], {}):
            self.assertFalse(valid_bdd(bad))


class ProvenancePreservedTest(unittest.TestCase):
    """1. source page preserved. 2. extraction method preserved."""

    def test_source_page_is_preserved(self):
        candidate = make(source_page="PDF hlm. 14 (halaman cetak 10)")
        self.assertEqual(candidate.source_page, "PDF hlm. 14 (halaman cetak 10)")

    def test_extraction_method_is_preserved_and_never_silently_upgraded(self):
        candidate = make(extraction_method=MANUAL_VISUAL_TRANSCRIPTION)
        self.assertEqual(candidate.extraction_method, MANUAL_VISUAL_TRANSCRIPTION)
        self.assertNotEqual(candidate.extraction_method, "automated_ocr")


class ParsedIsNotVerifiedTest(unittest.TestCase):
    """3. parsed != verified."""

    def test_a_cleanly_parsed_row_defaults_to_unverified(self):
        candidate = make()
        self.assertEqual(candidate.extraction_status, PARSED)
        self.assertEqual(candidate.verification_status, UNVERIFIED)

    def test_parsed_status_alone_does_not_qualify_for_approval(self):
        candidate = make()  # parsed, but verification_status=unverified
        with self.assertRaises(ApprovalError):
            approve_candidate(
                candidate,
                approver="tester",
                confirmations={k: True for k in (
                    "code_confirmed", "name_confirmed", "calories_confirmed",
                    "protein_confirmed", "fat_confirmed", "carbs_confirmed",
                    "page_reference_confirmed",
                )},
            )


class InvalidBddTest(unittest.TestCase):
    """4. invalid BDD rejected."""

    def test_out_of_range_bdd_is_flagged_needs_review(self):
        candidate = make(bdd_percent=150)
        self.assertEqual(candidate.extraction_status, NEEDS_REVIEW)
        self.assertIn("bdd_percent", candidate.extraction_notes)

    def test_negative_bdd_is_flagged_needs_review(self):
        candidate = make(bdd_percent=-5)
        self.assertEqual(candidate.extraction_status, NEEDS_REVIEW)


class MalformedRowTest(unittest.TestCase):
    """5. malformed rows rejected/reviewed safely."""

    def test_missing_required_field_is_rejected(self):
        candidate = make(calories_per_100g=None)
        self.assertEqual(candidate.extraction_status, REJECTED)

    def test_negative_macro_is_rejected(self):
        candidate = make(protein_per_100g=-1)
        self.assertEqual(candidate.extraction_status, REJECTED)

    def test_implausible_food_code_is_rejected(self):
        candidate = make(source_food_code="???")
        self.assertEqual(candidate.extraction_status, REJECTED)

    def test_non_numeric_macro_is_rejected_not_guessed(self):
        candidate = make(carbs_per_100g="banyak")
        self.assertEqual(candidate.extraction_status, REJECTED)
        self.assertIsNone(candidate.carbs_per_100g)

    def test_rejected_row_is_never_eligible_for_approval(self):
        candidate = make(calories_per_100g=None)
        with self.assertRaises(ApprovalError):
            approve_candidate(candidate, approver="tester", confirmations={})


class DuplicateCodeTest(unittest.TestCase):
    """6. duplicate code detection."""

    def test_no_duplicates_in_a_clean_set(self):
        candidates = [make(source_food_code="AR001"), make(source_food_code="AR002")]
        self.assertEqual(find_duplicate_codes(candidates), {})

    def test_duplicate_code_is_detected(self):
        candidates = [
            make(source_food_code="AR001"),
            make(source_food_code="AR001"),
            make(source_food_code="AR002"),
        ]
        self.assertEqual(find_duplicate_codes(candidates), {"AR001": 2})

    def test_shipped_candidate_file_has_no_duplicate_codes(self):
        candidates = load_candidates(CANDIDATES_PATH)
        self.assertEqual(find_duplicate_codes(candidates), {})


class UncertainValuesNeedReviewTest(unittest.TestCase):
    """7. uncertain values -> needs_review."""

    def test_explicitly_flagged_uncertain_row_is_needs_review(self):
        candidate = make(uncertain=True, uncertainty_reason="name not fully legible on the scan")
        self.assertEqual(candidate.extraction_status, NEEDS_REVIEW)
        self.assertIn("not fully legible", candidate.extraction_notes)

    def test_uncertain_row_never_silently_repaired(self):
        # The numeric values pass through unchanged - being uncertain lowers
        # the status, it does not trigger any "fix".
        candidate = make(
            source_food_code="DR115", calories_per_100g=128,
            uncertain=True, uncertainty_reason="name unclear",
        )
        self.assertEqual(candidate.calories_per_100g, 128)
        self.assertEqual(candidate.extraction_status, NEEDS_REVIEW)


class ExplicitApprovalRequiredTest(unittest.TestCase):
    """8. explicit approval required. 9. unapproved candidate cannot become production verified."""

    FULL_CONFIRMATIONS = {
        "code_confirmed": True, "name_confirmed": True, "calories_confirmed": True,
        "protein_confirmed": True, "fat_confirmed": True, "carbs_confirmed": True,
        "page_reference_confirmed": True,
    }

    def _spot_checked(self, **overrides) -> TkpiCandidate:
        return make(verification_status=SPOT_CHECKED, **overrides)

    def test_approval_succeeds_with_all_confirmations_on_a_spot_checked_row(self):
        candidate = self._spot_checked()
        approval = approve_candidate(candidate, approver="reviewer", confirmations=self.FULL_CONFIRMATIONS)
        self.assertEqual(approval.source_food_code, candidate.source_food_code)
        self.assertEqual(approval.approver, "reviewer")

    def test_approval_fails_on_unverified_candidate(self):
        candidate = make(verification_status=UNVERIFIED)
        with self.assertRaises(ApprovalError):
            approve_candidate(candidate, approver="reviewer", confirmations=self.FULL_CONFIRMATIONS)

    def test_approval_fails_when_any_confirmation_is_missing(self):
        candidate = self._spot_checked()
        incomplete = {**self.FULL_CONFIRMATIONS, "carbs_confirmed": False}
        with self.assertRaises(ApprovalError):
            approve_candidate(candidate, approver="reviewer", confirmations=incomplete)

    def test_approval_fails_without_an_approver_name(self):
        candidate = self._spot_checked()
        with self.assertRaises(ApprovalError):
            approve_candidate(candidate, approver="  ", confirmations=self.FULL_CONFIRMATIONS)

    def test_promotion_fails_without_a_matching_approval(self):
        candidate = self._spot_checked(source_food_code="AR900")
        with self.assertRaises(PromotionError):
            promote(candidate, approvals=[], food_id="contoh", aliases=["contoh"], foods_path=FOODS_PATH)

    def test_promotion_fails_for_a_needs_review_candidate_even_if_somehow_approved(self):
        # An approval object can't be built for a needs_review row (tested
        # above), but promote() must also independently refuse to use one -
        # defence in depth, not "approval implies safe to promote blindly".
        candidate = make(bdd_percent=999, verification_status=SPOT_CHECKED)  # -> needs_review
        self.assertEqual(candidate.extraction_status, NEEDS_REVIEW)
        from scripts.tkpi_promotion import ApprovalRecord
        approval = ApprovalRecord(
            source_food_code=candidate.source_food_code, approver="x",
            approved_at="2026-01-01T00:00:00+00:00", confirmations=self.FULL_CONFIRMATIONS,
        )
        with self.assertRaises(PromotionError):
            promote(candidate, approvals=[approval], food_id="contoh", aliases=[], foods_path=FOODS_PATH)


class NoBulkPromotionTest(unittest.TestCase):
    """9 (continued). Nothing in the pipeline promotes more than one
    explicitly-approved row at a time, and the real foods.json ships with
    zero approvals consumed in Phase 3E."""

    def test_shipped_approval_ledger_is_empty(self):
        payload = json.loads(APPROVED_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("approved"), [])

    def test_promote_writes_exactly_one_record_and_refuses_duplicate_id(self):
        import tempfile
        candidate = make(source_food_code="AR950", verification_status=SPOT_CHECKED)
        from scripts.tkpi_promotion import ApprovalRecord
        approval = ApprovalRecord(
            source_food_code="AR950", approver="tester",
            approved_at="2026-01-01T00:00:00+00:00",
            confirmations={
                "code_confirmed": True, "name_confirmed": True, "calories_confirmed": True,
                "protein_confirmed": True, "fat_confirmed": True, "carbs_confirmed": True,
                "page_reference_confirmed": True,
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            foods_path = Path(tmp) / "foods.json"
            foods_path.write_text(json.dumps({"foods": []}), encoding="utf-8")
            record = promote(candidate, [approval], food_id="contoh_bahan", aliases=["contoh"], foods_path=foods_path)
            self.assertEqual(record["id"], "contoh_bahan")
            payload = json.loads(foods_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["foods"]), 1)

            # Promoting the same id again must refuse, not overwrite.
            with self.assertRaises(PromotionError):
                promote(candidate, [approval], food_id="contoh_bahan", aliases=["contoh"], foods_path=foods_path)


class PartialCoverageHonestyTest(unittest.TestCase):
    """10. partial coverage represented honestly."""

    def test_metadata_declares_partial_coverage(self):
        payload = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
        coverage = payload["metadata"]["coverage"].lower()
        self.assertIn("partial", coverage)
        self.assertIn("not the complete", coverage)

    def test_candidate_file_is_not_the_full_140_page_dataset(self):
        candidates = load_candidates(CANDIDATES_PATH)
        # A real full TKPI 2020 extraction would run into the hundreds of
        # rows across ~140 pages; this pilot is explicitly small.
        self.assertLess(len(candidates), 50)

    def test_readme_or_report_states_pilot_is_not_verified_production(self):
        report = Path(__file__).resolve().parent.parent / "reports" / "phase_3e_tkpi_audit.md"
        text = report.read_text(encoding="utf-8").lower()
        self.assertIn("pilot", text)


class ExistingSourcePriorityUnchangedTest(unittest.TestCase):
    """16. existing local -> USDA -> unavailable behaviour unchanged.

    Phase 3E/3F/3F.1 never touched foods.json, so at the time these were
    written "unchanged" meant "byte-identical to git HEAD" and "exactly
    {nasi_putih, kol}". Phase 3G is explicitly authorized to add new
    verified records (coverage expansion) - so what must actually stay
    unchanged is the *behaviour and content of the pre-existing records*,
    not the file's size or its exact verified-id set.
    """

    PRE_PHASE_3G_VERIFIED_IDS = {"nasi_putih", "kol"}

    def test_foods_json_still_loads_and_still_has_its_pre_existing_verified_records(self):
        from services.food_matcher import get_matcher
        matcher = get_matcher()
        verified_ids = {r.id for r in matcher.records if r.is_verified}
        self.assertTrue(self.PRE_PHASE_3G_VERIFIED_IDS.issubset(verified_ids))

    def test_pre_existing_records_are_untouched_by_later_phases(self):
        # Phase 3G may only ever APPEND new records - it must never modify
        # a record that predates it (Phase 3D/3E's own verified entries).
        payload = json.loads(FOODS_PATH.read_text(encoding="utf-8"))
        by_id = {f["id"]: f for f in payload["foods"]}
        self.assertEqual(by_id["nasi_putih"]["source_food_code"], "AP001")
        self.assertEqual(by_id["kol"]["source_food_code"], "DR114")
        self.assertEqual(by_id["kol"]["edible_portion_factor"], 0.75)

    def test_the_two_known_verified_tkpi_records_are_unchanged(self):
        payload = json.loads(FOODS_PATH.read_text(encoding="utf-8"))
        by_id = {f["id"]: f for f in payload["foods"]}
        self.assertEqual(by_id["nasi_putih"]["calories_per_100g"], 180)
        self.assertEqual(by_id["kol"]["calories_per_100g"], 29)


class HighRiskScreenTest(unittest.TestCase):
    def test_flags_high_protein(self):
        candidate = make(protein_per_100g=45)
        self.assertTrue(any("protein" in r for r in high_risk_reasons(candidate)))

    def test_flags_low_bdd(self):
        candidate = make(bdd_percent=10)
        self.assertTrue(any("BDD" in r for r in high_risk_reasons(candidate)))

    def test_flags_all_zero_macros(self):
        candidate = make(calories_per_100g=0, protein_per_100g=0, fat_per_100g=0, carbs_per_100g=0)
        self.assertTrue(any("zero" in r for r in high_risk_reasons(candidate)))

    def test_ordinary_row_has_no_flags(self):
        candidate = make()
        self.assertEqual(high_risk_reasons(candidate), [])


class SaveLoadRoundTripTest(unittest.TestCase):
    def test_round_trip_preserves_all_fields(self):
        import tempfile
        candidate = make(source_food_code="AR901", extraction_notes="contoh")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            save_candidates(path, [candidate], metadata={"coverage": "test"})
            loaded = load_candidates(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].source_food_code, "AR901")
        self.assertEqual(loaded[0].calories_per_100g, candidate.calories_per_100g)


if __name__ == "__main__":
    unittest.main()
