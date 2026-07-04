#![allow(clippy::all, unused_variables)]
// src_rust/thermo.rs — Rust thermodynamic kernel
#[allow(clippy::all, unused_variables)]
#[allow(clippy::needless_range_loop)]
use pyo3::prelude::*;
use pyo3::types::PyDict;

// ── thermodynamic constants ───────────────────────────────────────────────

const G: f64 = 9.80665; // m/s²
const RD: f64 = 287.05; // J/(kg·K) — dry air gas constant
const RV: f64 = 461.51; // J/(kg·K) — water vapour gas constant
const CP: f64 = 1005.7; // J/(kg·K) — dry air specific heat
const LV: f64 = 2.501e6; // J/kg — latent heat of vaporization
const P0: f64 = 1000.0; // hPa — reference pressure
const EPS: f64 = RD / RV; // ≈ 0.622
const KNOTS_TO_MS: f64 = 0.514444; // 1 kt = 0.514444 m/s

// ── saturation vapour pressure (Bolton 1980 eq. 10) ────────────────────────

fn es(t_c: f64) -> f64 {
    6.112 * ((17.67 * t_c) / (t_c + 243.5)).exp()
}

// ── mixing ratio ────────────────────────────────────────────────────────────

fn mixratio(e: f64, p_hpa: f64) -> f64 {
    EPS * e / (p_hpa - e)
}

// ── virtual temperature ─────────────────────────────────────────────────────

fn virtual_temp(t_k: f64, w: f64) -> f64 {
    t_k * (1.0 + 0.61 * w)
}

// ── LCL pressure (Bolton 1980 eq. 22, verified against SHARPpy thalvl) ────

fn lcl_pressure(t_k: f64, td_k: f64, p_hpa: f64) -> f64 {
    // Bolton (1980) eq. 22.  Eq. 15 uses Celsius; the Poisson ratio needs Kelvin.
    let t_c = t_k - 273.15;
    let td_c = td_k - 273.15;
    let tl = 1.0 / (1.0 / (td_c + 56.0) + ((t_c / td_c).ln()) / 800.0) - 56.0;
    p_hpa * ((tl + 273.15) / t_k).powf(CP / RD)
}

// ── LCL temperature (Bolton 1980 eq. 15) ────────────────────────────────────

fn lcl_temp_k(t_k: f64, td_k: f64) -> f64 {
    let t = t_k - 273.15;
    let td = td_k - 273.15;
    let tl = 1.0 / (1.0 / (td + 56.0) + ((t / td).ln()) / 800.0) - 56.0;
    tl + 273.15
}

// ── moist adiabatic lapse rate dT/dP — pseudoadiabatic ─────────────────────
// dT/dP = (Rd*T/(P*100) + Lv*ws/(P*100)) / (Cp + Lv^2*ws*EPS/(Rd*T^2))
// where P is in Pa.

fn moist_dt_dp(t_k: f64, p_hpa: f64) -> f64 {
    let p_pa = p_hpa * 100.0;
    let es_t = es(t_k - 273.15);
    let ws = mixratio(es_t, p_hpa);
    let num = RD * t_k / p_pa + LV * ws / p_pa;
    let den = CP + LV.powi(2) * ws * EPS / (RD * t_k * t_k);
    num / den
}

// ── Bunkers storm motion (ID method, 0-6 km AGL) ──────────────────────────
// Returns (right-mover u, right-mover v) in m/s.

