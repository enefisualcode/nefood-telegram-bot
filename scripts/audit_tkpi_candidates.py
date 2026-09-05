"""Structural audit of data/tkpi_2020_candidates.json.

This script only checks what a machine can check: required fields, plausible
ranges, duplicate codes, and a high-risk value screen. It cannot compare a
row against the rendered PDF - that is the actual independent audit required
by Phase 3E section F, and it was done by hand; see
reports/phase_3e_tkpi_audit.md for those findings.

Running this script never changes verification_status - only a human who has
actually looked at the rendered PDF page may move a row from "unverified" to
"spot_checked" or "officially_verified" (done directly in the candidate file,
with the specific page checked cited in extraction_notes / the audit report).
"""

from __future__ import annotations

from pathlib import Path

from scripts.tkpi_extraction import (
    NEEDS_REVIEW,
    OFFICIALLY_VERIFIED,
    PARSED,
    REJECTED,
    SPOT_CHECKED,
    UNVERIFIED,
    find_duplicate_codes,
    high_risk_reasons,
    load_candidates,
)

CANDIDATES_PATH = Path(__file__).resolve().parent.parent / "data" / "tkpi_2020_candidates.json"


def audit(path: Path = CANDIDATES_PATH) -> dict:
    candidates = load_candidates(path)

    by_extraction = {status: [] for status in (PARSED, NEEDS_REVIEW, REJECTED)}
    for c in candidates:
        by_extraction.setdefault(c.extraction_status, []).append(c)

    by_verification = {status: [] for status in (UNVERIFIED, SPOT_CHECKED, OFFICIALLY_VERIFIED)}
    for c in candidates:
        by_verification.setdefault(c.verification_status, []).append(c)

    duplicates = find_duplicate_codes(candidates)
    high_risk = {c.source_food_code: high_risk_reasons(c) for c in candidates}
    high_risk = {code: reasons for code, reasons in high_risk.items() if reasons}

    return {
        "total": len(candidates),
        "by_extraction_status": {k: len(v) for k, v in by_extraction.items()},
        "by_verification_status": {k: len(v) for k, v in by_verification.items()},
        "duplicate_codes": duplicates,
        "high_risk": high_risk,
        "candidates": candidates,
    }


def print_report(path: Path = CANDIDATES_PATH) -> None:
    result = audit(path)
    print(f"TKPI candidate audit: {path}")
    print(f"  total rows: {result['total']}")
    print(f"  extraction_status: {result['by_extraction_status']}")
    print(f"  verification_status: {result['by_verification_status']}")

    if result["duplicate_codes"]:
        print(f"  DUPLICATE CODES FOUND: {result['duplicate_codes']}")
    else:
        print("  duplicate codes: none")

    if result["high_risk"]:
        print("  high-risk rows flagged for manual PDF re-check:")
        for code, reasons in result["high_risk"].items():
            print(f"    - {code}: {'; '.join(reasons)}")
    else:
        print("  high-risk rows: none flagged")


if __name__ == "__main__":
    print_report()
