# Phase 3E — TKPI 2020 source-integrity audit

**Scope:** all 24 rows in `data/tkpi_2020_candidates.json` (22 new pilot
candidates + AP001 + DR114, carried over from Phase 3D as already-verified
reference rows). This exceeds the ">= 15 rows, spread across all
transcribed pages, including AP001 and DR114" requirement.

**Source accessed:** the official Kemenkes repository page
(`https://repository.kemkes.go.id/book/668`) was opened directly in a
browser. It links to the actual PDF via Google Drive
(`https://drive.google.com/file/d/1qJZO-Bz1PVHcxWd7Gj_4CPW72gNH_LRo/view`).
The document is a 140-page **scanned** book (image pages, not machine text),
matching the repository's own metadata (title, ISBN 9786233010368, 2020,
140 halaman). No OCR tool is available in this environment, and there is no
local copy of the PDF to run one against even if there were — so every value
in this pilot was read by eye off the rendered pages
(`extraction_method: "manual_visual_transcription"`).

**Page-offset finding:** the PDF's own page index runs 4 pages ahead of the
book's printed page numbers (confirmed via the "TABEL KOMPOSISI PANGAN | 9"
footer stamped on PDF page 13, which is printed page 9). This offset was
verified consistently across three independent locations (AP001, the BR
table, and the DR table re-check below) and matches the Phase 3D citation
for AP001 ("halaman 10" print = PDF page 14).

**Method:** each sampled row was re-opened from the rendered PDF a *second
time*, independently of whatever was already typed into the candidate file,
at 300-400% zoom (the level at which digits stopped being ambiguous — see
discrepancies below, all of which were misreads at the lower ~225% zoom used
on the first pass). `data/tkpi_2020_candidates.json` itself was never used
as evidence — the comparison target was the rendered page, re-fetched each
time.

## Results

| Code | Name (as transcribed) | Page checked | Result |
|---|---|---|---|
| AP001 | Nasi | PDF p.14 (Tabel 4.1) | **PASS** — exact match to `data/foods.json` (180/3.0/0.3/39.8, BDD 100%). Confirms Phase 3D's verification independently. |
| AP002 | Nasi tim | PDF p.14 | PASS |
| AP003 | Tapai beras | PDF p.14 | PASS |
| AR015 | Jagung muda, kuning, mentah | PDF p.14 | PASS |
| AR016 | Jagung kuning pipil, kering, mentah | PDF p.14 | PASS |
| AR017 | Jagung pipil var. harapan, kering | PDF p.14 | PASS |
| AR018 | Jagung pipil var. metro, kering | PDF p.14 | PASS |
| AR019 | Jali, mentah | PDF p.14 | PASS |
| AR020 | Jawawut, mentah | PDF p.14 | PASS |
| AR021 | Jampang huma, mentah | PDF p.14 | **DISCREPANCY, corrected** — first pass (225% zoom) misread `carbs_per_100g` as 79.2; re-read at 350% zoom gives **78.2**. Candidate file holds the corrected value. |
| BR021 | Sagu lempeng | PDF p.21 (Tabel 4.2) | PASS |
| BR022 | Sagu singkong kering | PDF p.21 | PASS |
| BR023 | Sente, talas, segar | PDF p.21 | **DISCREPANCY, corrected** — first pass misread the food name as "Serfe, talas, segar"; re-read at 300% zoom gives **"Sente, talas, segar"**. Numeric values were already correct. |
| BR024 | Suweg, talas, segar | PDF p.21 | PASS |
| BR025 | Talas bogor, segar | PDF p.21 | PASS |
| BR026 | Talas pontianak, segar | PDF p.21 | PASS |
| BR027 | Talas viqueque, segar | PDF p.21 | **DISCREPANCY, corrected** — first pass misread `fat_per_100g` as 0.2; re-read at 350% zoom gives **0.5**. |
| BR028 | Ubi jalar, kuning, segar | PDF p.21 | **DISCREPANCY, corrected** — first pass misread `bdd_percent` as 86; re-read at 350% zoom gives **85**. |
| BR029 | Ubi jalar manis, segar | PDF p.21 | PASS |
| DR113 | Kool kembang | PDF p.40 (Tabel 4.4) | PASS |
| DR114 | Kool merah, kool putih | PDF p.40 | **PASS** — exact match to `data/foods.json` (29/1.4/0.2/5.3, BDD 75%). Confirms Phase 3D's verification independently. |
| DR115 | "Koro kanjuk", polong | PDF p.40 | **NOT CONFIRMED — stays unverified.** The four macro values and BDD were read identically on two independent passes (128/8.3/0.7/22.1, BDD 68%), but the food name is not legible with confidence at the scan's resolution even at 400% zoom. Recording a name we are not sure of would misrepresent the food, so `extraction_status = needs_review` and `verification_status = unverified` — this row is explicitly excluded from promotion until someone can read the name with more certainty (e.g. from a sharper scan, or Kemenkes' own errata/index). |
| DR116 | Koro wedus, polong | PDF p.40 | PASS |
| DR117 | Kucai, segar | PDF p.40 | **DISCREPANCY, corrected** — first pass misread `bdd_percent` as 52; re-read at 375% zoom gives **42**. |

**Totals:** 24 rows checked, 2 already-`officially_verified` (AP001, DR114)
re-confirmed exact, 21 rows moved to `spot_checked` after independently
matching the rendered PDF (5 of those only after a value was corrected from
a first-pass misread), 1 row (DR115) held at `unverified` because its name
could not be confirmed.

## High-risk value screen (section F)

`scripts/audit_tkpi_candidates.py` was run over the full candidate set
against the thresholds in the phase brief (protein > 40, carbs > 90,
fat > 50, BDD < 30, all-zero macros, calories > 600, suspicious
extra-decimal precision). **No row in this pilot triggers any of these
flags** — the pilot's food groups (cereals, starchy tubers, and a handful of
vegetables) don't happen to contain extreme values. This is reported
honestly as "nothing flagged" rather than as "nothing to check": a future,
larger extraction pass covering groups like legumes, meats, or dried/fried
foods should expect to actually exercise this screen.

## What this audit demonstrates

The two-pass process caught real errors: 5 of 22 new candidate rows
(23%) had at least one misread value or name at the first (lower-zoom)
reading. This is the concrete argument for why `extraction_status: parsed`
must never be conflated with `verification_status: verified` — a
structurally well-formed row (right shape, plausible ranges, all fields
present) was still wrong in almost a quarter of cases in this pilot, until
someone looked a second time, independently, at the actual rendered source.