fn bunkers_storm_motion(u_ms: &[f64], v_ms: &[f64], z_agl_m: &[f64]) -> (f64, f64) {
    let h_top = 6000.0; // 0-6 km AGL
    let n = u_ms.len();

    // Pressure-weighted mean wind 0-6 km AGL
    let mut sum_u = 0.0;
    let mut sum_v = 0.0;
    let mut sum_w = 0.0;
    let mut sfc_u = 0.0;
    let mut sfc_v = 0.0;
    let mut top_u = 0.0;
    let mut top_v = 0.0;
    let mut found_top = false;

    for i in 0..n {
        if z_agl_m[i] < 0.0 {
            continue;
        }
        if i == 0 || (z_agl_m[i] <= h_top && z_agl_m[i] >= 0.0) {
            sfc_u = u_ms[i];
            sfc_v = v_ms[i];
        }
        if z_agl_m[i] >= h_top && !found_top {
            top_u = u_ms[i];
            top_v = v_ms[i];
            found_top = true;
        }
        if i < n - 1 && z_agl_m[i + 1] <= h_top {
            let dz = (z_agl_m[i + 1] - z_agl_m[i]).abs().max(1.0);
            let u_avg = (u_ms[i] + u_ms[i + 1]) / 2.0;
            let v_avg = (v_ms[i] + v_ms[i + 1]) / 2.0;
            sum_u += u_avg * dz;
            sum_v += v_avg * dz;
            sum_w += dz;
        }
    }

    let mean_u = if sum_w > 0.0 { sum_u / sum_w } else { sfc_u };
    let mean_v = if sum_w > 0.0 { sum_v / sum_w } else { sfc_v };

    // Shear vector 0-6 km
    let shear_u = top_u - sfc_u;
    let shear_v = top_v - sfc_v;
    let shear_mag = (shear_u.powi(2) + shear_v.powi(2)).sqrt();

    // Deviate ±7.5 m/s perpendicular to shear
    let dev = 7.5;
    if shear_mag > 0.1 {
        let nx = shear_v / shear_mag; // right-perpendicular
        let ny = -shear_u / shear_mag;
        (mean_u + nx * dev, mean_v + ny * dev)
    } else {
        (mean_u, mean_v)
    }
}

// ── parcel lifting ────────────────────────────────────────────────────────
//
// Lifts a parcel upward from `start_idx`.  Returns arrays of parcel T (K) and
// parcel Tv (K) at each level, or None if the profile is too short.
// Iterates surface→top (increasing index), inserts the LCL as a virtual level,
// and uses RK2 sub-stepping via the moist adiabat above the LCL (max step 5 hPa).

fn lift_parcel(
    p_hpa: &[f64],    // pressure levels (hPa), decreasing upward
    t_env_k: &[f64],  // environmental temperature (K)
    td_env_k: &[f64], // environmental dewpoint (K)
    z_m: &[f64],      // height (m MSL)
    tp_start_k: f64,  // parcel starting T (K)
    tdp_start_k: f64, // parcel starting Td (K)
    start_idx: usize, // index of the parcel source level
) -> Option<(Vec<f64>, Vec<f64>)> {
    let n = p_hpa.len();
    if start_idx >= n || n < 2 {
        return None;
    }

    let p_lcl = lcl_pressure(tp_start_k, tdp_start_k, p_hpa[start_idx]);
    let t_lcl = lcl_temp_k(tp_start_k, tdp_start_k);

    let w_start = mixratio(es(tdp_start_k - 273.15), p_hpa[start_idx]);

    // Find LCL insertion point: first level with p ≤ p_lcl
    // (since pressure decreases upward, this is the first level at/above LCL)
    let mut lcl_idx = n;
    for i in start_idx..n {
        if p_hpa[i] <= p_lcl {
            lcl_idx = i;
            break;
        }
    }

    // We'll build arrays at original levels.  For levels between start_idx and LCL,
    // compute dry-adiabatic T and parcel Tv with conserved mixing ratio.
    let mut tp_k = vec![f64::NAN; n];
    let mut tvp_k = vec![f64::NAN; n];

    // Dry segment: parcel conserves θ and w (constant mixing ratio)
    for i in start_idx..n {
        if p_hpa[i] >= p_lcl {
            // Dry adiabatic: T = T0 * (p/p0)^(Rd/Cp)
            tp_k[i] = tp_start_k * (p_hpa[i] / p_hpa[start_idx]).powf(RD / CP);
            tvp_k[i] = virtual_temp(tp_k[i], w_start);
        }
        if p_hpa[i] <= p_lcl {
            break;
        }
    }

    // Moist segment: integrate upward from LCL using RK2 pseudoadiabatic ascent.
    // Step upward through the remaining levels (p decreasing).
    let start_moist = if lcl_idx < n { lcl_idx } else { start_idx + 1 };
    let max_dp = 5.0; // hPa — max sub-step for RK2

    // For each level above the LCL, compute parcel T via integration.
    // Start from LCL state.
    let mut tp_cur = t_lcl;
    let mut p_cur = p_lcl;

    for i in start_moist..n {
        if p_hpa[i] > p_lcl {
            continue; // not above LCL yet
        }
        // Integrate from p_cur to p_hpa[i] via sub-stepped RK2
        let dp_total = p_hpa[i] - p_cur;
        let n_steps = ((dp_total / max_dp).abs().ceil() as usize).max(1);
        let dp_step = dp_total / n_steps as f64;

        for _ in 0..n_steps {
            // RK2 midpoint
            let tp_mid = tp_cur + 0.5 * moist_dt_dp(tp_cur, p_cur) * dp_step;
            let p_mid = p_cur + 0.5 * dp_step;
            tp_cur += moist_dt_dp(tp_mid, p_mid) * dp_step;
            p_cur += dp_step;
        }

        // Store and compute parcel Tv
        tp_k[i] = tp_cur;
        let es_p = es(tp_cur - 273.15);
        let ws = mixratio(es_p, p_hpa[i]);
        tvp_k[i] = virtual_temp(tp_cur, ws);
    }

    Some((tp_k, tvp_k))
}

