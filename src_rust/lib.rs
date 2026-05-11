#![allow(
    clippy::collapsible_if,
    clippy::manual_range_contains,
    clippy::type_complexity
)]

use geo::algorithm::contains::Contains;
use geo::{Coord, Point, Polygon};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;
use rstar::RTree;
use std::sync::RwLock;
use xxhash_rust::xxh3;

#[pymodule]
fn spc_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sum_as_string, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_fast_hash, m)?)?;
    m.add_function(wrap_pyfunction!(find_vwp_header_offset, m)?)?;
    m.add_function(wrap_pyfunction!(parse_vwp_tabular_data, m)?)?;
    m.add_function(wrap_pyfunction!(extract_latlon_coords, m)?)?;
    m.add_function(wrap_pyfunction!(init_radar_index, m)?)?;
    m.add_function(wrap_pyfunction!(find_nearest_radar, m)?)?;
    m.add_function(wrap_pyfunction!(filter_points_in_polygons, m)?)?;
    m.add_function(wrap_pyfunction!(vec2comp, m)?)?;
    m.add_function(wrap_pyfunction!(comp2vec, m)?)?;
    m.add_function(wrap_pyfunction!(compute_bunkers, m)?)?;
    m.add_function(wrap_pyfunction!(compute_srh, m)?)?;
    m.add_function(wrap_pyfunction!(compute_critical_angle, m)?)?;
    m.add_function(wrap_pyfunction!(compute_dtm, m)?)?;
    m.add_function(wrap_pyfunction!(compute_crit_angl, m)?)?;
    m.add_function(wrap_pyfunction!(parse_vtec, m)?)?;
    m.add_function(wrap_pyfunction!(validate_image_cache_batch, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_product_id, m)?)?;
    m.add_function(wrap_pyfunction!(haversine, m)?)?;
    m.add_function(wrap_pyfunction!(haversine_batch, m)?)?;
    Ok(())
}

#[pyfunction]
fn sum_as_string(a: usize, b: usize) -> PyResult<String> {
    Ok((a + b).to_string())
}

#[pyfunction]
fn calculate_fast_hash(data: &[u8]) -> PyResult<String> {
    let hash = xxh3::xxh3_64(data);
    Ok(format!("{:016x}", hash))
}

#[pyfunction]
fn find_vwp_header_offset(data: &[u8]) -> PyResult<Option<usize>> {
    if data.len() < 2 {
        return Ok(None);
    }
    let search_limit = std::cmp::min(data.len() - 2, 200);
    for i in 0..=search_limit {
        if data[i] == 0x00 && data[i + 1] == 0x30 {
            if i >= 30 {
                if data[i - 30] == 0x00 && data[i - 30 + 1] == 0x30 {
                    return Ok(Some(i));
                }
            }
        }
    }
    Ok(None)
}

struct VadRecord {
    wind_dir: f64,
    wind_spd: f64,
    rms_error: f64,
    divergence: f64,
    slant_range: f64,
    elev_angle: f64,
    altitude: f64,
}

