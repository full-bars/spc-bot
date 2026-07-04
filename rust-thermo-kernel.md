# Rust Thermodynamic Kernel — Implementation Brief

## Goal

Replace SounderPy/MetPy's `sounding_params(clean_data).calc()` call with a Rust-native thermodynamic kernel that runs directly on the event loop without a process pool. P1 (#566) already moved this into the sounding process pool, so the current bottleneck is latency (process-pool round-trip), not event-loop blocking.

The kernel computes from raw numeric arrays: **CAPE** (SB/MU/ML), **CIN**, **LCL**, **SRH** (0-500m, 0-1km, 0-3km), and **bulk shear** (0-500m, 0-1km, 0-3km, 0-6km). The Python caller is `get_sounding_params_text()` in `cogs/sounding_utils.py:178`.

## Current state

**Branch:** `feat/rust-thermo-kernel`  
**File:** `src_rust/thermo.rs` (254 lines, first draft)  
**Status:** Functions written but NOT verified against MetPy. Compiles under `cargo check` but not yet registered in `lib.rs` and not yet wired to Python. Needs physics verification before integration.

## What needs Claude/Fable 5 to verify

### 1. Physics verification (CRITICAL — highest priority)

The draft implements these algorithms from first principles. Need Claude to:

- Trace through **SounderPy's `sounding_params.calc()`** and **MetPy's `cape_cin()`** to extract the exact formulas
- Compare against `src_rust/thermo.rs` and report discrepancies
- Specifically verify:
  - **CAPE/CIN**: Is the parcel lifting (dry adiabatic → moist adiabatic transition at LCL) correct? Is the buoyancy integration `∫ g*(Tp - Te)/Te dz` correct? Does MetPy use virtual temperature correction the same way?
  - **LCL**: Is the Romps 2017 formula correct vs what SounderPy uses?
  - **SRH**: Is `∫ (u - u_storm)*(dv/dz) - (v - v_storm)*(du/dz) dz` correct? Does SounderPy use a different Bunkers motion calculation?
  - **Bulk shear**: Is simple top-minus-bottom wind difference correct, or does MetPy interpolate/density-weight?

### 2. Data format extraction

Current Python code in `_compute_params_text` (sounding_utils.py:178) receives `clean_data` — a dict from SounderPy's `get_obs_data()`. Need Claude to:

- Trace `spy.sounding_params.__init__` and `calc()` to document exactly what keys `clean_data` provides and their units
- Determine the correct mapping from `clean_data` keys to the numeric arrays the Rust function needs:
  - `p_hpa: Vec<f64>` — pressure in hPa (which key?)
  - `t_c: Vec<f64>` — temperature in °C (which key?)
  - `td_c: Vec<f64>` — dewpoint in °C (which key?)
  - `u_ms: Vec<f64>` — u-wind component in m/s (which key?)
  - `v_ms: Vec<f64>` — v-wind component in m/s (which key?)
  - `z_m: Vec<f64>` — height/altitude in meters (which key?)
- Are values already sorted by decreasing pressure (surface → top)?
- Are there gaps (NaN/missing values)? How does SounderPy handle them?

### 3. Test fixtures

A Python script that:
1. Downloads a real sounding for a known station/time (e.g. KILX 2026-07-01 00Z)
2. Runs it through SounderPy's `sounding_params().calc()` 
3. Extracts both:
   - The raw numeric arrays (pressure, temp, dewpoint, winds, height)
   - The computed parameters (CAPE, CIN, LCL, SRH, shear)
4. Writes them to a JSON fixture file for Rust unit tests

This gives us a ground-truth comparison dataset.

## Remaining todo (for deepseek after Claude verifies physics)

1. Register `compute_thermo_params` in `src_rust/lib.rs`  
2. Add the `thermo` module to `lib.rs`  
3. Update `_compute_params_text` in `sounding_utils.py` to call the Rust function when available, falling back to SounderPy
4. Rebuild the `.so` via `maturin develop`
5. Add Rust unit tests using the fixture Claude creates
6. Verify parity against SounderPy on multiple real soundings

## Key files to read

