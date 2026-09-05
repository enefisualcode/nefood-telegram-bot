"""Apply one approved TKPI candidate to a foods.json-shaped database.

This is the only piece of Phase 3E that is allowed to write a
production-shaped food record - and even this refuses to do anything unless
an explicit ApprovalRecord already exists (see scripts/tkpi_promotion.py).
There is no default foods.json path here on purpose: every call must name
the file it is writing to, so this can never be run "by accident" against
production. Phase 3E itself never calls this against the real
data/foods.json - see reports/phase_3e_tkpi_audit.md and the phase report
for confirmation that data/foods.json was not modified.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.tkpi_extraction import OFFICIALLY_VERIFIED, PARSED, SPOT_CHECKED, TkpiCandidate
from scripts.tkpi_promotion import ApprovalRecord, find_approval


class PromotionError(RuntimeError):
    """Raised when a candidate is not eligible to be promoted."""


def build_food_record(
    candidate: TkpiCandidate,
    food_id: str,
    aliases: list[str],
    name: str | None = None,
) -> dict:
    """Turn an approved candidate into a data/foods.json-shaped record.

    BDD is carried over as provenance only - weight_basis stays 'edible' so
    no correction is auto-applied. Activating a BDD factor against a gross
    weight estimate is a separate, deliberate decision (see Phase 3D policy
    in data/foods.json and services/food_matcher.py), not something
    promotion should decide unilaterally.
    """
    record = {
        "id": food_id,
        "name": name or candidate.source_food_name,
        "aliases": list(aliases),
        "calories_per_100g": candidate.calories_per_100g,
        "protein_per_100g": candidate.protein_per_100g,
        "carbs_per_100g": candidate.carbs_per_100g,
        "fat_per_100g": candidate.fat_per_100g,
        "source": candidate.source,
        "source_reference": candidate.source_reference,
        "source_food_code": candidate.source_food_code,
        "source_food_name": candidate.source_food_name,
        "source_version": candidate.source_version,
        "data_status": "verified",
        "weight_basis": "edible",
        "edible_portion_source": "",
        "edible_portion_source_reference": "",
        "edible_portion_factor": None,
        "edible_portion_status": "not_applicable",
    }
    if candidate.bdd_percent is not None:
        record["edible_portion_factor"] = round(candidate.bdd_percent / 100.0, 4)
        record["edible_portion_status"] = "verified"
        record["edible_portion_source"] = candidate.source
        record["edible_portion_source_reference"] = candidate.source_reference
    return record


def promote(
    candidate: TkpiCandidate,
    approvals: list[ApprovalRecord],
    food_id: str,
    aliases: list[str],
    foods_path: Path,
    name: str | None = None,
) -> dict:
    """Append one approved candidate to the foods.json at `foods_path`.

    Raises PromotionError for any of: no matching approval, candidate not
    parsed, candidate not (spot_checked or officially_verified), or a
    food_id that already exists in the target file (promotion never
    silently overwrites an existing record).
    """
    if candidate.extraction_status != PARSED:
        raise PromotionError(
            f"{candidate.source_food_code}: extraction_status is "
            f"{candidate.extraction_status!r}, not 'parsed' - refusing to promote"
        )

    if candidate.verification_status not in (SPOT_CHECKED, OFFICIALLY_VERIFIED):
        raise PromotionError(
            f"{candidate.source_food_code}: verification_status is "
            f"{candidate.verification_status!r} - refusing to promote an unverified candidate"
        )

    approval = find_approval(candidate.source_food_code, approvals)
    if approval is None:
        raise PromotionError(
            f"{candidate.source_food_code}: no approval on record - "
            "run scripts/tkpi_promotion.approve_candidate first"
        )

    payload = json.loads(Path(foods_path).read_text(encoding="utf-8"))
    existing_ids = {food["id"] for food in payload.get("foods", [])}
    if food_id in existing_ids:
        raise PromotionError(f"food id {food_id!r} already exists in {foods_path} - refusing to overwrite")

    record = build_food_record(candidate, food_id, aliases, name=name)
    payload.setdefault("foods", []).append(record)
    Path(foods_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record