/// Fast parser for VWP tabular data.
/// Returns a dictionary of lists (wind_dir, wind_spd, etc.) sorted by altitude.
/// Processes all pages of multi-page VAD data and merges results.
#[pyfunction]
fn parse_vwp_tabular_data<'py>(
    py: Python<'py>,
    data: &[u8],
    _offset_tabular: usize,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let marker = b"VAD Algorithm Output";
    let mut records = Vec::new();
    let mut search_start = 0;

    // Find all occurrences of marker (handles multi-page VAD files)
    while let Some(marker_pos) = data[search_start..]
        .windows(marker.len())
        .position(|w| w == marker)
    {
        let actual_pos = search_start + marker_pos;

        // Find the 0x00 0x50 line start marker before this data marker
        let mut line_start = 0;
        for i in (0..actual_pos).rev() {
            if i + 2 <= data.len() && data[i] == 0x00 && data[i + 1] == 0x50 {
                line_start = i;
                break;
            }
        }

        if line_start == 0 {
            search_start = actual_pos + marker.len();
            if search_start >= data.len() {
                break;
            }
            continue;
        }

        // Parse this page starting from line_start
        let mut current_pos = line_start;
        let mut line_count = 0;

        while current_pos + 82 <= data.len() {
            let len = i16::from_be_bytes([data[current_pos], data[current_pos + 1]]);

            if len != 80 {
                if len == -1 || len == 0 {
                    break;
                }
                current_pos += 2;
                continue;
            }

            // Skip first 3 lines of headers
            if line_count >= 3 {
                let line_bytes = &data[current_pos + 2..current_pos + 82];
                // Use lossy UTF-8 conversion for just this line
                let line = String::from_utf8_lossy(line_bytes);
                let parts: Vec<&str> = line.split_whitespace().collect();

                if parts.len() >= 10 {
                    if let (Some(d), Some(s), Some(r), Some(v), Some(sl), Some(e)) = (
                        parts[4].parse::<f64>().ok(),
                        parts[5].parse::<f64>().ok(),
                        parts[6].parse::<f64>().ok(),
                        if parts[7] == "NA" {
                            Some(f64::NAN)
                        } else {
                            parts[7].parse::<f64>().ok()
                        },
                        parts[8].parse::<f64>().ok(),
                        parts[9].parse::<f64>().ok(),
                    ) {
                        let slant_km: f64 = sl * (6067.1 / 3281.0);
                        let r_e: f64 = (4.0 / 3.0) * 6371.0;
                        let elev_rad: f64 = e.to_radians();
                        let alt = (r_e.powi(2)
                            + slant_km.powi(2)
                            + 2.0 * r_e * slant_km * elev_rad.sin())
                        .sqrt()
                            - r_e;

                        records.push(VadRecord {
                            wind_dir: d,
                            wind_spd: s,
                            rms_error: r,
                            divergence: v,
                            slant_range: slant_km,
                            elev_angle: e,
                            altitude: alt,
                        });
                    }
                }
            }

            current_pos += 82;
            line_count += 1;
        }

        search_start = actual_pos + marker.len();
        if search_start >= data.len() {
            break;
        }
    }

    if records.is_empty() {
        return Ok(None);
    }

    // Sort by altitude (all pages merged and sorted together)
    records.sort_by(|a, b| {
        a.altitude
            .partial_cmp(&b.altitude)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let dict = PyDict::new_bound(py);
    let mut wind_dir = Vec::with_capacity(records.len());
    let mut wind_spd = Vec::with_capacity(records.len());
    let mut rms_error = Vec::with_capacity(records.len());
    let mut divergence = Vec::with_capacity(records.len());
    let mut slant_range = Vec::with_capacity(records.len());
    let mut elev_angle = Vec::with_capacity(records.len());
    let mut altitude = Vec::with_capacity(records.len());

    for r in records {
        wind_dir.push(r.wind_dir);
        wind_spd.push(r.wind_spd);
        rms_error.push(r.rms_error);
        divergence.push(r.divergence);
        slant_range.push(r.slant_range);
        elev_angle.push(r.elev_angle);
        altitude.push(r.altitude);
    }

    dict.set_item("wind_dir", wind_dir)?;
    dict.set_item("wind_spd", wind_spd)?;
    dict.set_item("rms_error", rms_error)?;
    dict.set_item("divergence", divergence)?;
    dict.set_item("slant_range", slant_range)?;
    dict.set_item("elev_angle", elev_angle)?;
    dict.set_item("altitude", altitude)?;
    Ok(Some(dict))
}

// ── Pillar #1: Fast Coordinate Extraction ────────────────────────────────────

#[pyfunction]
fn extract_latlon_coords(text: &str) -> PyResult<Vec<(f64, f64)>> {
    let mut coords = Vec::with_capacity(20);
    let mut nums = text.split_whitespace();
    while let (Some(lat_str), Some(lon_str)) = (nums.next(), nums.next()) {
        if let (Ok(lat_int), Ok(lon_int)) = (lat_str.parse::<i32>(), lon_str.parse::<i32>()) {
            let lat = lat_int as f64 / 100.0;
            let lon = -(lon_int as f64 / 100.0);
            if lat >= 15.0 && lat <= 75.0 && lon >= -170.0 && lon <= -60.0 {
                coords.push((lat, lon));
            }
        }
    }
    Ok(coords)
}

// ── Pillar #2: R-Tree Radar Lookup ───────────────────────────────────────────

struct RadarPoint {
    id: String,
    location: [f64; 2],
}

impl rstar::RTreeObject for RadarPoint {
    type Envelope = rstar::AABB<[f64; 2]>;
    fn envelope(&self) -> Self::Envelope {
        rstar::AABB::from_point(self.location)
    }
}

impl rstar::PointDistance for RadarPoint {
    fn distance_2(&self, point: &[f64; 2]) -> f64 {
        let d1 = self.location[0] - point[0];
        let d2 = self.location[1] - point[1];
        d1 * d1 + d2 * d2
    }
}

static RADAR_INDEX: Lazy<RwLock<Option<RTree<RadarPoint>>>> = Lazy::new(|| RwLock::new(None));

#[pyfunction]
fn init_radar_index(coords: &Bound<'_, PyDict>) -> PyResult<()> {
    let mut points = Vec::new();
    for (id, loc) in coords.iter() {
        let id_str: String = id.extract()?;
        let loc_tuple: (f64, f64) = loc.extract()?;
        points.push(RadarPoint {
            id: id_str,
            location: [loc_tuple.0, loc_tuple.1],
        });
    }
    let mut index = RADAR_INDEX.write().unwrap();
    *index = Some(RTree::bulk_load(points));
    Ok(())
}

#[pyfunction]
fn find_nearest_radar(lat: f64, lon: f64) -> PyResult<Option<String>> {
    let index_lock = RADAR_INDEX.read().unwrap();
    if let Some(index) = index_lock.as_ref() {
        if let Some(nearest) = index.nearest_neighbor(&[lat, lon]) {
            return Ok(Some(nearest.id.clone()));
        }
    }
    Ok(None)
}

// ── Pillar #3: Batch Spatial Joins ───────────────────────────────────────────

#[pyfunction]
fn filter_points_in_polygons(
    points: Vec<(f64, f64)>,
    polygons: Vec<Vec<(f64, f64)>>,
) -> PyResult<Vec<usize>> {
    let mut result = Vec::new();
    let geo_polys: Vec<Polygon<f64>> = polygons
        .into_iter()
        .filter(|p| p.len() >= 3)
        .map(|p| {
            let coords: Vec<Coord<f64>> = p
                .into_iter()
                .map(|(lat, lon)| Coord { x: lon, y: lat })
                .collect();
            Polygon::new(geo::LineString::new(coords), vec![])
        })
        .collect();

    for (idx, (lat, lon)) in points.into_iter().enumerate() {
        let p = Point::new(lon, lat);
        for poly in &geo_polys {
            if poly.contains(&p) {
                result.push(idx);
                break;
            }
        }
    }
    Ok(result)
}

// ── Phase 1: SRH/Bunkers Calculator ──────────────────────────────────────────

#[pyfunction]
fn vec2comp(wdir: f64, wspd: f64) -> PyResult<(f64, f64)> {
    let wdir_rad = wdir.to_radians();
    let u = -wspd * wdir_rad.sin();
    let v = -wspd * wdir_rad.cos();
    Ok((u, v))
}

#[pyfunction]
fn comp2vec(u: f64, v: f64) -> PyResult<(f64, f64)> {
    let spd = (u * u + v * v).sqrt();
    let dir = if spd > 0.0 {
        let angle_rad = (-v).atan2(-u);
        let angle_deg = angle_rad.to_degrees();
        let dir_deg = (90.0 - angle_deg) % 360.0;
        if dir_deg < 0.0 {
            dir_deg + 360.0
        } else {
            dir_deg
        }
    } else {
        0.0
    };
    Ok((dir, spd))
}

fn clip_profile(
    wind_dir: &[f64],
    wind_spd: &[f64],
    altitude: &[f64],
    max_hght: f64,
) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let mut clipped_dir = Vec::new();
    let mut clipped_spd = Vec::new();
    let mut clipped_alt = Vec::new();
    for (i, &alt) in altitude.iter().enumerate() {
        if alt <= max_hght {
            clipped_dir.push(wind_dir[i]);
            clipped_spd.push(wind_spd[i]);
            clipped_alt.push(alt);
        }
    }
    (clipped_dir, clipped_spd, clipped_alt)
}

