use pyo3::prelude::*;
use pyo3::types::PyDict;
use xxhash_rust::xxh3;
use rstar::RTree;
use std::sync::RwLock;
use once_cell::sync::Lazy;
use geo::{Polygon, Point, Coord};
use geo::algorithm::contains::Contains;
use regex::Regex;

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
    m.add_function(wrap_pyfunction!(parse_vtec, m)?)?;
    m.add_function(wrap_pyfunction!(validate_image_cache_batch, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_product_id, m)?)?;
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
    if data.len() < 2 { return Ok(None); }
    let search_limit = std::cmp::min(data.len() - 2, 200);
    for i in 0..=search_limit {
        if data[i] == 0x00 && data[i+1] == 0x30 {
            if i >= 30 {
                if data[i-30] == 0x00 && data[i-30+1] == 0x30 {
                    return Ok(Some(i));
                }
            }
        }
    }
    Ok(None)
}

struct VadRecord {
    wind_dir: f64, wind_spd: f64, rms_error: f64, divergence: f64,
    slant_range: f64, elev_angle: f64, altitude: f64,
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
    while let Some(marker_pos) = data[search_start..].windows(marker.len()).position(|w| w == marker) {
        let actual_pos = search_start + marker_pos;

        // Find the 0x00 0x50 line start marker before this data marker
        let mut line_start = 0;
        for i in (0..actual_pos).rev() {
            if i + 2 <= data.len() && data[i] == 0x00 && data[i+1] == 0x50 {
                line_start = i;
                break;
            }
        }

        if line_start == 0 {
            search_start = actual_pos + marker.len();
            if search_start >= data.len() { break; }
            continue;
        }

        // Parse this page starting from line_start
        let mut current_pos = line_start;
        let mut line_count = 0;

        while current_pos + 82 <= data.len() {
            let len = i16::from_be_bytes([data[current_pos], data[current_pos+1]]);

            if len != 80 {
                if len == -1 || len == 0 { break; }
                current_pos += 2;
                continue;
            }

            // Skip first 3 lines of headers
            if line_count >= 3 {
                let line_bytes = &data[current_pos+2..current_pos+82];
                // Use lossy UTF-8 conversion for just this line
                let line = String::from_utf8_lossy(line_bytes);
                let parts: Vec<&str> = line.split_whitespace().collect();

                if parts.len() >= 10 {
                    if let (Some(d), Some(s), Some(r), Some(v), Some(sl), Some(e)) = (
                        parts[4].parse::<f64>().ok(),
                        parts[5].parse::<f64>().ok(),
                        parts[6].parse::<f64>().ok(),
                        if parts[7] == "NA" { Some(f64::NAN) } else { parts[7].parse::<f64>().ok() },
                        parts[8].parse::<f64>().ok(),
                        parts[9].parse::<f64>().ok(),
                    ) {
                        let slant_km: f64 = sl * (6067.1 / 3281.0);
                        let r_e: f64 = (4.0 / 3.0) * 6371.0;
                        let elev_rad: f64 = e.to_radians();
                        let alt = (r_e.powi(2) + slant_km.powi(2) + 2.0 * r_e * slant_km * elev_rad.sin()).sqrt() - r_e;

                        records.push(VadRecord {
                            wind_dir: d, wind_spd: s, rms_error: r, divergence: v,
                            slant_range: slant_km, elev_angle: e, altitude: alt,
                        });
                    }
                }
            }

            current_pos += 82;
            line_count += 1;
        }

        search_start = actual_pos + marker.len();
        if search_start >= data.len() { break; }
    }

    if records.is_empty() { return Ok(None); }

    // Sort by altitude (all pages merged and sorted together)
    records.sort_by(|a, b| a.altitude.partial_cmp(&b.altitude).unwrap_or(std::cmp::Ordering::Equal));

    let dict = PyDict::new_bound(py);
    let mut wind_dir = Vec::with_capacity(records.len());
    let mut wind_spd = Vec::with_capacity(records.len());
    let mut rms_error = Vec::with_capacity(records.len());
    let mut divergence = Vec::with_capacity(records.len());
    let mut slant_range = Vec::with_capacity(records.len());
    let mut elev_angle = Vec::with_capacity(records.len());
    let mut altitude = Vec::with_capacity(records.len());