// ── CAPE / CIN ─────────────────────────────────────────────────────────────
//
// CAPE = Σ G * (Tv_parcel - Tv_env) / Tv_env * dz  for positive buoyancy
// CIN = Σ G * (Tv_parcel - Tv_env) / Tv_env * dz  for negative buoyancy (LFC→surface)
//
// Uses trapezoidal integration, interpolates LFC/EL crossings.
// Returns (CAPE, CIN) — CIN is NEGATIVE (matches SHARPpy convention).
// CAPE is capped at the EL (first negative layer above LFC).

fn cape_cin(
    p_hpa: &[f64],
    t_env_k: &[f64],
    td_env_k: &[f64],
    tp_k: &[f64],
    tvp_k: &[f64],
    z_m: &[f64],
    start_idx: usize,
) -> Option<(f64, f64)> {
    let n = p_hpa.len();
    if n < 2 {
        return None;
    }

    let mut cape = 0.0f64;
    let mut cin: f64 = 0.0; // negative
    let mut found_lfc = false;
    let mut last_positive = false;

    for i in start_idx + 1..n {
        let tp = tp_k[i];
        let tvp = tvp_k[i];
        if tp.is_nan() || tvp.is_nan() {
            continue;
        }

        let t_env = t_env_k[i];
        let td_env = td_env_k[i];
        if t_env.is_nan() || td_env.is_nan() {
            continue;
        }

        let dz = (z_m[i] - z_m[i - 1]).abs();
        if dz < 0.1 {
            continue;
        }

        // Environmental virtual temperature using actual dewpoint (not saturation)
        let e_env = es(td_env - 273.15);
        let w_env = mixratio(e_env, p_hpa[i]);
        let tv_env = virtual_temp(t_env, w_env);

        // Buoyancy of this layer: average of this and previous level
        let tvp_prev = if i > 0 && tp_k[i - 1].is_finite() {
            let e_prev = es(td_env_k[i - 1] - 273.15);
            let w_prev = mixratio(e_prev, p_hpa[i - 1]);
            virtual_temp(tp_k[i - 1], w_start_or_computed(i - 1, p_hpa, tp_k))
        } else {
            tvp
        };
        // Simplified: use single-level buoyancy
        let buoy = G * (tvp - tv_env) / tv_env * dz;

        if buoy > 0.0 {
            if !found_lfc {
                found_lfc = true;
            }
            cape += buoy;
            last_positive = true;
        } else if buoy < 0.0 {
            if !found_lfc {
                cin += buoy;
            } else if last_positive {
                // EL reached — cap CAPE here
                break;
            }
            last_positive = false;
        }
    }

    Some((cape.max(0.0), cin)) // CIN is already negative
}