fn _interp_linear(x: &[f64], y: &[f64], xi: f64) -> f64 {
    if x.is_empty() {
        return f64::NAN;
    }
    if xi <= x[0] {
        return if x[0].is_nan() { f64::NAN } else { y[0] };
    }
    if xi >= x[x.len() - 1] {
        return if x[x.len() - 1].is_nan() {
            f64::NAN
        } else {
            y[y.len() - 1]
        };
    }
    for i in 0..x.len() - 1 {
        if x[i] <= xi && xi <= x[i + 1] {
            let t = (xi - x[i]) / (x[i + 1] - x[i]);
            return y[i] + t * (y[i + 1] - y[i]);
        }
    }
    f64::NAN
}

#[pyfunction]
fn compute_bunkers(
    wind_dir: Vec<f64>,
    wind_spd: Vec<f64>,
    altitude: Vec<f64>,
) -> PyResult<((f64, f64), (f64, f64), (f64, f64))> {
    if wind_dir.is_empty() {
        return Ok((
            (f64::NAN, f64::NAN),
            (f64::NAN, f64::NAN),
            (f64::NAN, f64::NAN),
        ));
    }

    let hght = 6.0; // Height in same units as altitude (km)
    let d = 7.5 * 1.94;

    // Convert all wind vectors to u/v components
    let mut u_all = Vec::new();
    let mut v_all = Vec::new();
    for i in 0..wind_dir.len() {
        let (u, v) = vec2comp(wind_dir[i], wind_spd[i])?;
        u_all.push(u);
        v_all.push(v);
    }

    // Interpolate u/v to the target height (6 in same units as altitude)
    let u_hght = _interp_linear(&altitude, &u_all, hght);
    let v_hght = _interp_linear(&altitude, &v_all, hght);

    // Clip profile: find where altitude[i] <= hght < altitude[i+1]
    // Then include all points up to i and append the interpolated value
    let mut clip_idx: Option<usize> = None;
    for i in 0..(altitude.len() - 1) {
        if altitude[i] <= hght && hght < altitude[i + 1] {
            clip_idx = Some(i);
            break;
        }
    }

    let mut u_clip = Vec::new();
    let mut v_clip = Vec::new();

    if let Some(idx) = clip_idx {
        // Normal case: hght is between two points
        for i in 0..=idx {
            u_clip.push(u_all[i]);
            v_clip.push(v_all[i]);
        }
    } else if !altitude.is_empty() && altitude[altitude.len() - 1] <= hght {
        // Edge case: hght is at or beyond the last point
        // Include all points
        for i in 0..altitude.len() {
            if altitude[i] <= hght {
                u_clip.push(u_all[i]);
                v_clip.push(v_all[i]);
            }
        }
    } else {
        // hght is below all points or other edge case - return NaN
        return Ok((
            (f64::NAN, f64::NAN),
            (f64::NAN, f64::NAN),
            (f64::NAN, f64::NAN),
        ));
    }

    // Append interpolated value at target height
    u_clip.push(u_hght);
    v_clip.push(v_hght);

    if u_clip.len() < 2 {
        return Ok((
            (f64::NAN, f64::NAN),
            (f64::NAN, f64::NAN),
            (f64::NAN, f64::NAN),
        ));
    }

    // Mean wind 0-6km: average of u/v components
    let mnu6 = u_clip.iter().sum::<f64>() / u_clip.len() as f64;
    let mnv6 = v_clip.iter().sum::<f64>() / v_clip.len() as f64;

    // Shear: difference between top (interpolated at hght) and bottom (surface)
    let shru = u_hght - u_all[0];
    let shrv = v_hght - v_all[0];

    // Bunkers displacement: perpendicular to shear
    let shear_mag = (shru * shru + shrv * shrv).sqrt();
    let tmp = if shear_mag > 0.01 { d / shear_mag } else { 0.0 };

    // Right and left storm motions
    let rstu = mnu6 + (tmp * shrv);
    let rstv = mnv6 - (tmp * shru);
    let lstu = mnu6 - (tmp * shrv);
    let lstv = mnv6 + (tmp * shru);

    // Convert back to dir/spd
    let (right_dir, right_spd) = comp2vec(rstu, rstv)?;
    let (left_dir, left_spd) = comp2vec(lstu, lstv)?;
    let (mean_dir, mean_spd) = comp2vec(mnu6, mnv6)?;

    // Return in Python order: (right, left, mean)
    Ok((
        (right_dir, right_spd),
        (left_dir, left_spd),
        (mean_dir, mean_spd),
    ))
}

