"""Phase 3E pilot extraction: TKPI 2020, four food groups, by hand.

WHAT THIS IS
------------
The official source (Tabel Komposisi Pangan Indonesia 2020, Kementerian
Kesehatan RI, ISBN 9786233010368) is a 140-page SCANNED PDF - a photograph of
a printed book, not a text layer. It is published at
https://repository.kemkes.go.id/book/668, which links to a Google Drive
viewer as the actual PDF host. There is no OCR tool available in this
environment, and no local copy of the PDF file to feed one even if there
were.

Every value below was therefore read by eye off the rendered PDF pages (zoom
150-400% in the Google Drive viewer) on 2026-09-05, by the agent performing
this phase, and cross-checked a second time at higher zoom before being
recorded here. That second pass caught and corrected several first-read
errors (see reports/phase_3e_tkpi_audit.md for the specific cases) - which is
exactly why extraction_status and verification_status are tracked separately
from whether a row merely parses. extraction_method is honestly recorded as
"manual_visual_transcription", never as automated OCR.

WHAT THIS IS NOT
----------------
This is a PILOT covering 4 of the source's many food-group tables (about
1.5 of 140 pages). It is not the complete TKPI 2020 dataset, and none of the
rows below are production data - they are candidates that still require the
independent audit in reports/phase_3e_tkpi_audit.md and an explicit
promotion step (scripts/tkpi_promotion.py) before they could ever reach
data/foods.json. Running this script only ever writes
data/tkpi_2020_candidates.json - it never touches data/foods.json.

Groups covered:
  - Tabel 4.1 Serealia dan Hasil Olahannya (subsections TUNGGAL/SINGLE "AR"
    and OLAHAN/PRODUK/KOMPOSIT "AP"), PDF page 14 / printed page 10.
  - Tabel 4.2 Umbi Berpati dan Hasil Olahannya ("BR"), PDF page 21 / printed
    page 17.
  - Tabel 4.4 Sayuran dan Hasil Olahannya ("DR"), PDF page 40 / printed page
    36 - the same table and page nasi_putih (AP001) and kol (DR114) were
    already verified against in Phase 3D.

AP001 and DR114 are included here too, as already-verified reference rows
carried into the new pipeline for continuity (see Phase 3D / data/foods.json)
- they are not re-promoted from here, and this script does not modify
data/foods.json.
"""

from __future__ import annotations

from pathlib import Path

from scripts.tkpi_extraction import (
    MANUAL_VISUAL_TRANSCRIPTION,
    OFFICIALLY_VERIFIED,
    SPOT_CHECKED,
    UNVERIFIED,
    candidate_from_raw,
    find_duplicate_codes,
    save_candidates,
)

# Rows independently re-checked against the rendered PDF during the Phase 3E
# source-integrity audit (reports/phase_3e_tkpi_audit.md) and confirmed to
# match. DR115 is deliberately excluded: its numbers were confirmed but its
# food-name identity was not, so it stays "unverified" pending a clearer look.
_AUDITED_CODES = {
    "AP002", "AP003",
    "AR015", "AR016", "AR017", "AR018", "AR019", "AR020", "AR021",
    "BR021", "BR022", "BR023", "BR024", "BR025", "BR026", "BR027", "BR028", "BR029",
    "DR113", "DR116", "DR117",
}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "tkpi_2020_candidates.json"

SOURCE = "Tabel Komposisi Pangan Indonesia (TKPI) 2020, Kementerian Kesehatan RI (ISBN 9786233010368)"
SOURCE_VERSION = "TKPI 2020"
SOURCE_URL = "https://repository.kemkes.go.id/book/668"
ACCESS_NOTE = (
    "dibaca langsung dari salinan PDF resmi (tautan Google Drive resmi dari "
    f"{SOURCE_URL}) pada zoom 300-400%, ditranskripsi manual (bukan OCR), 5 Sep 2026"
)