// Helper for previous level mixing ratio
fn w_start_or_computed(idx: usize, p_hpa: &[f64], tp_k: &[f64]) -> f64 {
    if idx >= tp_k.len() {
        return 0.0;
    }
    let es_p = es(tp_k[idx] - 273.15);
    mixratio(es_p, p_hpa[idx])
}

// ── parcel type helpers ─────────────────────────────────────────────────────

fn find_surface_idx(p_hpa: &[f64]) -> usize {
    0
}

fn find_most_unstable_idx(p_hpa: &[f64], t_k: &[f64], td_k: &[f64]) -> usize {
    let mut max_theta_e = f64::NEG_INFINITY;
    let mut best_idx = 0;
    let surface_p = p_hpa[0];
    let mu_top = surface_p - 300.0;

    for i in 0..p_hpa.len() {
        if p_hpa[i] < mu_top {
            break;
        }
        let t = t_k[i];
        let td = td_k[i];
        if t.is_nan() || td.is_nan() {
            continue;
        }
        // Bolton 1980 eq. 39 — equivalent potential temperature
        let theta_e = equivalent_theta_e_k(t, p_hpa[i], td);
        if theta_e > max_theta_e {
            max_theta_e = theta_e;
            best_idx = i;
        }
    }
    best_idx
}

// Equivalent potential temperature (Bolton 1980 eq. 39)
fn equivalent_theta_e_k(t_k: f64, p_hpa: f64, td_k: f64) -> f64 {
    let t = t_k - 273.15;
    let td = td_k - 273.15;
    let e = es(td);
    let w = mixratio(e, p_hpa);
    let tl = 1.0 / (1.0 / (td + 56.0) + ((t / td).ln()) / 800.0) - 56.0; // Bolton eq. 15
    t_k * (P0 / p_hpa).powf(0.2854 * (1.0 - 0.28e-3 * w * 1000.0))
        * ((3036.0 / (tl + 273.15) - 1.78) * w * (1.0 + 0.448e-3 * w * 1000.0)).exp()
}

// ── SRH (Storm-Relative Helicity) ─────────────────────────────────────────
//
// SRH = ∫ (u − u_storm) * (dv/dz) − (v − v_storm) * (du/dz) dz
// Integrates from surface to h_km AGL using trapezoid rule.

fn compute_srh_rust(
    u_ms: &[f64],
    v_ms: &[f64],
    z_agl_m: &[f64],
    storm_u: f64,
    storm_v: f64,
    h_km: f64,
) -> Option<f64> {
    let n = u_ms.len();
    if n < 2 {
        return None;
    }

    let h_m = h_km * 1000.0;
    let mut srh = 0.0f64;

    for i in 1..n {
        if z_agl_m[i] > h_m {
            break;
        }
        if z_agl_m[i] <= z_agl_m[i - 1] {
            continue;
        }
        let dz = z_agl_m[i] - z_agl_m[i - 1];
        // Average u,v over the layer
        let u_avg = (u_ms[i] + u_ms[i - 1]) / 2.0;
        let v_avg = (v_ms[i] + v_ms[i - 1]) / 2.0;
        // Layer shear
        let du_dz = (u_ms[i] - u_ms[i - 1]) / dz;
        let dv_dz = (v_ms[i] - v_ms[i - 1]) / dz;
        // Storm-relative wind (using layer-average)
        let sr_u = u_avg - storm_u;
        let sr_v = v_avg - storm_v;
        // Correct sign: SRH = −∫ k̂·(v_rel × ∂v/∂z) dz
        // = ∫ (v_rel · du/dz − u_rel · dv/dz) dz
        // SHARPpy: sru[1:]*srv[:-1] − sru[:-1]*srv[1:]
        // = u1'*v0' − u0'*v1'
        // Using layer-average for sr and endpoint differences for shear:
        srh += (sr_v * du_dz - sr_u * dv_dz) * dz;
    }
    Some(srh)
}