#[pyfunction]
fn compute_srh(
    wind_dir: Vec<f64>,
    wind_spd: Vec<f64>,
    altitude: Vec<f64>,
    storm_dir: f64,
    storm_spd: f64,
    hght_km: f64,
) -> PyResult<f64> {
    if wind_dir.is_empty() {
        return Ok(f64::NAN);
    }

    let hght = hght_km; // Altitude is already in km

    // Convert all wind vectors to u/v components
    let mut u_all = Vec::new();
    let mut v_all = Vec::new();
    for i in 0..wind_dir.len() {
        let (u, v) = vec2comp(wind_dir[i], wind_spd[i])?;
        u_all.push(u);
        v_all.push(v);
    }

    // Convert storm motion to u/v
    let (storm_u, storm_v) = vec2comp(storm_dir, storm_spd)?;

    // Compute storm-relative wind for entire profile
    let mut sru = Vec::new();
    let mut srv = Vec::new();
    for i in 0..u_all.len() {
        sru.push((u_all[i] - storm_u) / 1.94);
        srv.push((v_all[i] - storm_v) / 1.94);
    }

    // Interpolate to target height
    let sru_hght = _interp_linear(&altitude, &sru, hght);
    let srv_hght = _interp_linear(&altitude, &srv, hght);

    // Clip and append interpolated values (matching Python's _clip_profile)
    let mut sru_clip = Vec::new();
    let mut srv_clip = Vec::new();
    for i in 0..altitude.len() {
        if altitude[i] <= hght {
            sru_clip.push(sru[i]);
            srv_clip.push(srv[i]);
        }
    }
    sru_clip.push(sru_hght);
    srv_clip.push(srv_hght);

    if sru_clip.len() < 2 {
        return Ok(f64::NAN);
    }

    // Compute cross products between consecutive layers: u[i+1]*v[i] - u[i]*v[i+1]
    let mut srh = 0.0;
    for i in 0..(sru_clip.len() - 1) {
        let cross = (sru_clip[i + 1] * srv_clip[i]) - (sru_clip[i] * srv_clip[i + 1]);
        srh += cross;
    }

    Ok(srh)
}

