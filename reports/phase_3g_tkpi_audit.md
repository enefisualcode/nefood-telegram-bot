# Phase 3G — Nutrition coverage expansion, batch 1: TKPI audit

**Scope:** 7 foods observed in real bot use with no safe verified nutrition
mapping: `ayam bumbu merah`, `tempe goreng`, `tahu goreng`, `urap sayur`,
`ikan asin goreng`, `daun selada`, `timun`.

**Source accessed:** the official Kemenkes repository page
(`https://repository.kemkes.go.id/book/668`), which links to the actual PDF
via Google Drive (same document used in Phase 3D/3E — 140-page scan, ISBN
9786233010368). Every value below was read directly off the rendered PDF
page (150-250% zoom), not from any third-party mirror or recalled memory.
`Tabel 2. Sistem Pengkodean Bahan Pangan` (PDF p.7) was used to identify
which food-group table each target food would fall under, from its code
prefix (raw/olahan pairs: AR/AP Serealia, BR/BP Umbi, CR/CP Kacang-biji-bean,
DR/DP Sayur, ER/EP Buah, FR/FP Daging&unggas, GR/GP Ikan-kerang-udang,
HR/HP Telur, ...).

## Results

### tempe goreng — VERIFIED LOCAL

**CP076 "Tempe kedelai murni, goreng"**, Tabel 4.3 Kacang, Biji, Bean dan
Hasil Olahannya, PDF p.32 (halaman cetak 28), sumber internal KZGPI-1990.
Energi 350 kcal, protein 24.5 g, lemak 26.6 g, KH 10.4 g, BDD 100%.

This code and these exact values were already noted as a *candidate* during
Phase 3D's investigation of the ambiguous "tempe" record, but were never
independently checked against the rendered PDF at the time. Phase 3G re-read
the row directly from the render and confirms an exact match. Promoted as
`tempe_goreng`, a new record distinct from the existing `tempe` (which stays
provisional/mentah — the two preparations differ enormously in fat and
calories).

### tahu goreng — VERIFIED LOCAL

**CP062 "Tahu goreng"**, same table, PDF p.32 (halaman cetak 28), sumber
KZGPI-1990. Energi 115 kcal, protein 9.7 g, lemak 8.5 g, KH 2.5 g, BDD 100%.
Same situation as tempe goreng: a Phase 3D candidate, now independently
re-confirmed against the rendered PDF. Promoted as `tahu_goreng`, distinct
from the existing `tahu` (mentah).

### daun selada — VERIFIED LOCAL

**DR145 "Selada, segar"**, Tabel 4.4 Sayuran dan Hasil Olahannya, PDF p.41
(halaman cetak 37), sumber DABM-1964. Energi 18 kcal, protein 1.2 g, lemak
0.2 g, KH 2.9 g, BDD 69%. The entry is explicitly "segar" (fresh/raw),
matching how lettuce is normally seen in a meal photo (as lalapan/garnish,
not cooked) — a defensible exact identity match. Promoted as `selada`
(aliased to `daun selada` and `lettuce`).

### timun — VERIFIED LOCAL

**DR109 "Ketimun, segar"**, same table, PDF p.39 (halaman cetak 35), sumber
KZGPI-1990. Energi 8 kcal, protein 0.2 g, lemak 0.2 g, KH 1.4 g, BDD 55%.

TKPI lists three cucumber entries: DR109 "Ketimun, segar" (no variety
qualifier), DR110 "Ketimun krai, segar", and DR111 "Ketimun madura, segar" —
each with different values. DR109 was chosen because it is the *only*
unqualified entry, making it the sole defensible match for a plain "timun"
detection (which likewise carries no variety information) — the same logic
already used for `nasi_putih` (plain "Nasi") over its many named variants.