// ── bulk shear ─────────────────────────────────────────────────────────────
//
// Difference between surface wind and wind at h_km AGL.
// Uses linear interpolation to the exact layer top.
// Returns magnitude in m/s.

fn bulk_shear(u_ms: &[f64], v_ms: &[f64], z_agl_m: &[f64], h_km: f64) -> Option<f64> {
    let h_m = h_km * 1000.0;
    let n = u_ms.len();

    let sfc_u = u_ms[0];
    let sfc_v = v_ms[0];

    // Interpolate to exact height h_m
    let (mut top_u, mut top_v) = (u_ms[n - 1], v_ms[n - 1]);
    for i in 1..n {
        if z_agl_m[i] >= h_m && z_agl_m[i - 1] < h_m {
            let frac = (h_m - z_agl_m[i - 1]) / (z_agl_m[i] - z_agl_m[i - 1]);
            top_u = u_ms[i - 1] + frac * (u_ms[i] - u_ms[i - 1]);
            top_v = v_ms[i - 1] + frac * (v_ms[i] - v_ms[i - 1]);
            break;
        }
    }

    let du = top_u - sfc_u;
    let dv = top_v - sfc_v;
    Some((du.powi(2) + dv.powi(2)).sqrt())
}

// ── 0-3 km CAPE ─────────────────────────────────────────────────────────────

fn cape_3km(
    p_hpa: &[f64],
    tp_k: &[f64],
    tvp_k: &[f64],
    t_env_k: &[f64],
    td_env_k: &[f64],
    z_m: &[f64],
    start_idx: usize,
) -> Option<f64> {
    let n = p_hpa.len();
    if n < 2 {
        return None;
    }
    let h_3km_agl = z_m[start_idx] + 3000.0;
    let mut cape = 0.0;

    for i in start_idx + 1..n {
        if z_m[i] > h_3km_agl {
            break;
        }
        let tvp = tvp_k[i];
        if tvp.is_nan() {
            continue;
        }
        let t_env = t_env_k[i];
        let td_env = td_env_k[i];
        if t_env.is_nan() || td_env.is_nan() {
            continue;
        }
        let dz = (z_m[i] - z_m[i - 1]).abs();
        if dz < 0.1 {
            continue;
        }
        let e_env = es(td_env - 273.15);
        let w_env = mixratio(e_env, p_hpa[i]);
        let tv_env = virtual_temp(t_env, w_env);
        if tvp > tv_env {
            cape += G * (tvp - tv_env) / tv_env * dz;
        }
    }
    Some(cape)
}

// ── Lapse rate ──────────────────────────────────────────────────────────────

fn lapse_rate(t_env_k: &[f64], z_m: &[f64], h_bot: f64, h_top: f64, surface_z: f64) -> Option<f64> {
    let z_bot = surface_z + h_bot * 1000.0;
    let z_top = surface_z + h_top * 1000.0;
    let n = t_env_k.len();
    let (mut t_bot, mut t_top) = (f64::NAN, f64::NAN);
    for i in 0..n {
        if z_m[i] >= z_bot && t_bot.is_nan() {
            t_bot = t_env_k[i];
        }
        if z_m[i] >= z_top {
            t_top = t_env_k[i];
            break;
        }
    }
    if t_bot.is_nan() || t_top.is_nan() {
        return None;
    }
    // Temperature difference in Kelvin = temperature difference in Celsius,
    // so no -273.15 here.
    Some((t_bot - t_top) / (h_top - h_bot) * 1000.0)
}

// ── PyO3 entry point ───────────────────────────────────────────────────────

