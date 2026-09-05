# Nutrition Telegram Bot

A Telegram bot that recognises the foods in a photo using the Google Gemini API.

**Current phase: 3E — TKPI extraction pipeline + Indonesian dish foundation (pilot).**
The bot detects visible foods, estimates portions, and — after the user confirms — calculates
nutrition from a local curated database. Phase 3E adds a reproducible pipeline for turning
official TKPI 2020 pages into reviewable candidates, plus a compound-dish decomposition
foundation — **neither is wired into the running bot yet**; both are offline tooling and data
files only.

Nutrition values never come from the vision model. Gemini only identifies foods and
estimates grams; all calorie and macro values come from `data/foods.json`, where every
record cites its source. A food with no record is reported as unavailable rather than
guessed.

### Nutrition source priority

1. **Verified local record** in `data/foods.json`
2. **USDA FoodData Central** — live API lookup, for foods with no verified local
   record (a *provisional* local record does not block this fallback)
3. **Unavailable** — reported to the user, excluded from the total

USDA lookups use a small curated Indonesian-to-USDA query map in
`services/usda_food_data.py`; only generic `Foundation`/`SR Legacy` records are
accepted, and each candidate must pass explicit include/exclude token rules. A USDA
failure (timeout, 429, bad JSON, missing nutrient, no safe match) affects only that
one food — the rest of the meal still totals. Results are cached in memory for the
process lifetime. Without `USDA_API_KEY` the fallback is simply skipped.

### Edible portion (BDD)

Some estimates are gross weight (a chicken piece includes bone), while nutrition
databases describe the edible portion. Each record declares:

- `weight_basis`: `gross` (may need correction) or `edible` (already edible mass)
- `edible_portion_factor`: decimal in (0, 1]
- `edible_portion_status`: `verified` | `provisional` | `not_applicable`
- `edible_portion_source` / `edible_portion_source_reference`

A factor is applied **only** when `weight_basis` is `gross` AND
`edible_portion_status` is `verified`. Otherwise the original grams are used
unchanged and `edible_portion_applied` is recorded as false. The vision model never
supplies a BDD factor. The original estimate is always preserved alongside the
adjusted one, as `estimated_gross_grams` and `calculated_edible_grams`.

**No verified BDD factor is currently shipped**, so no correction is applied to any
food today. `ayam_goreng` is marked `weight_basis: gross` to record that it will
need one once an official figure (TKPI / Kemenkes) can be verified.

### Verified Indonesian (TKPI) data

Local records can now cite fine-grained nutrition provenance: `source_food_code`,
`source_food_name`, `source_version`, alongside the existing `source` /
`source_reference`. Nothing here changes the source priority - it is still
**verified local record -> USDA FoodData Central -> unavailable** - it only makes
"verified local" achievable with a real Indonesian source (TKPI) instead of only USDA.

A food is promoted to `data_status: verified` only when an official source (TKPI /
Kemenkes) has an exact or clearly defensible match - not merely a similarly-named
one, and only after the exact code and values have been read directly from the
official document itself (a third-party mirror may be used to *discover* a
candidate, but never as the sole basis for `verified`). Two foods are currently
verified this way: `nasi_putih` (TKPI 2020, code AP001) and `kol` (TKPI 2020, code
DR114) - both confirmed directly against the official PDF at
repository.kemkes.go.id/book/668 (ISBN 9786233010368).

**`kemangi` was investigated, promoted, then reverted.** A third-party mirror
reported TKPI code DR039 as "Daun kemangi, segar" (basil), and that name/value pair
was initially trusted and promoted to verified. A follow-up audit read the official
PDF directly and found DR039 is actually **"Daun kemang, segar"** - the leaf of
*kemang* (a mango relative, Mangifera kemanga), a different plant from kemangi
(basil, Ocimum basilicum) - with no separate kemangi entry anywhere in TKPI's
Sayuran section (DR001-DR166). The record was removed rather than left as a
mislabeled "provisional" entry; `kemangi` now relies solely on its existing USDA
FoodData Central mapping (verified live in Phase 3B, FDC 172232 "Basil, fresh").
This is why every mirror-sourced value now gets a second, independent check against
the primary document before promotion.

Several other foods were investigated and deliberately **not** promoted because TKPI
only offers ambiguous candidates for them (see `data/foods.json` for the reasoning
recorded on each record) - e.g. TKPI's only `ayam goreng` entries are
branded/regional (Kentucky, Pasundan, ...), and `tempe`/`tahu` split into
raw/fried entries with very different values while Gemini's detection does not say
which one was seen. Silently picking one would misrepresent the food, so those stay
`provisional` and continue to rely on the USDA fallback.