#[pyfunction]
fn compute_critical_angle(
    wind_dir: Vec<f64>,
    wind_spd: Vec<f64>,
    altitude: Vec<f64>,
    storm_dir: f64,
    storm_spd: f64,
) -> PyResult<f64> {
    if wind_dir.is_empty() {
        return Ok(f64::NAN);
    }

    let (clipped_dir, clipped_spd, _) = clip_profile(&wind_dir, &wind_spd, &altitude, 6000.0);
    if clipped_dir.is_empty() {
        return Ok(f64::NAN);
    }

    let storm_rad = storm_dir.to_radians();
    let (storm_u, storm_v) = (-storm_spd * storm_rad.sin(), -storm_spd * storm_rad.cos());

    let mut min_angle: f64 = 360.0;
    for i in 0..clipped_dir.len() {
        let wd = clipped_dir[i].to_radians();
        let u = -clipped_spd[i] * wd.sin();
        let v = -clipped_spd[i] * wd.cos();

        let rel_u = u - storm_u;
        let rel_v = v - storm_v;
        let rel_dir = ((-rel_v).atan2(-rel_u).to_degrees() + 90.0) % 360.0;
        let diff = (rel_dir - storm_dir).abs();
        if diff > 180.0 {
            min_angle = min_angle.min(360.0 - diff);
        } else {
            min_angle = min_angle.min(diff);
        }
    }

    Ok(min_angle)
}

// ── Phase 1b: DTM and Critical Angle ─────────────────────────────────────────

/// Deviant Tornado Motion (DTM) — 70% Bunkers RM + 30% 0–500m mean wind.
/// Returns (dir_deg, spd_kt) or (NaN, NaN) on failure.
#[pyfunction]
fn compute_dtm(wind_dir: Vec<f64>, wind_spd: Vec<f64>, altitude: Vec<f64>) -> PyResult<(f64, f64)> {
    if wind_dir.is_empty() {
        return Ok((f64::NAN, f64::NAN));
    }

    // Convert all wind to u/v
    let mut u_all = Vec::with_capacity(wind_dir.len());
    let mut v_all = Vec::with_capacity(wind_dir.len());
    for i in 0..wind_dir.len() {
        let (u, v) = vec2comp(wind_dir[i], wind_spd[i])?;
        u_all.push(u);
        v_all.push(v);
    }

    // Mean u/v over 0–0.5 km using 20 linearly-spaced interpolation points
    let n = 20usize;
    let mut sum_u = 0.0f64;
    let mut sum_v = 0.0f64;
    let mut count = 0usize;
    for k in 0..n {
        let h = 0.5 * k as f64 / (n - 1) as f64;
        let ui = _interp_linear(&altitude, &u_all, h);
        let vi = _interp_linear(&altitude, &v_all, h);
        if ui.is_finite() && vi.is_finite() {
            sum_u += ui;
            sum_v += vi;
            count += 1;
        }
    }
    if count == 0 {
        return Ok((f64::NAN, f64::NAN));
    }
    let mn_u_500 = sum_u / count as f64;
    let mn_v_500 = sum_v / count as f64;

    // Bunkers right-mover
    let ((brm_dir, brm_spd), _, _) = compute_bunkers(wind_dir, wind_spd, altitude)?;
    if brm_dir.is_nan() {
        return Ok((f64::NAN, f64::NAN));
    }
    let (brm_u, brm_v) = vec2comp(brm_dir, brm_spd)?;

    // DTM = 0.7 * BRM + 0.3 * mean_0500m
    let dtm_u = 0.7 * brm_u + 0.3 * mn_u_500;
    let dtm_v = 0.7 * brm_v + 0.3 * mn_v_500;

    comp2vec(dtm_u, dtm_v)
}