GROUP_AP_AR = {
    "table": "Tabel 4.1 Serealia dan Hasil Olahannya",
    "pdf_page": "14",
    "printed_page": "10",
}
GROUP_BR = {
    "table": "Tabel 4.2 Umbi Berpati dan Hasil Olahannya",
    "pdf_page": "21",
    "printed_page": "17",
}
GROUP_DR = {
    "table": "Tabel 4.4 Sayuran dan Hasil Olahannya",
    "pdf_page": "40",
    "printed_page": "36",
}


def _page(group: dict) -> str:
    return f"PDF hlm. {group['pdf_page']} (halaman cetak {group['printed_page']})"


def _reference(group: dict, internal_row: str, extra: str = "") -> str:
    base = (
        f"{group['table']}, {_page(group)} - {ACCESS_NOTE}. "
        f"Baris sumber internal TKPI: {internal_row}."
    )
    return f"{base} {extra}".strip()


# Each raw row is exactly what was read off the page: code, name, the four
# macros, and BDD%. Nothing here is guessed or backfilled from a mirror.
RAW_ROWS = [
    # --- Tabel 4.1, OLAHAN/PRODUK/KOMPOSIT (AP) ---------------------------
    dict(
        source_food_code="AP001", source_food_name="Nasi",
        calories_per_100g=180, protein_per_100g=3.0, fat_per_100g=0.3, carbs_per_100g=39.8,
        bdd_percent=100, source_internal_row="KZGPI-1990",
        verification_status=OFFICIALLY_VERIFIED,
        extraction_notes=(
            "Sudah diverifikasi Phase 3D (lihat data/foods.json, nasi_putih). Dibawa ke "
            "pipeline Phase 3E sebagai baris referensi untuk audit ulang, bukan kandidat baru."
        ),
    ),
    dict(
        source_food_code="AP002", source_food_name="Nasi tim",
        calories_per_100g=120, protein_per_100g=2.4, fat_per_100g=0.4, carbs_per_100g=26.0,
        bdd_percent=100, source_internal_row="OKN-1992",
    ),
    dict(
        source_food_code="AP003", source_food_name="Tapai beras",
        calories_per_100g=99, protein_per_100g=1.7, fat_per_100g=0.3, carbs_per_100g=22.4,
        bdd_percent=100, source_internal_row="KZGMI-2001",
    ),
    # --- Tabel 4.1, TUNGGAL/SINGLE (AR) -------------------------------------
    dict(
        source_food_code="AR015", source_food_name="Jagung muda, kuning, mentah",
        calories_per_100g=147, protein_per_100g=5.1, fat_per_100g=0.7, carbs_per_100g=31.5,
        bdd_percent=100, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="AR016", source_food_name="Jagung kuning pipil, kering, mentah",
        calories_per_100g=366, protein_per_100g=9.8, fat_per_100g=7.3, carbs_per_100g=69.1,
        bdd_percent=100, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="AR017", source_food_name="Jagung pipil var. harapan, kering",
        calories_per_100g=367, protein_per_100g=6.2, fat_per_100g=5.1, carbs_per_100g=78.2,
        bdd_percent=100, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="AR018", source_food_name="Jagung pipil var. metro, kering",
        calories_per_100g=368, protein_per_100g=5.5, fat_per_100g=4.6, carbs_per_100g=78.0,
        bdd_percent=100, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="AR019", source_food_name="Jali, mentah",
        calories_per_100g=324, protein_per_100g=11.0, fat_per_100g=4.0, carbs_per_100g=61.0,
        bdd_percent=90, source_internal_row="DABM-1964",
    ),
    dict(
        source_food_code="AR020", source_food_name="Jawawut, mentah",
        calories_per_100g=364, protein_per_100g=9.7, fat_per_100g=3.5, carbs_per_100g=73.4,
        bdd_percent=100, source_internal_row="DABM-1964",
    ),
    dict(
        source_food_code="AR021", source_food_name="Jampang huma, mentah",
        calories_per_100g=350, protein_per_100g=6.2, fat_per_100g=1.4, carbs_per_100g=78.2,
        bdd_percent=100, source_internal_row="DABM-1964",
        extraction_notes=(
            "Pembacaan awal (zoom 225%) sempat salah baca KH sebagai 79.2; dikoreksi "
            "menjadi 78.2 setelah dibaca ulang pada zoom 350%. Lihat audit report."
        ),
    ),
    # --- Tabel 4.2, Umbi Berpati (BR) ---------------------------------------
    dict(
        source_food_code="BR021", source_food_name="Sagu lempeng",
        calories_per_100g=347, protein_per_100g=0.9, fat_per_100g=0.3, carbs_per_100g=85.2,
        bdd_percent=100, source_internal_row="KZGMI-2001",
    ),
    dict(
        source_food_code="BR022", source_food_name="Sagu singkong kering",
        calories_per_100g=362, protein_per_100g=0.5, fat_per_100g=0.3, carbs_per_100g=86.9,
        bdd_percent=100, source_internal_row="DABM-1964",
    ),
    dict(
        source_food_code="BR023", source_food_name="Sente, talas, segar",
        calories_per_100g=353, protein_per_100g=0.7, fat_per_100g=0.2, carbs_per_100g=84.7,
        bdd_percent=86, source_internal_row="DABM-1964",
        extraction_notes=(
            "Nama sempat terbaca 'Serfe' pada zoom 225%; dikoreksi menjadi 'Sente' "
            "setelah dibaca ulang pada zoom 300%."
        ),
    ),
    dict(
        source_food_code="BR024", source_food_name="Suweg, talas, segar",
        calories_per_100g=74, protein_per_100g=1.4, fat_per_100g=0.1, carbs_per_100g=17.2,
        bdd_percent=86, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="BR025", source_food_name="Talas bogor, segar",
        calories_per_100g=108, protein_per_100g=1.4, fat_per_100g=0.4, carbs_per_100g=25.0,
        bdd_percent=85, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="BR026", source_food_name="Talas pontianak, segar",
        calories_per_100g=163, protein_per_100g=2.3, fat_per_100g=0.5, carbs_per_100g=36.4,
        bdd_percent=83, source_internal_row="KZGPI-1990",
    ),
    dict(
        source_food_code="BR027", source_food_name="Talas viqueque, segar",
        calories_per_100g=115, protein_per_100g=1.8, fat_per_100g=0.5, carbs_per_100g=25.9,
        bdd_percent=85, source_internal_row="KZGMI-2001",
        extraction_notes=(
            "Pembacaan awal (zoom 225%) sempat salah baca lemak sebagai 0.2; dikoreksi "
            "menjadi 0.5 setelah dibaca ulang pada zoom 350%. Lihat audit report."
        ),
    ),
    dict(
        source_food_code="BR028", source_food_name="Ubi jalar, kuning, segar",
        calories_per_100g=119, protein_per_100g=0.5, fat_per_100g=0.4, carbs_per_100g=25.1,
        bdd_percent=85, source_internal_row="KZGMI-2001",
        extraction_notes=(
            "BDD pembacaan awal (zoom 225%) sempat terbaca 86; dikoreksi menjadi 85 "
            "setelah dibaca ulang pada zoom 350%. Lihat audit report."
        ),
    ),
    dict(
        source_food_code="BR029", source_food_name="Ubi jalar manis, segar",
        calories_per_100g=83, protein_per_100g=1.5, fat_per_100g=0.2, carbs_per_100g=18.8,
        bdd_percent=65, source_internal_row="KZGPI-1990",
    ),
    # --- Tabel 4.4, Sayuran (DR) ---------------------------------------------
    dict(
        source_food_code="DR113", source_food_name="Kool kembang",
        calories_per_100g=25, protein_per_100g=2.4, fat_per_100g=0.2, carbs_per_100g=4.9,
        bdd_percent=57, source_internal_row="DABM-1964",
    ),
    dict(
        source_food_code="DR114", source_food_name="Kool merah, kool putih",
        calories_per_100g=29, protein_per_100g=1.4, fat_per_100g=0.2, carbs_per_100g=5.3,
        bdd_percent=75, source_internal_row="DABM-1964",
        verification_status=OFFICIALLY_VERIFIED,
        extraction_notes=(
            "Sudah diverifikasi Phase 3D (lihat data/foods.json, kol). Dibawa ke pipeline "
            "Phase 3E sebagai baris referensi untuk audit ulang, bukan kandidat baru."
        ),
    ),
    dict(
        source_food_code="DR115", source_food_name="Koro kanjuk, polong",
        calories_per_100g=128, protein_per_100g=8.3, fat_per_100g=0.7, carbs_per_100g=22.1,
        bdd_percent=68, source_internal_row="DABM-1964",
        uncertain=True,
        uncertainty_reason=(
            "Nama bahan tidak sepenuhnya terbaca jelas pada scan (kandidat: 'Koro "
            "kanjuk'); tidak ditemukan padanan nama umum yang meyakinkan. Angka gizi "
            "terbaca jelas dan konsisten pada dua kali pembacaan, tetapi identitas "
            "pangan harus dikonfirmasi ulang terhadap PDF sebelum baris ini digunakan."
        ),
    ),
    dict(
        source_food_code="DR116", source_food_name="Koro wedus, polong",
        calories_per_100g=46, protein_per_100g=3.0, fat_per_100g=0.3, carbs_per_100g=7.9,
        bdd_percent=70, source_internal_row="DABM-1964",
    ),
    dict(
        source_food_code="DR117", source_food_name="Kucai, segar",
        calories_per_100g=45, protein_per_100g=2.2, fat_per_100g=0.3, carbs_per_100g=10.3,
        bdd_percent=42, source_internal_row="DABM-1964",
        extraction_notes=(
            "BDD pembacaan awal (zoom 225%) sempat terbaca 52; dikoreksi menjadi 42 "
            "setelah dibaca ulang pada zoom 375%. Lihat audit report."
        ),
    ),
]