- `src_rust/thermo.rs` — the Rust kernel (already written, needs review)
- `src_rust/lib.rs` — module registry (needs `mod thermo;` + function registration)
- `cogs/sounding_utils.py:178-240` — Python caller that would use the Rust kernel
- `cogs/sounding_utils.py:96-168` — `_compute_params_text` worker function (target for replacement)
- SounderPy: `sounding_params.__init__`, `sounding_params.calc()` in site-packages
- MetPy: `metpy.calc.cape_cin`, `metpy.calc.storm_relative_helicity`, `metpy.calc.bulk_shear`

## CLAUDE.md note

Do NOT mention Claude, AI, or this file in any commit message, PR description, or CHANGELOG. This file is for internal coordination only (like progress.md).

## Fable 5 Physics Review Findings

Reviewed 2026-07-03 against the *installed* references in `venv/lib/python3.12/site-packages/`:
SounderPy v3.1.0 (`sounderpy/calc.py`) and MetPy (`metpy/calc/{thermo,kinematics,indices}.py`).
Key fact up front: **SounderPy does NOT use MetPy for CAPE/CIN/SRH/shear — it uses its vendored
SHARPpy** (`sounderpy/SHARPPYMAIN/sharppy/sharptab/{params,winds,thermo,interp}.py`). MetPy is only
used for the MU/ML parcel *selection* and as a Bunkers fallback. Parity targets are therefore the
SHARPpy routines, not `metpy.calc.cape_cin`.

**Verdict: NOT safe to integrate as-is.** The parcel-lifting core is structurally broken (wrong
iteration direction, wrong virtual-temperature usage), SRH uses a hardcoded placeholder storm motion
with a sign-flipped integrand, and both SRH/shear confuse MSL and AGL heights. One isolated bug
(inverted LCL Poisson ratio) was patched; everything else is documented below for the rewrite.

### Patch applied by this review

- `src_rust/thermo.rs` `lcl_pressure()` (formerly lines 41–48): the Poisson ratio was inverted —
  `p * (T/T_lcl)^(cp/Rd)` instead of `p * (T_lcl/T)^(cp/Rd)`. As written it returned an LCL *above*
  the surface pressure (1000 hPa, 25/15 °C → 1158 hPa; correct is ~863 hPa; MetPy gives 862.87 hPa).
  Numerically verified post-fix: 863.25 hPa. Also removed the dead `a` variable and corrected the
  comment: the formula is **Bolton (1980) eq. 22**, not Romps (2017). Romps' closed form uses the
  Lambert-W function (`W_{-1}`) — nothing in this file resembles it. Bolton agrees with SHARPpy's
  `lcltemp`+`thalvl` (`thermo.py:20-86`) to well under 1 hPa, so keeping Bolton is fine.
  Compile-checked via a temporary `mod thermo;` (then reverted; `lib.rs` untouched, as required).

### 1. Physics verification — discrepancies (thermo.rs line numbers = original draft)

**CAPE/CIN — parcel lifting (`lift_parcel`, lines 55–132): broken, needs rewrite**

- **Dry segment iterates the wrong way (line 76):** `for i in (0..=p_start_idx).rev()` walks from
  the start level *downward in index*. Profile is surface→top (index 0 = surface, confirmed below),
  so for a surface parcel the loop visits only index 0 and the dry-adiabatic leg is never computed.
  Must iterate `p_start_idx..n` (upward / decreasing pressure).
- **Parcel virtual temperature is never computed below the LCL (lines 88–94):** `tvp_k[i]` is filled
  with the *environment* virtual temperature. `cape_cin()` then reads `tv_parcel_k` as the parcel Tv,
  so sub-LCL buoyancy is identically ~0. Correct: parcel Tv below the LCL uses the conserved parcel
  mixing ratio `w0 = mixratio(es(Td_start), p_start)` (MetPy `thermo.py:2833-2841`; SHARPpy does the
  equivalent via `virtemp(p, theta_parcel-derived T, temp_at_mixrat(blmr, p))`, `params.py:1840-1852`).