/// Critical angle — angle between the storm-relative surface-to-BRM vector
/// and the 0–0.5 km shear vector (Rasmussen 2003).
/// Returns degrees or NaN.
#[pyfunction]
fn compute_crit_angl(
    wind_dir: Vec<f64>,
    wind_spd: Vec<f64>,
    altitude: Vec<f64>,
    storm_dir: f64,
    storm_spd: f64,
) -> PyResult<f64> {
    if wind_dir.is_empty() {
        return Ok(f64::NAN);
    }

    let (storm_u, storm_v) = vec2comp(storm_dir, storm_spd)?;

    // Surface u/v
    let (u0, v0) = vec2comp(wind_dir[0], wind_spd[0])?;

    // Convert all to u/v for interpolation
    let mut u_all = Vec::with_capacity(wind_dir.len());
    let mut v_all = Vec::with_capacity(wind_dir.len());
    for i in 0..wind_dir.len() {
        let (u, v) = vec2comp(wind_dir[i], wind_spd[i])?;
        u_all.push(u);
        v_all.push(v);
    }

    // Interpolate to 0.5 km
    let u_05 = _interp_linear(&altitude, &u_all, 0.5);
    let v_05 = _interp_linear(&altitude, &v_all, 0.5);
    if u_05.is_nan() || v_05.is_nan() {
        return Ok(f64::NAN);
    }

    // base = storm motion relative to surface wind
    let base_u = storm_u - u0;
    let base_v = storm_v - v0;

    // ang = 0.5km wind relative to surface wind (0–0.5km shear)
    let ang_u = u_05 - u0;
    let ang_v = v_05 - v0;

    let len_base = (base_u * base_u + base_v * base_v).sqrt();
    let len_ang = (ang_u * ang_u + ang_v * ang_v).sqrt();

    if len_base < 1e-9 || len_ang < 1e-9 {
        return Ok(f64::NAN);
    }

    let cos_theta = (base_u * ang_u + base_v * ang_v) / (len_base * len_ang);
    // Clamp to [-1, 1] to guard against float rounding past the domain of acos
    let cos_theta = cos_theta.clamp(-1.0, 1.0);
    Ok(cos_theta.acos().to_degrees())
}

// ── Phase 2: VTEC Parser ─────────────────────────────────────────────────────

#[pyfunction]
fn parse_vtec<'py>(py: Python<'py>, text: &str) -> PyResult<Option<Bound<'py, PyDict>>> {
    static VTEC_RE: Lazy<Regex> = Lazy::new(|| {
        let pattern = r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/";
        Regex::new(pattern).unwrap()
    });

    if text.is_empty() {
        return Ok(None);
    }

    if let Some(caps) = VTEC_RE.captures(text) {
        let action = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        let office_raw = caps.get(2).map(|m| m.as_str()).unwrap_or("");
        let phenom = caps.get(3).map(|m| m.as_str()).unwrap_or("");
        let sig = caps.get(4).map(|m| m.as_str()).unwrap_or("");
        let etn = caps.get(5).map(|m| m.as_str()).unwrap_or("");
        let start = caps.get(6).map(|m| m.as_str()).unwrap_or("");
        let end = caps.get(7).map(|m| m.as_str()).unwrap_or("");

        // Normalize office: if 3 chars and starts with letter, prepend K
        let office = if office_raw.len() == 3
            && office_raw
                .chars()
                .next()
                .unwrap_or('Z')
                .is_ascii_uppercase()
        {
            format!("K{}", office_raw)
        } else {
            office_raw.to_string()
        };

        let vtec_id = format!("{}.{}.{}.{}", office, phenom, sig, etn);

        let result = PyDict::new_bound(py);
        result.set_item("action", action)?;
        result.set_item("office", office)?;
        result.set_item("phenom", phenom)?;
        result.set_item("sig", sig)?;
        result.set_item("etn", etn)?;
        result.set_item("start", start)?;
        result.set_item("end", end)?;
        result.set_item("vtec_id", vtec_id)?;
        return Ok(Some(result));
    }

    Ok(None)
}

// ── Phase 3: Image Cache Batch Validator ────────────────────────────────────

#[pyfunction]
fn validate_image_cache_batch(
    items: Vec<(String, Vec<u8>)>,
) -> PyResult<Vec<(String, String, bool)>> {
    let mut results = Vec::with_capacity(items.len());
    for (url, content) in items {
        let hash_hex = format!("{:016x}", xxh3::xxh3_64(&content));
        let is_placeholder = content.len() < 2048;
        results.push((url, hash_hex, is_placeholder));
    }
    Ok(results)
}

// ── Phase 4: NWWS product_id Normalizer ──────────────────────────────────────