### TKPI 2020 extraction pipeline (Phase 3E — pilot, partial coverage)

`scripts/extract_tkpi_2020.py` transcribes rows from the official **Tabel Komposisi
Pangan Indonesia 2020** (Kementerian Kesehatan RI, ISBN 9786233010368,
repository.kemkes.go.id/book/668) into `data/tkpi_2020_candidates.json`. The official
document is a 140-page **scanned** PDF with no text layer; no OCR tool is available in
this environment, so every row is read by eye off the rendered page and recorded with
`extraction_method: "manual_visual_transcription"` — never labelled as automated OCR.

**This pilot covers 4 subsections across 3 of the source's ~30+ food-group tables**
(Serealia AP/AR, Umbi Berpati BR, Sayuran DR) — **24 rows in total. This is not the
complete TKPI 2020 dataset**, and none of these rows are production data. A candidate
row's `extraction_status` (`parsed` / `needs_review` / `rejected`) describes whether the
row is structurally well-formed — it says nothing about whether the values are correct.
Correctness is tracked separately as `verification_status` (`unverified` / `spot_checked`
/ `officially_verified`), and only changes when a human has independently compared the
row against the *rendered* official PDF — never against the candidate file itself.

`reports/phase_3e_tkpi_audit.md` documents that independent audit: all 24 rows were
checked (comfortably over the required minimum of 15), including AP001 and DR114 (the
two records already `verified` in `data/foods.json` since Phase 3D — both re-confirmed
as an exact match). **5 of the 22 new candidate rows had a wrong digit or an unclear
name on the first, lower-zoom read**, caught only by looking a second time at higher
zoom — concrete evidence for why "parsed" must never be read as "verified". One row
(DR115) still has an illegible food name and is deliberately held at `unverified`.

`scripts/audit_tkpi_candidates.py` runs the checks a machine actually can run:
required fields, plausible food codes, non-negative macros, BDD in [0, 100], duplicate
codes, and a high-risk screen (very high/low macros, all-zero rows, suspicious
precision) — none of which fired on this pilot's data.

**Promotion is a separate, explicit step and nothing has been promoted.**
`data/tkpi_approved.json` is an approval ledger — an entry there means a human
explicitly confirmed every field of one candidate against the rendered PDF, but by
itself it still changes nothing. `scripts/tkpi_promotion.py` refuses to create an
approval for any candidate that isn't `extraction_status=parsed` and
`verification_status` in (`spot_checked`, `officially_verified`); `scripts/promote_tkpi_candidate.py`
refuses to write a food record without a matching approval, and refuses to overwrite an
existing food id. **In Phase 3E, `data/foods.json` was not modified at all** — the
approval ledger ships empty, and the mechanism is proven by tests against temporary
files, not by running it against production data.

### Indonesian compound-dish foundation (Phase 3E — not yet wired in)

`data/dish_templates.json` + `services/dish_matcher.py` add decomposition *hints* for
common Indonesian compound dishes (pecel lele, nasi uduk, martabak telur/manis, nasi
goreng, mie ayam, bakso, soto ayam, gado-gado, ketoprak, nasi padang) — e.g. pecel lele
is hinted as lele goreng + sambal + kol + timun + kemangi. **No dish template carries a
gram amount or a nutrition value of any kind** — `dish_matcher.py` refuses to load a
template file that smuggles one in. The intent is for a future image-based estimator to
detect and match each visible component separately, the same way single foods are
matched today — not to fabricate a fixed recipe.

Some dishes are marked `variable` (`nasi padang` — a rice-plus-any-selection-of-lauk
meal category with no fixed identity beyond the rice) rather than
`fixed_components`. A bare **"martabak"** is marked `ambiguous` on purpose: it does not
say whether the savory `martabak_telur` or the sweet `martabak_manis` is meant, and the
two are nutritionally very different — this template must never be silently resolved to
either one. This foundation is **not connected to `bot.py` or `services/food_vision.py`
in this phase**.

### Data status — read before trusting any number

Every record carries `data_status`:

- `verified` — values were checked against the cited source. Only these are used in
  user-facing nutrition values and meal totals.
- `provisional` — the record exists but its values have **not** been checked against the
  cited source. Kept for reference, never counted. The bot tells the user the data is
  `belum terverifikasi`.