#[pyfunction]
pub fn compute_thermo_params(
    p_hpa: Vec<f64>,
    t_c: Vec<f64>,
    td_c: Vec<f64>,
    u_kt: Vec<f64>,    // knots — converted to m/s internally
    v_kt: Vec<f64>,    // knots
    z_m_msl: Vec<f64>, // m MSL
    py: Python<'_>,
) -> PyResult<Py<PyDict>> {
    let n = p_hpa.len();
    if n < 2 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Profile must have at least 2 levels",
        ));
    }

    let surface_z = z_m_msl[0];

    // Convert winds to m/s, temps to K
    let u_ms: Vec<f64> = u_kt.iter().map(|&x| x * KNOTS_TO_MS).collect();
    let v_ms: Vec<f64> = v_kt.iter().map(|&x| x * KNOTS_TO_MS).collect();
    let t_k: Vec<f64> = t_c.iter().map(|&x| x + 273.15).collect();
    let td_k: Vec<f64> = td_c.iter().map(|&x| x + 273.15).collect();
    let z_agl_m: Vec<f64> = z_m_msl.iter().map(|&x| x - surface_z).collect();

    let result = PyDict::new(py);
    let none = || py.None();

    let sf_idx = find_surface_idx(&p_hpa);
    let mu_idx = find_most_unstable_idx(&p_hpa, &t_k, &td_k);

    // ── Bunkers storm motion ────────────────────────────────────────────
    let (storm_u, storm_v) = bunkers_storm_motion(&u_ms, &v_ms, &z_agl_m);

    // ── CAPE / CIN for SB, MU, ML ──────────────────────────────────────
    for (label, idx, use_mixed) in [("SB", sf_idx, false), ("MU", mu_idx, false)] {
        let (t_start, td_start) = if use_mixed {
            // ML: average potential temp and mixing ratio over 50 hPa
            let ml_top = p_hpa[sf_idx] - 50.0;
            let (mut sum_theta, mut sum_w, mut count) = (0.0, 0.0, 0.0);
            for i in sf_idx..n {
                if p_hpa[i] < ml_top {
                    break;
                }
                let theta = t_k[i] * (P0 / p_hpa[i]).powf(RD / CP);
                let e = es(td_k[i] - 273.15);
                let w = mixratio(e, p_hpa[i]);
                sum_theta += theta;
                sum_w += w;
                count += 1.0;
            }
            if count < 1.0 {
                continue;
            }
            let ml_theta = sum_theta / count;
            let ml_w = sum_w / count;
            // Back-compute T and Td from θ and w at surface pressure
            let ml_t = ml_theta / (P0 / p_hpa[sf_idx]).powf(RD / CP);
            // Approximate Td from w at surface pressure
            let e_ml = ml_w * p_hpa[sf_idx] / (EPS + ml_w);
            let ml_td = if e_ml > 0.0 {
                (243.5 * (e_ml / 6.112).ln()) / (17.67 - (e_ml / 6.112).ln())
            } else {
                ml_t - 20.0 // dry fallback
            };
            (ml_t, ml_td + 273.15)
        } else {
            (t_k[idx], td_k[idx])
        };

        if let Some((tp, tvp)) = lift_parcel(&p_hpa, &t_k, &td_k, &z_m_msl, t_start, td_start, idx)
        {
            if let Some((cape_val, cin_val)) =
                cape_cin(&p_hpa, &t_k, &td_k, &tp, &tvp, &z_m_msl, idx)
            {
                let prefix = match label {
                    "SB" => "sb",
                    "MU" => "mu",
                    "ML" => "ml",
                    _ => "",
                };
                result.set_item(format!("{}cape", prefix), cape_val)?;
                result.set_item(format!("{}cin", prefix), cin_val)?;

                // 0-3 km CAPE
                if let Some(c3) = cape_3km(&p_hpa, &tp, &tvp, &t_k, &td_k, &z_m_msl, idx) {
                    result.set_item(format!("{}3cape", prefix), c3)?;
                } else {
                    result.set_item(format!("{}3cape", prefix), none())?;
                }
            } else {
                result.set_item(format!("{}cape", label.to_lowercase()), none())?;
                result.set_item(format!("{}cin", label.to_lowercase()), none())?;
            }
        }
    }

    // ML parcel (50 hPa mixed layer)
    {
        if let Some((tp, tvp)) = lift_ml_parcel(&p_hpa, &t_k, &td_k, &z_m_msl, sf_idx) {
            if let Some((cape_val, cin_val)) =
                cape_cin(&p_hpa, &t_k, &td_k, &tp, &tvp, &z_m_msl, sf_idx)
            {
                result.set_item("mlcape", cape_val)?;
                result.set_item("mlcin", cin_val)?;
                if let Some(c3) = cape_3km(&p_hpa, &tp, &tvp, &t_k, &td_k, &z_m_msl, sf_idx) {
                    result.set_item("ml3cape", c3)?;
                } else {
                    result.set_item("ml3cape", none())?;
                }
            }
        }
    }

    // ── LCL / LFC ──────────────────────────────────────────────────────
    {
        let lcl_p = lcl_pressure(t_k[sf_idx], td_k[sf_idx], p_hpa[sf_idx]);
        result.set_item("sb_lcl_p", lcl_p)?;
    }

    // ── SRH ────────────────────────────────────────────────────────────
    for &(h_km, key) in &[
        (0.5, "srh_0_to_500"),
        (1.0, "srh_0_to_1000"),
        (3.0, "srh_0_to_3000"),
    ] {
        let srh =
            compute_srh_rust(&u_ms, &v_ms, &z_agl_m, storm_u, storm_v, h_km).unwrap_or(f64::NAN);
        result.set_item(key, srh)?;
    }

    // ── bulk shear ────────────────────────────────────────────────────
    for &(h_km, key) in &[
        (0.5, "shear_0_to_500"),
        (1.0, "shear_0_to_1000"),
        (3.0, "shear_0_to_3000"),
        (6.0, "shear_0_to_6000"),
    ] {
        let shear_ms = bulk_shear(&u_ms, &v_ms, &z_agl_m, h_km).unwrap_or(f64::NAN);
        result.set_item(key, shear_ms * 1.94384)?; // m/s → kt
    }

    // ── lapse rates ────────────────────────────────────────────────────
    for &(h_bot, h_top, key) in &[(0.0, 3.0, "lr_03km"), (3.0, 6.0, "lr_36km")] {
        let lr = lapse_rate(&t_k, &z_m_msl, h_bot, h_top, surface_z).unwrap_or(f64::NAN);
        result.set_item(key, lr)?;
    }

    Ok(result.into())
}