- **Moist segment integrates in the wrong direction from the wrong anchor (lines 102–128):**
  `for i in (0..n).rev()` starts at the *top of the profile*, seeds `tp = T_LCL` there, and steps
  `tp -= dt_dp * dp` downward. The moist adiabat must be integrated *upward from the LCL*
  (anchor at (p_lcl, T_lcl)). The pseudoadiabatic dT/dp expression itself (lines 120–122,
  `(Rd·T + Lv·ws) / (cp + Lv²·ws·ε/(Rd·T²))` per unit p) matches the standard form MetPy integrates
  (`moist_lapse`) — only direction/anchor are wrong. Also: single Euler step per raw level; raw
  spacing near the top is 25–50+ hPa → visible drift. SHARPpy instead uses the Wobus-function
  `wetlift` (`thermo.py:346`); MetPy solves the ODE on the actual grid with an ODE integrator.
  Recommend sub-stepping (≤5 hPa) RK2/RK4 if not implementing Wobus.
- **No LCL level is inserted:** both references evaluate the parcel *at* the LCL and at layer
  crossings; the Rust version skips from the last sub-LCL level to the first super-LCL level.

**CAPE/CIN — integration (`cape_cin`, lines 138–197)**

- **Environment moisture is wrong (lines 173–174):** env Tv uses `mixratio(es(T_env), p)` — the
  *saturation* mixing ratio at the env temperature, i.e. it assumes 100 % RH everywhere. Must use the
  env **dewpoint** (`es(Td_env)`), which currently isn't even passed in. Effect: env Tv biased high
  in dry air → CAPE biased low, CIN biased deep. (MetPy: `virtual_temperature_from_dewpoint(p,T,Td)`,
  `thermo.py:2839`; SHARPpy `interp.vtmp` likewise uses dwpc.)
- **Rectangle vs trapezoid + no zero-crossing interpolation:** SHARPpy accumulates
  `G·(tdef1+tdef2)/2·(h2−h1)` per layer (`params.py:1848-1857`) and interpolates the LFC/EL
  crossings; MetPy integrates `Rd·∫(Tv_p−Tv_e) d ln p` between interpolated crossings
  (`thermo.py:2860-2884`). The Rust one-sided rectangle without crossing interpolation gives
  tens-of-J/kg errors on coarse profiles.
- **No EL cap on CAPE / CIN window differences:** Rust sums *every* positive layer to profile top;
  SHARPpy truncates `bplus` at the EL and only counts CIN for `pe2 > 500 hPa`; MetPy counts CIN
  strictly surface→LFC. Rust's `in_cape_region` also never resets.
- **Sign convention:** Rust returns CIN ≥ 0; SHARPpy `bminus` (what `sbcin`/`mucin` are today) is
  **negative**. The Python formatter prints values verbatim, so the wired-up kernel must emit
  negative CIN or the bot's output flips sign vs. production.
- **Buoyancy form:** `g·dz·ΔTv/Tv_env` (height coordinate) is equivalent to SHARPpy's form — fine —
  but MetPy's `Rd·ΔTv·d ln p` differs only by hydrostatic assumption; either is acceptable once the
  above bugs are fixed.

**Parcel definitions (`compute_thermo_params`, lines 308–416)**

- **ML parcel (lines 353–380) mismatches SounderPy twice:** SounderPy uses
  `mpcalc.mixed_parcel(..., depth=50 hPa)` (`calc.py:361`) which averages **potential temperature
  and mixing ratio**, then converts back. Rust averages **raw T and Td over 100 hPa**. Raw-T
  averaging biases the mixed parcel warm; wrong depth changes MLCAPE further. (MetPy's own default
  is 100 hPa, but parity target SounderPy explicitly passes 50.)
- **MU parcel (lines 207–238):** 300 hPa search depth matches. But SounderPy ranks by MetPy
  `equivalent_potential_temperature` (exact Bolton eq. 39 via T_LCL), whereas Rust uses an
  approximate θe `T·(1000/p)^{0.2854(1−0.00028·w·1000)}·exp(2675·w/T)` with the computed `tl`
  (line 229) left **unused**. Usually picks the same level; can differ on marginal profiles. Minor.