**BDD verification note:** 55% is unusually low for a whole raw cucumber
(commonly assumed to be ~90-100% edible). Because the number was surprising,
it was independently re-read from the rendered PDF **three separate times**
using progressively more careful row-alignment techniques (a wide-table
capture with only numeric landmarks was found to be error-prone — see the
Phase 3E audit for the same lesson with different rows). All three passes
converged on 55%, including a final pass with the food name and the BDD
value visible in the *same* screenshot (eliminating row-counting ambiguity
entirely). The value is reported as read; the existing USDA fallback
(`services/usda_food_data.py`, "timun" query) is left in place as a safety
net but is no longer invoked while this local record stays verified.

### ayam bumbu merah — UNAVAILABLE (no defensible source)

Searched the entire "Ayam"-prefixed cluster of Tabel 4.6 Daging, Unggas dan
Hasil Olahannya (FP001–FP041, PDF p.53–56, halaman cetak 49–52): `Ayam,
ampela goreng` (FP001), `Ayam, usus, goreng` (FP002), `Ayam laheng, masakan`
(FP034), `Ayam kuah lodeh, masakan` (FP035), and the branded fried-chicken
entries already known from Phase 3D (church texas, kalasan, kentucky x3,
mbok berek, pasundan x2, pioneer, sudukan x2). No bumbu-based preparation
("bumbu merah", "bumbu bali", "bumbu rujak", or similar) exists anywhere in
this list. USDA FoodData Central has no equivalent either — it has no
Indonesian spice-paste chicken preparations, and mapping to plain cooked
chicken would misrepresent the dish (the sauce/oil changes the nutrition
profile substantially, the same reasoning that keeps plain `ayam_goreng`
provisional). **No record added; stays unavailable**, per the phase brief's
own example of what must not be forced.

### ikan asin goreng — UNAVAILABLE (preparation mismatch)

Searched Tabel 4.7 Ikan, Kerang, Udang dan Hasil Olahannya (GP001–GP068+,
PDF p.61–68, halaman cetak 57–64). Found **GP044 "Ikan asin, kering,
mentah"** (KZGPI-1990) — but this is explicitly raw/dried ("mentah"), not
fried ("goreng"). No fried salted-fish variant exists anywhere nearby (the
surrounding entries — teri nasi, teri kering, teri bubuk — are all also
"mentah"). Promoting the raw/dried record to serve a "goreng" detection
would repeat the exact mentah-vs-goreng mismatch already avoided for
tempe/tahu/telur in Phase 3D — frying adds substantial oil, which raw/dried
fish values do not reflect. USDA has no defensible equivalent either (no
Indonesian-style salted, sun-dried reef/freshwater fish, fried or otherwise)
— matches the phase brief's own worked example. **No record added; stays
unavailable.**

### urap sayur — UNAVAILABLE (no exact prepared-dish record)

Searched the complete Sayur, Olahan section (Tabel 4.4, DP001–DP051, PDF
p.43–45, halaman cetak 39–41) — alphabetically complete from "Bacem" through
"Sayur lim-teruduk" (the section ends there; "4.5 Buah" begins immediately
on the next page). Found many composite vegetable dishes (`Gado-gado`
DP030, `Karedok, sayur` DP037, `Pelecing kangkung` DP043, `Rujak cingur`
DP046, `Sayur kohu-kohu` DP050 — a similar coconut-and-vegetable dish from
Sulawesi) but **no "Urap" entry of any kind**. Per section E of the phase
brief, no single hard-coded value was assigned and no recipe (coconut
weight, vegetable proportions) was invented. **No record added; stays
unavailable as a single item** — a future phase could still decompose it
into visible components the same way Phase 3F handles other compound
dishes, but that is out of scope here.

## Summary table

| Food | Result | Code | Page (PDF/cetak) | BDD |
|---|---|---|---|---|
| tempe goreng | verified local | CP076 | 32/28 | 100% |
| tahu goreng | verified local | CP062 | 32/28 | 100% |
| daun selada | verified local | DR145 | 41/37 | 69% |
| timun | verified local | DR109 | 39/35 | 55% |
| ayam bumbu merah | unavailable | — | — | — |
| ikan asin goreng | unavailable | — | — | — |
| urap sayur | unavailable | — | — | — |