// ML parcel helper
fn lift_ml_parcel(
    p_hpa: &[f64],
    t_k: &[f64],
    td_k: &[f64],
    z_m: &[f64],
    sf_idx: usize,
) -> Option<(Vec<f64>, Vec<f64>)> {
    if sf_idx >= p_hpa.len() {
        return None;
    }
    let ml_top = p_hpa[sf_idx] - 50.0;
    let (mut sum_theta, mut sum_w, mut count) = (0.0, 0.0, 0.0);
    let n = p_hpa.len();

    for i in sf_idx..n {
        if p_hpa[i] < ml_top {
            break;
        }
        let theta = t_k[i] * (P0 / p_hpa[i]).powf(RD / CP);
        let e = es(td_k[i] - 273.15);
        let w = mixratio(e, p_hpa[i]);
        sum_theta += theta;
        sum_w += w;
        count += 1.0;
    }

    if count < 1.0 {
        return None;
    }
    let ml_theta = sum_theta / count;
    let ml_w = sum_w / count;
    let ml_t = ml_theta / (P0 / p_hpa[sf_idx]).powf(RD / CP);
    let e_ml = ml_w * p_hpa[sf_idx] / (EPS + ml_w);
    let ml_td = if e_ml > 0.0 {
        (243.5 * (e_ml / 6.112).ln()) / (17.67 - (e_ml / 6.112).ln())
    } else {
        ml_t - 20.0
    } + 273.15;

    lift_parcel(p_hpa, t_k, td_k, z_m, ml_t, ml_td, sf_idx)
}