- **CIN for ML/MU parcels:** SounderPy lifts MU/ML parcels *from their own pressure level* via
  `parcelx(pres=…)`; Rust lifts the ML parcel from `surface_idx` with mixed properties (acceptable)
  and the MU parcel from `mu_idx` (correct).
- **LFC missing:** the file header claims LFC but nothing emits `sb_lfc_p` (the Python text prints
  it — see §3 key gaps).

**SRH (lines 243–275 and 388–400): three independent bugs**

1. **Storm motion placeholder (lines 391–394):** hardcoded "250° at 15 m/s". SounderPy uses SHARPpy
   `bunkers_storm_motion` (`params.py:2407`): pressure-weighted mean wind from the effective-inflow
   base to 65 % of the MU-parcel EL height, ±7.5 m/s perpendicular to that layer's shear vector;
   falls back to `non_parcel_bunkers_motion` (0–6 km AGL mean wind ± 7.5 m/s perpendicular to the
   sfc–6 km shear, `winds.py:247`) when MUCAPE ≤ 100 J/kg, and to `mpcalc.bunkers_storm_motion` if
   masked (`calc.py:472-488`). The Rust kernel needs at minimum the non-parcel Bunkers ID method —
   fixed climatological motion is not acceptable for a tool feeding severe-wx analysis. Note also
   the placeholder's own math is wrong for a "from 250°" wind (met convention u = −spd·sin(dir)):
   as written it points *toward* 250°.
2. **Integrand sign is flipped (line 272):** per layer Rust computes
   `(u₀−us)(v₁−v₀) − (v₀−vs)(u₁−u₀) = u₀′v₁′ − u₁′v₀′`, the **negative** of the reference term
   `sru[1:]·srv[:-1] − sru[:-1]·srv[1:] = u₁′v₀′ − u₀′v₁′` (SHARPpy `winds.py:346`, MetPy
   `kinematics.py:1019-1021`). The `.max(0.0)` clamp (line 274) then masks the flip — and is itself
   wrong: total SRH = phel + nhel and can legitimately be negative.
3. **MSL vs AGL (lines 260–263):** `z_m[i] > h_m` compares MSL heights against AGL layer tops.
   `clean_data['z']` is MSL (KLOT fixture: z[0] = 191.7 m). Must integrate over `z − z[0]`.
   Also no interpolation of u,v to the exact layer top (SHARPpy interpolates at the bounding
   pressures, `winds.py:333-340`).

**Bulk shear (lines 279–304): direction of the fix**

- Same **MSL vs AGL** bug (line 289).
- **No interpolation to the exact layer top:** takes the first level at/above h. SHARPpy
  `wind_shear` (`winds.py:158`) interpolates u,v to the exact pressures of sfc and sfc+h AGL
  (via `interp.components`), MetPy `bulk_shear` → `get_layer(..., interpolate=True)`
  (`indices.py:505-509`). With the 43-level fixture profile this alone is multi-knot error.
  Neither reference density-weights; plain top-minus-bottom vector difference is correct *after*
  interpolation.
- Magnitude-in-knots output matches what the bot prints (SHARPpy `comp2vec(...)[1]`, kts) — but
  only if inputs are m/s; see units trap in §2.

**Output-dict gaps vs the text the bot renders** (`_compute_params_text`,
`cogs/sounding_utils.py:96-159`): the kernel emits none of `sb3cape`, `mu3cape`, `dcape`,
`mu_ecape`, `sb_lfc_p`, `lr_03km`, `lr_36km`, `eil_z`, `eil_stp`, `eil_scp`, and its key names
(`srh_1000m`, `shear_6000m`, …) don't match the ones the formatter reads
(`srh_0_to_1000`, `shear_0_to_6000`, …). As-is, over half the summary fields would print "N/A".
Missing values should be `None`, not rounded i64 zeros, to preserve the formatter's N/A path.

### 2. Data format — `clean_data` → Rust args mapping

From `sounderpy/obs_data.py:130-136` (RAOB), same schema for IGRA (`:193-215`) and BUFKIT
(`bufkit_data.py`). All arrays are pint `Quantity`-wrapped numpy arrays plus `site_info` /
`titles` dicts.

