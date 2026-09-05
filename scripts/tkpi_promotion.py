"""Explicit approval ledger for promoting a TKPI candidate to production.

Phase 3E section G: "parsed" must never be enough to reach production, and
neither may a candidate become verified merely because it parsed. This
module is the gate: an ApprovalRecord can only be created when every field
of a candidate has been individually, explicitly confirmed - not just
"the row looked fine".

This module never touches data/foods.json. See scripts/promote_tkpi_candidate.py
for the (separate, explicit) step that actually writes a food record, and
only once an approval exists here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.tkpi_extraction import OFFICIALLY_VERIFIED, PARSED, SPOT_CHECKED, TkpiCandidate

APPROVED_PATH = Path(__file__).resolve().parent.parent / "data" / "tkpi_approved.json"

# Every one of these must be explicitly confirmed True - no defaults, no
# partial approval. This is the "exact food identity required, all macros
# confirmed" requirement made concrete.
REQUIRED_CONFIRMATIONS = (
    "code_confirmed",
    "name_confirmed",
    "calories_confirmed",
    "protein_confirmed",
    "fat_confirmed",
    "carbs_confirmed",
    "page_reference_confirmed",
)


class ApprovalError(RuntimeError):
    """Raised when a candidate does not qualify for approval."""


@dataclass(frozen=True)
class ApprovalRecord:
    source_food_code: str
    approver: str
    approved_at: str
    confirmations: dict
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def approve_candidate(
    candidate: TkpiCandidate,
    approver: str,
    confirmations: dict,
    notes: str = "",
) -> ApprovalRecord:
    """Create an approval record, or raise ApprovalError.

    A candidate qualifies only when:
      - its extraction_status is "parsed" (never needs_review or rejected -
        a row that failed structural validation cannot be approved no
        matter how confident the approver is), and
      - its verification_status is "spot_checked" or "officially_verified"
        (never "unverified" - parsing successfully is not verification), and
      - every field in REQUIRED_CONFIRMATIONS is explicitly True.
    """
    if candidate.extraction_status != PARSED:
        raise ApprovalError(
            f"{candidate.source_food_code}: cannot approve a candidate with "
            f"extraction_status={candidate.extraction_status!r} (must be 'parsed')"
        )

    if candidate.verification_status not in (SPOT_CHECKED, OFFICIALLY_VERIFIED):
        raise ApprovalError(
            f"{candidate.source_food_code}: cannot approve a candidate with "
            f"verification_status={candidate.verification_status!r} "
            "(must be 'spot_checked' or 'officially_verified' - never 'unverified')"
        )

    missing = [key for key in REQUIRED_CONFIRMATIONS if confirmations.get(key) is not True]
    if missing:
        raise ApprovalError(
            f"{candidate.source_food_code}: missing explicit confirmation for: {', '.join(missing)}"
        )

    if not approver.strip():
        raise ApprovalError("an approval must name an approver")

    return ApprovalRecord(
        source_food_code=candidate.source_food_code,
        approver=approver.strip(),
        approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        confirmations={key: True for key in REQUIRED_CONFIRMATIONS},
        notes=notes,
    )


def load_approved(path: Path = APPROVED_PATH) -> list[ApprovalRecord]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        ApprovalRecord(
            source_food_code=raw["source_food_code"],
            approver=raw["approver"],
            approved_at=raw["approved_at"],
            confirmations=raw["confirmations"],
            notes=raw.get("notes", ""),
        )
        for raw in payload.get("approved", [])
    ]


def save_approved(records: list[ApprovalRecord], path: Path = APPROVED_PATH) -> None:
    payload = {
        "_comment": (
            "Approval ledger for the Phase 3E TKPI promotion pipeline. An entry here "
            "does NOT by itself change data/foods.json - scripts/promote_tkpi_candidate.py "
            "requires a further explicit run to apply it."
        ),
        "approved": [record.to_dict() for record in records],
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_approval(code: str, records: list[ApprovalRecord]) -> ApprovalRecord | None:
    for record in records:
        if record.source_food_code == code:
            return record
    return None
