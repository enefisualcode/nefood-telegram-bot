# Nutrition Telegram Bot

A Telegram bot that recognises the foods in a photo using the Google Gemini API.

**Current phase: 3D — verified Indonesian (TKPI) food dataset foundation.** The bot detects visible
foods, estimates portions, and — after the user confirms — calculates nutrition from a
local curated database.

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
data/foods.json                  Curated nutrition database (sourced values only)
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