**All 7 records currently shipped are `provisional`.** They were written from recalled
USDA SR Legacy figures and were never fetched or confirmed from USDA or TKPI. In
practice this means the bot currently reports no totals at all — which is the intended,
honest behaviour until the values are verified against an official source (TKPI /
Kemenkes preferred) and promoted to `verified` by hand.

## Setup (Windows)

1. Create and activate a virtual environment:

   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Create your `.env` file:

   ```
   copy .env.example .env
   ```

4. Fill in `.env`:
   - `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `GEMINI_API_KEY` — from [Google AI Studio](https://aistudio.google.com/apikey)

## Run

```
.venv\Scripts\activate
python bot.py
```

The bot uses long polling — keep the terminal open. Press `Ctrl+C` to stop.

## Test it

1. Open your bot in Telegram, send `/start` and `/help`.
2. Send a photo of a meal.
3. The bot replies `🔍 Sedang menganalisis makanan...`, then a detection result:

   ```
   🔍 Makanan terdeteksi:

   🍽 Ayam goreng
   Porsi: ±150 g (1 potong)
   Identifikasi: Sangat yakin
   Estimasi porsi: Sedang

   ⚠️ Porsi hanya perkiraan dari foto.
   ```

   Two buttons appear under the result: **✅ Konfirmasi** and **✏️ Koreksi porsi**.
   Both currently just acknowledge - the editing flow comes later.

4. Send a photo with no food in it — the bot should say it could not identify
   the food clearly.

## Project structure

```
bot.py                    Telegram handlers and entrypoint
config.py                 Environment configuration
services/food_vision.py          Gemini image analysis (no Telegram code)
services/food_matcher.py         Name -> food record lookup
services/nutrition_calculator.py Deterministic per-100 g scaling
services/usda_food_data.py       USDA FoodData Central fallback
services/dish_matcher.py         Compound-dish decomposition hints (Phase 3E, not wired in)
data/foods.json                  Curated nutrition database (sourced values only)
data/tkpi_2020_candidates.json   TKPI extraction candidates - NOT production data (Phase 3E)
data/tkpi_approved.json          Explicit approval ledger for TKPI promotion (ships empty)
data/dish_templates.json         Indonesian compound-dish decomposition hints (Phase 3E)
scripts/tkpi_extraction.py       Candidate data model + validation
scripts/extract_tkpi_2020.py     Runs the pilot extraction -> data/tkpi_2020_candidates.json
scripts/audit_tkpi_candidates.py Structural audit (duplicates, ranges, high-risk screen)
scripts/tkpi_promotion.py        Explicit approval ledger (never touches foods.json)
scripts/promote_tkpi_candidate.py Applies one approved candidate to a foods.json-shaped file
reports/phase_3e_tkpi_audit.md   Independent PDF audit findings (Phase 3E)
tests/                           Unit tests
requirements.txt          Dependencies
.env.example              Template for .env (never commit .env)
```

## Commands

| Command  | Description                |
|----------|----------------------------|
| `/start` | Welcome message            |
| `/help`  | List of available commands |

## Configuration

| Variable             | Required | Default             |
|----------------------|----------|---------------------|
| `TELEGRAM_BOT_TOKEN` | yes      | —                   |
| `GEMINI_API_KEY`     | yes      | —                   |
| `GEMINI_MODEL`       | no       | `gemini-3.6-flash`  |
| `USDA_API_KEY`       | no       | — (fallback off)    |
| `USDA_TIMEOUT_SECONDS` | no     | `10`                |
| `LOG_LEVEL`          | no       | `INFO`              |

## Troubleshooting

- **`Missing required environment variable(s)`** — `.env` is missing or incomplete.
- **`404 ... model is no longer available`** — your API key cannot access
  `GEMINI_MODEL`. Set a different model in `.env`, e.g. `GEMINI_MODEL=gemini-3.6-flash`.
- **`Gemini API request failed`** in the logs — invalid key, quota exhausted, or a
  network problem. The user only ever sees a generic message; details stay in the logs.
- Set `LOG_LEVEL=DEBUG` in `.env` for more verbose logs.

## Tests

```
.venv\Scriptsctivate
python -m unittest discover -s tests
```

## Adding a food

Append a record to `data/foods.json` with values per 100 g of edible portion, fill in
`source` / `source_reference` with the reference you took them from, and set
`data_status`. Use `verified` only when you have personally checked each value against
that source; otherwise use `provisional`. Never add a record with estimated values — an
absent food is deliberately reported to the user as `belum tersedia`.