_GROUP_BY_PREFIX = {"AP": GROUP_AP_AR, "AR": GROUP_AP_AR, "BR": GROUP_BR, "DR": GROUP_DR}


def build_candidates() -> list:
    candidates = []
    for raw in RAW_ROWS:
        code = raw["source_food_code"]
        group = _GROUP_BY_PREFIX[code[:2]]
        default_verification = SPOT_CHECKED if code in _AUDITED_CODES else UNVERIFIED
        notes = raw.get("extraction_notes", "")
        if code in _AUDITED_CODES:
            audit_note = "Dicek ulang terhadap render PDF pada audit Phase 3E (reports/phase_3e_tkpi_audit.md): PASS."
            notes = f"{notes} {audit_note}".strip()
        enriched = {
            **raw,
            "source": SOURCE,
            "source_version": SOURCE_VERSION,
            "source_page": _page(group),
            "source_reference": _reference(group, raw["source_internal_row"]),
            "extraction_method": MANUAL_VISUAL_TRANSCRIPTION,
            "verification_status": raw.get("verification_status", default_verification),
            "extraction_notes": notes,
        }
        candidates.append(candidate_from_raw(enriched))
    return candidates


def main() -> None:
    candidates = build_candidates()

    duplicates = find_duplicate_codes(candidates)
    if duplicates:
        raise SystemExit(f"Refusing to write candidates: duplicate food codes {duplicates}")

    metadata = {
        "official_source": SOURCE,
        "official_source_url": SOURCE_URL,
        "source_access_note": (
            "PDF resmi bersifat scan (bukan teks); tidak ada alat OCR di lingkungan ini, "
            "sehingga seluruh baris ditranskripsi manual dari tampilan render PDF."
        ),
        "coverage": "PARTIAL - pilot pipeline demonstration only, NOT the complete TKPI 2020 dataset",
        "groups_covered": [
            GROUP_AP_AR["table"] + " (subset)",
            GROUP_BR["table"] + " (subset)",
            GROUP_DR["table"] + " (subset)",
        ],
        "total_rows": len(candidates),
        "note": (
            "Candidate rows are NOT automatically verified production foods. See "
            "reports/phase_3e_tkpi_audit.md for the independent audit and "
            "scripts/tkpi_promotion.py for the explicit approval step required before "
            "any row can reach data/foods.json."
        ),
    }
    save_candidates(OUTPUT_PATH, candidates, metadata)
    print(f"Wrote {len(candidates)} candidate rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