| clean_data key | units | → Rust arg | conversion |
|---|---|---|---|
| `p`  | hPa   | `p_hpa` | `clean_data['p'].m` (as-is) |
| `T`  | degC  | `t_c`   | `.m` (as-is) |
| `Td` | degC  | `td_c`  | `.m` (as-is) |
| `u`  | **knots** | `u_ms` | `.m / 1.94384` ← **trap: knots, not m/s** |
| `v`  | **knots** | `v_ms` | `.m / 1.94384` |
| `z`  | m **MSL** | `z_m`  | `.m`; kernel must use `z − z[0]` for all AGL layer math |

- **Sort order:** surface → top (pressure strictly decreasing). RAOB path drops duplicate-pressure
  rows (`np.diff(p) != 0`, `obs_data.py:134`) and trims everything above 98 hPa (`:149-155`).
  IGRA winds arrive in m/s and are converted to kt in-place (`:214-215`).
- **Heights:** MSL; `site_info['site-elv']` holds station elevation (9999 sentinel → SounderPy falls
  back to `z[0]`, `calc.py:207-210`). Bad monotonicity is repaired by `fix_bad_heights`
  (`obs_data.py:245-276`) before the dict is returned.
- **NaN/missing:** clean_data may still contain NaN (esp. Td, winds aloft). SounderPy filters levels
  where `z` is NaN before interpolating (`calc.py:245-255`); SHARPpy masks −9999 internally; MetPy
  `cape_cin` drops NaN rows (`_remove_nans`). The Rust kernel must tolerate NaN in any array
  (current code partially does, but e.g. NaN at the parcel start level poisons the LCL silently).
- **Sign/type notes for parity:** SHARPpy CIN is negative; masked/`--` values must map to `None`.

### 3. Test fixture — status

- **Script:** `scripts/gen_thermo_fixture.py` (new). Tries the Wyoming RAOB archive for the most
  recent 00Z/12Z cycles, then falls back to the latest PSU HRRR BUFKIT profile (same clean_data
  schema). Dumps raw arrays (with units metadata) + SounderPy/SHARPpy ground truth
  (CAPE/CIN ×3 parcels, 0–3 km CAPE, LCL/LFC, lapse rates, DCAPE, Bunkers `sm_u`/`sm_v`,
  SRH 0–500/1/3/6 km, shear 0–500/1/3/6 km) to `tests/fixtures/`.
- **Generated fixture (real data, on disk):**
  `tests/fixtures/thermo_kernel_LOT_bufkit-hrrr_20260703_07Z.json` — KLOT HRRR F01 valid
  2026-07-03 06Z, 43 levels. Ground truth: SBCAPE 1264.6, MUCAPE 2875.9, MLCAPE 1140.0,
  SBCIN −138.1, SB LCL 927.4 hPa, SRH 0–1 km 185.3 m²/s², shear 0–6 km 36.7 kt,
  Bunkers RM (29.03, 7.77) kt.
- **Caveat:** `weather.uwyo.edu` is unreachable from this sandbox (egress otherwise fine), so no
  *observed* RAOB fixture yet. Re-run `venv/bin/python scripts/gen_thermo_fixture.py ILX` from a
  box that can reach the Wyoming archive to add one; the script needs no changes.

### Recommended fix order for deepseek

1. Rewrite `lift_parcel` (iterate surface→top; parcel Tv with conserved w below LCL; insert LCL
   level; integrate moist adiabat upward from the LCL with sub-stepping).
2. Pass `td_env` into `cape_cin`; env Tv from dewpoint; trapezoid layers; cap CAPE at EL; emit
   negative CIN.
3. Implement non-parcel Bunkers motion (0–6 km AGL mean wind ± 7.5 m/s perpendicular to sfc–6 km
   shear); fix SRH integrand sign; drop the `.max(0)` clamp; AGL heights; interpolate layer tops.
4. Bulk shear: AGL + interpolate both bounds.
5. ML parcel: 50 hPa depth, average θ and mixing ratio.
6. Align output keys with `_compute_params_text` (or have the Python side map them) and use `None`
   for missing values; then validate against the JSON fixture (tolerances: CAPE ±5 %, SRH/shear
   ±5 % once storm motion matches).