#[pyfunction]
fn normalize_product_id(
    office: &str,
    ttaaii: &str,
    afos_pil: &str,
    issue_str: &str,
) -> PyResult<String> {
    let mut ts_str = issue_str.to_string();

    // Normalize ISO8601 format to compact format for dedup consistency
    if ts_str.contains("T") && ts_str.contains("Z") {
        // Convert "2026-05-03T06:50:00Z" → "202605030650"
        ts_str = ts_str.replace("-", "").replace("T", "").replace(":", "");
        if let Some(pos) = ts_str.find("Z") {
            ts_str.truncate(pos);
        }
    }

    // Take first 12 characters (YYYYMMDDHHMM format)
    if ts_str.len() > 12 {
        ts_str.truncate(12);
    }

    Ok(format!("{}-{}-{}-{}", ts_str, office, ttaaii, afos_pil))
}

// ── Phase 5: Haversine Batch Calculator ──────────────────────────────────────

#[pyfunction]
fn haversine(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> PyResult<f64> {
    // Great-circle distance formula
    let lat1_rad = lat1.to_radians();
    let lat2_rad = lat2.to_radians();
    let delta_lat = (lat2 - lat1).to_radians();
    let delta_lon = (lon2 - lon1).to_radians();

    let a = (delta_lat / 2.0).sin().powi(2)
        + lat1_rad.cos() * lat2_rad.cos() * (delta_lon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    let earth_radius_km = 6371.0;

    Ok(earth_radius_km * c)
}

#[pyfunction]
fn haversine_batch(
    origin_lat: f64,
    origin_lon: f64,
    targets: Vec<(f64, f64)>,
) -> PyResult<Vec<f64>> {
    let mut distances = Vec::with_capacity(targets.len());
    for (target_lat, target_lon) in targets {
        let dist = haversine(origin_lat, origin_lon, target_lat, target_lon)?;
        distances.push(dist);
    }
    Ok(distances)
}

// ── Rust Unit Tests ──────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vec2comp_north_wind() {
        let (u, v) = vec2comp(0.0, 10.0).unwrap();
        assert!(u.abs() < 0.01);
        assert!((v - (-10.0)).abs() < 0.01);
    }

    #[test]
    fn test_vec2comp_east_wind() {
        let (u, v) = vec2comp(90.0, 10.0).unwrap();
        assert!((u - 10.0).abs() < 0.01);
        assert!(v.abs() < 0.01);
    }

    #[test]
    fn test_comp2vec_north() {
        let (dir, spd) = comp2vec(0.0, -10.0).unwrap();
        assert!((spd - 10.0).abs() < 0.01);
        assert!(dir < 1.0 || dir > 359.0); // Should be ~0 or ~360
    }

    #[test]
    fn test_comp2vec_magnitude() {
        let (_, spd) = comp2vec(3.0, 4.0).unwrap();
        assert!((spd - 5.0).abs() < 0.01); // 3-4-5 triangle
    }

    #[test]
    fn test_haversine_zero_distance() {
        let dist = haversine(0.0, 0.0, 0.0, 0.0).unwrap();
        assert!(dist.abs() < 0.001);
    }

    #[test]
    fn test_haversine_symmetry() {
        let d1 = haversine(40.0, -100.0, 35.0, -95.0).unwrap();
        let d2 = haversine(35.0, -95.0, 40.0, -100.0).unwrap();
        assert!((d1 - d2).abs() < 0.001);
    }

    #[test]
    fn test_haversine_known_distance() {
        // NYC to LA is approximately 3944 km
        let dist = haversine(40.7128, -74.0060, 34.0522, -118.2437).unwrap();
        assert!(dist > 3900.0 && dist < 4000.0);
    }

    #[test]
    fn test_haversine_batch_single_target() {
        let targets = vec![(0.0, 0.0)];
        let dists = haversine_batch(0.0, 0.0, targets).unwrap();
        assert_eq!(dists.len(), 1);
        assert!(dists[0].abs() < 0.001);
    }

    #[test]
    fn test_haversine_batch_multiple_targets() {
        let targets = vec![(1.0, 0.0), (2.0, 0.0), (3.0, 0.0)];
        let dists = haversine_batch(0.0, 0.0, targets).unwrap();
        assert_eq!(dists.len(), 3);
        // Distances should be increasing
        assert!(dists[0] < dists[1]);
        assert!(dists[1] < dists[2]);
    }

    #[test]
    fn test_extract_latlon_valid() {
        let text = "3567 9823 4521 10134";
        let coords = extract_latlon_coords(text).unwrap();
        assert_eq!(coords.len(), 2);
        assert!((coords[0].0 - 35.67).abs() < 0.01);
        assert!((coords[0].1 - (-98.23)).abs() < 0.01);
    }

    #[test]
    fn test_extract_latlon_out_of_range() {
        let text = "1000 2000"; // Out of valid range
        let coords = extract_latlon_coords(text).unwrap();
        assert_eq!(coords.len(), 0);
    }

    #[test]
    fn test_clip_profile_simple() {
        let wind_dir = vec![250.0, 260.0, 270.0, 280.0];
        let wind_spd = vec![10.0, 15.0, 20.0, 25.0];
        let altitude = vec![0.0, 2000.0, 4000.0, 6000.0];
        let (clipped_dir, clipped_spd, clipped_alt) =
            clip_profile(&wind_dir, &wind_spd, &altitude, 5000.0);
        // Should include 0, 2000, 4000 but not 6000
        assert_eq!(clipped_dir.len(), 3);
        assert_eq!(clipped_spd.len(), 3);
        assert_eq!(clipped_alt.len(), 3);
    }

    #[test]
    fn test_compute_bunkers_valid_profile() {
        let wind_dir = vec![250.0; 7];
        let wind_spd = vec![5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0];
        let altitude = vec![0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0];

        let (mean, left, right) = compute_bunkers(wind_dir, wind_spd, altitude).unwrap();
        // Each motion should be (direction, speed)
        assert!(mean.1 > 0.0); // Mean wind speed should be positive
        assert!(left.1 > 0.0); // Left motion speed should be positive
        assert!(right.1 > 0.0); // Right motion speed should be positive
    }

    #[test]
    fn test_compute_bunkers_empty_profile() {
        let result = compute_bunkers(vec![], vec![], vec![]).unwrap();
        assert!(result.0 .0.is_nan());
        assert!(result.0 .1.is_nan());
    }

    #[test]
    fn test_compute_srh_valid_profile() {
        let wind_dir = vec![250.0; 7];
        let wind_spd = vec![5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0];
        let altitude = vec![0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0];

        let srh = compute_srh(wind_dir, wind_spd, altitude, 250.0, 15.0, 3000.0).unwrap();
        // SRH should be a finite number
        assert!(srh.is_finite());
    }

    #[test]
    fn test_compute_srh_empty_profile() {
        let srh = compute_srh(vec![], vec![], vec![], 250.0, 15.0, 3000.0).unwrap();
        assert!(srh.is_nan());
    }

    #[test]
    fn test_xxh3_hash_consistency() {
        let data1 = b"test data";
        let hash1a = xxh3::xxh3_64(data1);
        let hash1b = xxh3::xxh3_64(data1);
        assert_eq!(hash1a, hash1b);
    }

    #[test]
    fn test_xxh3_hash_different() {
        let hash1 = xxh3::xxh3_64(b"test1");
        let hash2 = xxh3::xxh3_64(b"test2");
        assert_ne!(hash1, hash2);
    }

    #[test]
    fn test_vtec_regex_match() {
        let pattern = r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/";
        let re = Regex::new(pattern).unwrap();
        let text = "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/";
        assert!(re.is_match(text));
    }

    #[test]
    fn test_vtec_regex_no_match() {
        let pattern = r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/";
        let re = Regex::new(pattern).unwrap();
        let text = "No VTEC here";
        assert!(!re.is_match(text));
    }

    #[test]
    fn test_vtec_capture_groups() {
        let pattern = r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/";
        let re = Regex::new(pattern).unwrap();
        let text = "/O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/";
        let caps = re.captures(text).unwrap();
        assert_eq!(caps.get(1).unwrap().as_str(), "NEW");
        assert_eq!(caps.get(2).unwrap().as_str(), "KOUN");
        assert_eq!(caps.get(3).unwrap().as_str(), "TO");
    }

    #[test]
    fn test_normalize_product_id_iso8601() {
        let id = normalize_product_id("KOUN", "ACUS42", "SVDMX", "2026-05-03T06:50:00Z").unwrap();
        assert_eq!(id, "202605030650-KOUN-ACUS42-SVDMX");
    }

    #[test]
    fn test_normalize_product_id_compact() {
        let id = normalize_product_id("KOUN", "ACUS42", "SVDMX", "202605030650").unwrap();
        assert_eq!(id, "202605030650-KOUN-ACUS42-SVDMX");
    }

    #[test]
    fn test_normalize_product_id_truncate() {
        let id = normalize_product_id("KOUN", "ACUS42", "SVDMX", "20260503065030").unwrap();
        assert_eq!(id, "202605030650-KOUN-ACUS42-SVDMX");
    }

    #[test]
    fn test_sum_as_string() {
        let result = sum_as_string(5, 3).unwrap();
        assert_eq!(result, "8");
    }
}
