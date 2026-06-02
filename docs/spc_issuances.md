# SPC Convective Outlook Issuance Times & Archive Structure

Reference guide for SPC product availability, issuance schedules, and historical archive access. **Last updated: 2026-06-02.**

## Archive Access

- **Base URL:** `https://www.spc.noaa.gov/products/outlook/archive/YYYY/`
- **Format:** GIF only (even post-March 2026 when live products became PNG)
- **Coverage floor:** 2004-01-01 (2003 returns 403 despite SPC's stated start of Jan 23, 2003)
- **File naming:** `day{N}{product}_{YYYYMMDD}_{HHMM}.gif`

Example: `day1otlk_20240601_1630.gif` = Day 1 categorical outlook issued at 16:30z on June 1, 2024

---

## Canonical Issuance Times (UTC)

### Day 1 Outlooks

| Issuance | HHMM | Frequency | Notes |
|----------|------|-----------|-------|
| Morning | `1300z` | Daily | Regular |
| Afternoon | `1630z` | Daily | Regular |
| Evening | `2000z` | Daily | Regular |
| Late night | `0100z` | Daily | **Filed under NEXT calendar date.** E.g., 0100z on Jun 2 covers the Jun 2 convective day, not Jun 1. There is no "Jun 1 late-night" — 2000z is the final Jun 1 issuance. |
| **Special: Active days** | `1800z`, `0600z` (rare) | **Active weather only** | Occasional bonus issuances during significant severe weather outbreaks. HEAD sweep required to discover. |

**Default (if time not specified):** `2000z` (latest)

### Day 2 Outlooks

| Issuance | HHMM | Frequency | Notes |
|----------|------|-----------|-------|
| Morning | `0600z` | Most days | May be absent on low-activity days |
| Afternoon | `1730z` | Daily | Nearly always present |

**Default:** `1730z`

### Day 3 Outlooks

| Issuance | HHMM | Frequency | Notes |
|----------|------|-----------|-------|
| Morning | `0730z` | Daily | Only one issuance per day |

**Default:** `0730z`

---

## Products by Day

### Day 1 Products

- **Categorical (`day1otlk_...`):** Base risk map (TSTM, SVR, EXTSV, HIGH, CRIT). Always issued.
- **Tornado (`day1probotlk_..._torn.gif`):** Tornado probability contours. Co-issued with categorical.
- **Wind (`day1probotlk_..._wind.gif`):** Severe wind probability. Co-issued with categorical.
- **Hail (`day1probotlk_..._hail.gif`):** Hail probability. Co-issued with categorical.

All four are issued at the **same 1300z, 1630z, 2000z times**.

### Day 2 Products

- **Categorical (`day2otlk_...`):** Base risk map.
- **Tornado (`day2probotlk_..._torn.gif`):** Co-issued.
- **Wind (`day2probotlk_..._wind.gif`):** Co-issued.
- **Hail (`day2probotlk_..._hail.gif`):** Co-issued.

All four issued at **0600z and 1730z only**. (Day 2 has no 2000z issuance.)

### Day 3 Products

- **Categorical (`day3otlk_...`):** Base risk map.
- **Probabilistic (`day3prob_...`):** Single aggregate "any severe" probability surface.

**No tornado/wind/hail breakdown** — Day 3 has categorical + one aggregate prob product only.

**Probabilistic floor:** `day3prob_*.gif` returns 403 before ~2009-01-01. Silently skip if missing.

**Issuance:** `0730z` only, one per day.

### Day 4-8 Products

- No publicly accessible historical archive. All `/products/exper/day4-8/archive/YYYY/` files return 403.
- Rare product in practice; not worth implementing for historical queries.

---

## Filename Structure

```
{PRODUCT_BASE}_{YYYYMMDD}_{HHMM}.gif

where PRODUCT_BASE is one of:
  - day1otlk          (Day 1 categorical)
  - day1probotlk      (Day 1 hazard; append _torn, _wind, _hail)
  - day2otlk          (Day 2 categorical)
  - day2probotlk      (Day 2 hazard; append _torn, _wind, _hail)
  - day3otlk          (Day 3 categorical)
  - day3prob          (Day 3 aggregate probability; no suffix variants)
```

**Never append `.png`** — archive is GIF-only.

---

## Important Edge Cases

### 1. Convective Day vs. Calendar Date

The `0100z` Day 1 issuance is filed under the **next calendar date**.

**Example:**
- User asks: `/historical date:2024-06-01 day:1 time:2000`
  - Returns: `day1otlk_20240601_2000.gif` ✓
- User asks: `/historical date:2024-06-01 day:1` (defaults to latest)
  - Returns: `day1otlk_20240601_2000.gif` (the 2000z issuance) ✓
  - Does NOT try `20240602_0100.gif` — that's the "late-night outlook" for June 2's convective day.

### 2. Day 2 Has No 2000z Issuance

Do **not** try `day2otlk_YYYYMMDD_2000.gif`. The two-per-day schedule is `0600z` and `1730z` only.

### 3. Special Issuances on Active Days

During significant severe weather outbreaks, SPC occasionally issues **bonus** outlooks outside the regular schedule:
- **1800z** (rare, between 1630z and 2000z Day 1)
- **0600z Day 1** (midnight outlook, very rare; filed under same calendar date as 2000z the prior evening, confusing)

**Discovery method:** If a user requests a time that doesn't exist (e.g., `/historical date:2024-05-31 day:1 time:1800`), run a **HEAD sweep** across all known times (`0600`, `1300`, `1630`, `1800`, `2000`) to discover which are actually available, then prompt with a select-menu of valid options.

### 4. Day 3 Probabilistic Pre-2009

`day3prob_*.gif` files return 403 before 2009-01-01. The command should silently skip attempting to fetch the prob variant for dates prior to this; categorical-only is acceptable.

### 5. Day 2 0600z Sparse on Low-Activity Days

The morning issuance (`0600z`) is often absent on days with low expected severe weather. Always fall back to `1730z` as the guaranteed-present time.

---

## Validation Rules

| Condition | Action |
|-----------|--------|
| Date < 2004-01-01 | Reject: "Archive starts 2004-01-01" |
| Date >= today | Reject: "Use /spc1, /spc2, /spc3 for current outlooks" |
| Day 1 time in [1300, 1630, 2000, 1800, 0600] | Valid (with [1800, 0600] needing discovery) |
| Day 2 time in [0600, 1730] | Valid only if time is 0600 or 1730 |
| Day 2 time in [1300, 1630, 2000] | Reject: "Day 2 only issued at 0600z and 1730z" |
| Day 3 time not in [0730] | Reject: "Day 3 only issued at 0730z" |
| Day 3 product in [tornado, wind, hail] | Reject: "Day 3 only has categorical and probabilistic products" |
| Requested time not found on discovery sweep | Prompt: "That time isn't available. Choose from: 1300z, 1630z, 2000z" (with select-menu) |

---

## IEM API Cross-Reference

The Iowa Environmental Mesonet provides a REST API for SPC outlooks:

```
GET https://mesonet.agron.iastate.edu/api/1/nws/spc_outlook.json
  ?day=1
  &valid=2024-06-01
  &cycle=13    # 13=1300z, 16=1630z, 20=2000z, 6=0600z, 7=0730z, 17=1730z
  &outlook_type=C  # C=categorical, T=tornado, W=wind, H=hail
```

Returns polygon records with `issue` timestamp, confirming the issuance existed. Useful for:
- Validating that an issuance actually happened before attempting to fetch the image.
- Discovering bonus issuances (1800z, 0600z) that are not in the canonical list.
- Future: retrieving text product URLs (SWODY1 etc.) for the same issuance.

**Note:** The IEM API uses `cycle` (integer hour), not HHMM strings. Mapping:
- `6` → `0600z`
- `7` → `0730z`
- `13` → `1300z`
- `16` → `1630z`
- `17` → `1730z`
- `18` → `1800z` (if available)
- `20` → `2000z`

---

## Timeline of Changes

| Date | Change |
|------|--------|
| 2004-01-01 | Archive coverage begins |
| ~2009-01-01 | Day 3 probabilistic (`day3prob_*.gif`) introduced |
| 2026-03-03 | Live SPC products shifted from GIF to PNG ("CIG format") — **archive still GIF-only** |

---

## Implementation Notes for Future Features

If extending beyond Day 1/2/3 images:

1. **Text products:** SPC text outlooks (SWODY1, SWODY2, SWODY3) are archived separately via IEM `iem_nwstext_url`. Use the IEM API cycle lookup and fetch from `https://mesonet.agron.iastate.edu/json/raob.py?...` or similar.

2. **Verification/STAT files:** SPC maintains STAT (verification) files at `/climo/stat/`. These are structured as multi-line text summaries of threat areas on issued dates.

3. **Fire weather outlooks:** `/products/fire/` has a separate, older archive structure; not currently needed.

4. **Watches & Mesoscale:** Already handled by existing `/md` and watch-fetch code; archive access via IEM if needed.

---

## References

- SPC main products page: https://www.spc.noaa.gov/products/
- SPC outlook archive: https://www.spc.noaa.gov/products/outlook/archive/
- IEM SPC API docs: https://mesonet.agron.iastate.edu/json/raob.py (search "SPC outlook")
- SPC archive page (archive index): https://www.spc.noaa.gov/archive/