    for r in records {
        wind_dir.push(r.wind_dir); wind_spd.push(r.wind_spd); rms_error.push(r.rms_error);
        divergence.push(r.divergence); slant_range.push(r.slant_range);
        elev_angle.push(r.elev_angle); altitude.push(r.altitude);
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
    let geo_polys: Vec<Polygon<f64>> = polygons.into_iter()
        .filter(|p| p.len() >= 3)
        .map(|p| {
            let coords: Vec<Coord<f64>> = p.into_iter()
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
        if dir_deg < 0.0 { dir_deg + 360.0 } else { dir_deg }
    } else {
        0.0
    };
    Ok((dir, spd))
}

fn clip_profile(wind_dir: &[f64], wind_spd: &[f64], altitude: &[f64], max_hght: f64) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
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
    if x.is_empty() { return f64::NAN; }
    if xi <= x[0] { return if x[0].is_nan() { f64::NAN } else { y[0] }; }
    if xi >= x[x.len() - 1] { return if x[x.len() - 1].is_nan() { f64::NAN } else { y[y.len() - 1] }; }
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
    if wind_dir.is_empty() { return Ok(((f64::NAN, f64::NAN), (f64::NAN, f64::NAN), (f64::NAN, f64::NAN))); }

    let (clipped_dir, clipped_spd, _clipped_alt) = clip_profile(&wind_dir, &wind_spd, &altitude, 6000.0);
    if clipped_dir.is_empty() { return Ok(((f64::NAN, f64::NAN), (f64::NAN, f64::NAN), (f64::NAN, f64::NAN))); }

    // Compute mean wind 0-6km: average of all clipped levels
    let mean_dir = clipped_dir.iter().sum::<f64>() / clipped_dir.len() as f64;
    let mean_spd = clipped_spd.iter().sum::<f64>() / clipped_spd.len() as f64;

    let (u_mean, v_mean) = {
        let wdir_rad = mean_dir.to_radians();
        (-mean_spd * wdir_rad.sin(), -mean_spd * wdir_rad.cos())
    };

    // Shear vector: (u_top - u_bot, v_top - v_bot)
    let u_bot = if !clipped_dir.is_empty() {
        let wd = clipped_dir[0].to_radians();
        -clipped_spd[0] * wd.sin()
    } else { 0.0 };
    let v_bot = if !clipped_dir.is_empty() {
        let wd = clipped_dir[0].to_radians();
        -clipped_spd[0] * wd.cos()
    } else { 0.0 };

    let u_top = if clipped_dir.len() > 1 {
        let wd = clipped_dir[clipped_dir.len() - 1].to_radians();
        -clipped_spd[clipped_spd.len() - 1] * wd.sin()
    } else { u_bot };
    let v_top = if clipped_dir.len() > 1 {
        let wd = clipped_dir[clipped_dir.len() - 1].to_radians();
        -clipped_spd[clipped_spd.len() - 1] * wd.cos()
    } else { v_bot };

    let shear_u = u_top - u_bot;
    let shear_v = v_top - v_bot;
    let shear_mag = (shear_u * shear_u + shear_v * shear_v).sqrt();

    let displacement = 7.5 * 1.94; // 7.5 kts in m/s
    let (du, dv) = if shear_mag > 0.01 {
        let scale = displacement / shear_mag;
        (scale * shear_v, -scale * shear_u)
    } else {
        (0.0, 0.0)
    };

    let (rleft_u, rleft_v) = (u_mean - du, v_mean - dv);
    let (rright_u, rright_v) = (u_mean + du, v_mean + dv);

    let right_dir = if rleft_u.abs() < 0.1 && rleft_v.abs() < 0.1 { 0.0 } else {
        let angle = (-rleft_v).atan2(-rleft_u).to_degrees();
        ((90.0 - angle) % 360.0 + 360.0) % 360.0
    };
    let right_spd = (rleft_u * rleft_u + rleft_v * rleft_v).sqrt();

    let left_dir = if rright_u.abs() < 0.1 && rright_v.abs() < 0.1 { 0.0 } else {
        let angle = (-rright_v).atan2(-rright_u).to_degrees();
        ((90.0 - angle) % 360.0 + 360.0) % 360.0
    };
    let left_spd = (rright_u * rright_u + rright_v * rright_v).sqrt();

    let mean_dir_result = if u_mean.abs() < 0.1 && v_mean.abs() < 0.1 { 0.0 } else {
        let angle = (-v_mean).atan2(-u_mean).to_degrees();
        ((90.0 - angle) % 360.0 + 360.0) % 360.0
    };

    Ok(((mean_dir_result, mean_spd), (left_dir, left_spd), (right_dir, right_spd)))
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
    if wind_dir.is_empty() { return Ok(f64::NAN); }

    let (clipped_dir, clipped_spd, clipped_alt) = clip_profile(&wind_dir, &wind_spd, &altitude, hght_km * 1000.0);
    if clipped_dir.is_empty() { return Ok(f64::NAN); }

    let storm_rad = storm_dir.to_radians();
    let (storm_u, storm_v) = (-storm_spd * storm_rad.sin(), -storm_spd * storm_rad.cos());

    let mut srh = 0.0;
    let mut prev_u = f64::NAN;
    let mut prev_v = f64::NAN;
    let mut prev_alt = f64::NAN;

    for i in 0..clipped_dir.len() {
        let wd = clipped_dir[i].to_radians();
        let u = -clipped_spd[i] * wd.sin();
        let v = -clipped_spd[i] * wd.cos();
        let sr_u = (u - storm_u) / 1.94; // Convert to m/s
        let sr_v = (v - storm_v) / 1.94;

        if !prev_u.is_nan() && !prev_alt.is_nan() {
            let dalt = (clipped_alt[i] - prev_alt) * 0.001; // km
            let cross = prev_u * sr_v - prev_v * sr_u;
            srh += cross * dalt;
        }

        prev_u = sr_u;
        prev_v = sr_v;
        prev_alt = clipped_alt[i];
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
    if wind_dir.is_empty() { return Ok(f64::NAN); }

    let (clipped_dir, clipped_spd, _) = clip_profile(&wind_dir, &wind_spd, &altitude, 6000.0);
    if clipped_dir.is_empty() { return Ok(f64::NAN); }

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
        let office = if office_raw.len() == 3 && office_raw.chars().next().unwrap_or('Z').is_ascii_uppercase() {
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
    items: Vec<(String, Vec<u8>)>
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
