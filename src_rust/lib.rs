#![allow(
    clippy::collapsible_if,
    clippy::manual_range_contains,
    clippy::type_complexity
)]

use futures::stream::StreamExt;
use geo::algorithm::contains::Contains;
use geo::{Coord, Point, Polygon};
use minidom::Element;
use nom::bytes::complete::tag_no_case;
use nom::character::complete::{digit1, multispace0, multispace1};
use nom::error::Error as NomError;
use nom::Parser as NomParser;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;
use rstar::RTree;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use tokio::sync::mpsc;
use xmpp_parsers::message::Message as XmppMessage;
use xmpp_parsers::presence::{Presence, Type as PresenceType};
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
    m.add_function(wrap_pyfunction!(parse_warning_polygon, m)?)?;
    m.add_function(wrap_pyfunction!(parse_md_number, m)?)?;
    m.add_function(wrap_pyfunction!(parse_watch_number, m)?)?;
    m.add_function(wrap_pyfunction!(nwws_start, m)?)?;
    m.add_function(wrap_pyfunction!(nwws_stop, m)?)?;
    m.add_function(wrap_pyfunction!(nwws_try_recv, m)?)?;
    m.add_function(wrap_pyfunction!(nwws_is_connected, m)?)?;
    m.add_function(wrap_pyfunction!(nwws_stats, m)?)?;
    m.add_function(wrap_pyfunction!(validate_image_cache_batch, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_product_id, m)?)?;
    m.add_function(wrap_pyfunction!(haversine, m)?)?;
    m.add_function(wrap_pyfunction!(haversine_batch, m)?)?;
    m.add_function(wrap_pyfunction!(find_nearest_stations_batch, m)?)?;
    m.add_function(wrap_pyfunction!(points_in_polygon_counts, m)?)?;
    m.add_function(wrap_pyfunction!(points_in_polygon_lookup, m)?)?;
    m.add_function(wrap_pyfunction!(compute_shear_mag, m)?)?;
    m.add_function(wrap_pyfunction!(compute_sr_flow, m)?)?;
    m.add_function(wrap_pyfunction!(clip_profile, m)?)?;
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

    let dict = PyDict::new(py);
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
    let mut index = RADAR_INDEX.write().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("RADAR_INDEX lock poisoned: {e}"))
    })?;
    *index = Some(RTree::bulk_load(points));
    Ok(())
}

#[pyfunction]
fn find_nearest_radar(lat: f64, lon: f64) -> PyResult<Option<String>> {
    let index_lock = RADAR_INDEX.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("RADAR_INDEX lock poisoned: {e}"))
    })?;
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

fn _clip_profile_internal(
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
    if x.is_empty() || xi.is_nan() {
        return f64::NAN;
    }
    // Match numpy.interp(left=nan, right=nan)
    if xi < x[0] || xi > x[x.len() - 1] {
        return f64::NAN;
    }
    if xi == x[0] {
        return y[0];
    }
    if xi == x[x.len() - 1] {
        return y[y.len() - 1];
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

    let (clipped_dir, clipped_spd, _) =
        _clip_profile_internal(&wind_dir, &wind_spd, &altitude, 6000.0);
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

        let result = PyDict::new(py);
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

// ── B1 Workstream: nom 8.0.0 Parsers ─────────────────────────────────────────

fn scan_to_ci<'a>(needle: &str, input: &'a str) -> Option<&'a str> {
    let nlen = needle.len();
    let needle_lower: String = needle.chars().flat_map(|c| c.to_lowercase()).collect();

    for (byte_pos, _) in input.char_indices() {
        let slice = &input[byte_pos..];
        if slice.len() < nlen {
            break;
        }
        let candidate: String = slice[..nlen]
            .chars()
            .flat_map(|c| c.to_lowercase())
            .collect();
        if candidate == needle_lower {
            return Some(&input[byte_pos + nlen..]);
        }
    }
    None
}

#[pyfunction]
fn parse_warning_polygon(text: &str) -> PyResult<Vec<(f64, f64)>> {
    let after_tag = match scan_to_ci("lat...lon", text) {
        Some(rest) => rest,
        None => return Ok(vec![]),
    };

    let (mut cursor, _) = multispace0::<&str, NomError<&str>>
        .parse(after_tag)
        .map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("parse_warning_polygon: {e}"))
        })?;

    let mut raw_ints: Vec<u32> = Vec::with_capacity(32);

    loop {
        let peeked = cursor.trim_start_matches([' ', '\t', '\r', '\n']);

        let stop = match peeked.chars().next() {
            None => true,
            Some(c) => c.is_ascii_uppercase() || c == '$' || c == '\0',
        };
        if stop {
            break;
        }

        let parse_result =
            nom::sequence::preceded(multispace0, digit1::<&str, NomError<&str>>).parse(cursor);

        match parse_result {
            Ok((rest, s)) => match s.parse::<u32>() {
                Ok(n) => {
                    raw_ints.push(n);
                    cursor = rest;
                }
                Err(_) => break,
            },
            Err(_) => break,
        }
    }

    let mut coords: Vec<(f64, f64)> = Vec::with_capacity(raw_ints.len() / 2);
    let mut i = 0;
    while i + 1 < raw_ints.len() {
        let lat = raw_ints[i] as f64 / 100.0;
        let lon = -(raw_ints[i + 1] as f64 / 100.0);
        i += 2;
        if lat >= 15.0 && lat <= 75.0 && lon >= -170.0 && lon <= -60.0 {
            coords.push((lat, lon));
        }
    }

    Ok(coords)
}

#[pyfunction]
fn parse_md_number(text: &str) -> PyResult<Option<String>> {
    let after_tag = match scan_to_ci("mesoscale discussion", text) {
        Some(rest) => rest,
        None => return Ok(None),
    };

    let after_ws = match multispace1::<&str, NomError<&str>>.parse(after_tag) {
        Ok((rest, _)) => rest,
        Err(_) => return Ok(None),
    };

    let digits = match digit1::<&str, NomError<&str>>.parse(after_ws) {
        Ok((_, d)) => d,
        Err(_) => return Ok(None),
    };

    Ok(Some(format!("{:0>4}", digits)))
}

#[pyfunction]
fn parse_watch_number(text: &str) -> PyResult<Option<(String, String)>> {
    let lower = text.to_ascii_lowercase();
    let tornado_pos = lower.find("tornado watch");
    let svr_pos = lower.find("severe thunderstorm watch");

    let (phrase_byte_end, watch_type_str): (usize, &str) = match (tornado_pos, svr_pos) {
        (None, None) => return Ok(None),
        (Some(tp), None) => (tp + "tornado watch".len(), "TORNADO"),
        (None, Some(sp)) => (sp + "severe thunderstorm watch".len(), "SVR"),
        (Some(tp), Some(sp)) => {
            if tp <= sp {
                (tp + "tornado watch".len(), "TORNADO")
            } else {
                (sp + "severe thunderstorm watch".len(), "SVR")
            }
        }
    };

    let after_watch = &text[phrase_byte_end..];

    let (after_ws0, _) = multispace0::<&str, NomError<&str>>
        .parse(after_watch)
        .map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("parse_watch_number ws0: {e}"))
        })?;

    let (after_number, _) =
        match tag_no_case::<&str, &str, NomError<&str>>("number").parse(after_ws0) {
            Ok(r) => r,
            Err(_) => return Ok(None),
        };

    let (after_ws1, _) = match multispace1::<&str, NomError<&str>>.parse(after_number) {
        Ok(r) => r,
        Err(_) => return Ok(None),
    };

    let (_, digits) = match digit1::<&str, NomError<&str>>.parse(after_ws1) {
        Ok(r) => r,
        Err(_) => return Ok(None),
    };

    let padded = format!("{:0>4}", digits);
    Ok(Some((padded, watch_type_str.to_string())))
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

fn linspace(start: f64, end: f64, n: usize) -> Vec<f64> {
    if n == 0 {
        return Vec::new();
    }
    if n == 1 {
        return vec![start];
    }
    let step = (end - start) / (n - 1) as f64;
    (0..n).map(|i| start + i as f64 * step).collect()
}

#[pyfunction]
fn compute_shear_mag(
    wind_dir: Vec<f64>,
    wind_spd: Vec<f64>,
    altitude: Vec<f64>,
    hght: f64,
) -> PyResult<f64> {
    if wind_dir.is_empty() || altitude.is_empty() {
        return Ok(f64::NAN);
    }

    let mut u_all = Vec::with_capacity(wind_dir.len());
    let mut v_all = Vec::with_capacity(wind_dir.len());
    for i in 0..wind_dir.len() {
        let (u, v) = vec2comp(wind_dir[i], wind_spd[i])?;
        u_all.push(u);
        v_all.push(v);
    }

    let u_hght = _interp_linear(&altitude, &u_all, hght);
    let v_hght = _interp_linear(&altitude, &v_all, hght);

    if u_hght.is_nan() || v_hght.is_nan() {
        return Ok(f64::NAN);
    }

    let du = u_hght - u_all[0];
    let dv = v_hght - v_all[0];
    Ok((du * du + dv * dv).sqrt())
}

#[pyfunction]
fn compute_sr_flow(
    wind_dir: Vec<f64>,
    wind_spd: Vec<f64>,
    altitude: Vec<f64>,
    storm_dir: f64,
    storm_spd: f64,
    hght_bot: f64,
    hght_top: f64,
) -> PyResult<f64> {
    if wind_dir.is_empty() || altitude.is_empty() {
        return Ok(f64::NAN);
    }

    let mut u_all = Vec::with_capacity(wind_dir.len());
    let mut v_all = Vec::with_capacity(wind_dir.len());
    for i in 0..wind_dir.len() {
        let (u, v) = vec2comp(wind_dir[i], wind_spd[i])?;
        u_all.push(u);
        v_all.push(v);
    }

    let (storm_u, storm_v) = vec2comp(storm_dir, storm_spd)?;

    let n = 50;
    let layer_alts = linspace(hght_bot, hght_top, n);

    let mut sum_mag = 0.0;
    let mut count = 0;

    for alt in layer_alts {
        let u_interp = _interp_linear(&altitude, &u_all, alt);
        let v_interp = _interp_linear(&altitude, &v_all, alt);

        if u_interp.is_finite() && v_interp.is_finite() {
            let sr_u = u_interp - storm_u;
            let sr_v = v_interp - storm_v;
            sum_mag += (sr_u * sr_u + sr_v * sr_v).sqrt();
            count += 1;
        }
    }

    if count == 0 {
        Ok(f64::NAN)
    } else {
        Ok(sum_mag / count as f64)
    }
}

#[pyfunction]
fn clip_profile(
    prof: Vec<f64>,
    alt: Vec<f64>,
    clip_alt: f64,
    intrp_prof: f64,
) -> PyResult<Vec<f64>> {
    if alt.len() < 2 {
        return Ok(vec![f64::NAN; prof.len()]);
    }

    let mut idx_clip = None;
    for i in 0..(alt.len() - 1) {
        if alt[i] <= clip_alt && alt[i + 1] > clip_alt {
            idx_clip = Some(i);
            break;
        }
    }

    match idx_clip {
        Some(idx) => {
            let mut result = prof[0..=idx].to_vec();
            result.push(intrp_prof);
            Ok(result)
        }
        None => Ok(vec![f64::NAN; prof.len()]),
    }
}

#[pyfunction]
fn find_nearest_stations_batch(
    lat: f64,
    lon: f64,
    targets: Vec<(f64, f64)>,
    n: usize,
) -> PyResult<Vec<(usize, f64)>> {
    let mut distances: Vec<(usize, f64)> = Vec::with_capacity(targets.len());
    for (i, (t_lat, t_lon)) in targets.into_iter().enumerate() {
        distances.push((i, haversine(lat, lon, t_lat, t_lon)?));
    }

    // Sort by distance
    distances.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));

    // Truncate to N
    if distances.len() > n {
        distances.truncate(n);
    }

    Ok(distances)
}

#[pyfunction]
fn points_in_polygon_counts(
    points: Vec<(f64, f64)>,
    polygons: Vec<Vec<(f64, f64)>>,
) -> PyResult<Vec<usize>> {
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

    let mut counts = vec![0; geo_polys.len()];
    for (lat, lon) in points {
        let p = Point::new(lon, lat);
        for (i, poly) in geo_polys.iter().enumerate() {
            if poly.contains(&p) {
                counts[i] += 1;
            }
        }
    }
    Ok(counts)
}

#[pyfunction]
fn points_in_polygon_lookup(
    points: Vec<(f64, f64)>,
    polygons: Vec<Vec<(f64, f64)>>,
) -> PyResult<Vec<Option<usize>>> {
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

    let mut lookup = Vec::with_capacity(points.len());
    for (lat, lon) in points {
        let p = Point::new(lon, lat);
        let mut found = None;
        for (i, poly) in geo_polys.iter().enumerate() {
            if poly.contains(&p) {
                found = Some(i);
                break;
            }
        }
        lookup.push(found);
    }
    Ok(lookup)
}

// ── Phase 3: Rust tokio XMPP Sidecar ────────────────────────────────────────

/// NwwsMessage represents a single NWWS product received from the XMPP stream.
#[derive(Clone, Debug)]
pub struct NwwsMessage {
    pub office: String,
    pub ttaaii: String,
    pub awipsid: String,
    pub issue: String,
    pub raw_text: String,
}

/// NwwsState manages the tokio runtime, message channel, and connection status.
struct NwwsState {
    _runtime_handle: tokio::runtime::Runtime,
    _sender: mpsc::UnboundedSender<NwwsMessage>,
    receiver: Arc<std::sync::Mutex<mpsc::UnboundedReceiver<NwwsMessage>>>,
    is_connected: Arc<AtomicBool>,
    messages_received: Arc<AtomicU64>,
    messages_filtered: Arc<AtomicU64>,
    reconnect_count: Arc<AtomicU64>,
    last_error: Arc<RwLock<String>>,
}

static NWWS_STATE: Lazy<RwLock<Option<NwwsState>>> = Lazy::new(|| RwLock::new(None));

/// Helper: extract NWWS message from XMPP message stanza.
///
/// NWWS-OI products carry their metadata in an `<x xmlns="nwws-oi">` payload
/// with `cccc` (office), `ttaaii` (WMO header), `awipsid` (AFOS PIL), and
/// `issue` (timestamp) attributes. The product text is the body of that
/// element (not the message body, which iembot leaves blank or summary-only).
///
/// Messages without an `nwws-oi` payload are MUC chatter / status pings and
/// are skipped silently — they aren't products.
fn parse_xmpp_message(msg: &XmppMessage) -> Option<NwwsMessage> {
    // Locate the <x xmlns="nwws-oi"> payload. The namespace iembot uses is
    // literally "nwws-oi" (no URI scheme).
    let nwws_payload = msg
        .payloads
        .iter()
        .find(|p| p.name() == "x" && p.ns() == "nwws-oi")?;

    let office = nwws_payload.attr("cccc")?.trim().to_string();
    let ttaaii = nwws_payload.attr("ttaaii")?.trim().to_string();
    let awipsid = nwws_payload
        .attr("awipsid")
        .unwrap_or("")
        .trim()
        .to_string();
    let issue = nwws_payload.attr("issue").unwrap_or("").trim().to_string();

    // Product text lives inside the <x> element body. Fall back to message
    // body for older feeds that put it there.
    let raw_text = {
        let inner = nwws_payload.text();
        if inner.trim().is_empty() {
            msg.bodies
                .iter()
                .next()
                .map(|(_lang, body)| body.0.clone())
                .unwrap_or_default()
        } else {
            inner
        }
    };

    if office.is_empty() || ttaaii.is_empty() {
        return None;
    }

    Some(NwwsMessage {
        office,
        ttaaii,
        awipsid,
        issue,
        raw_text,
    })
}

/// Type alias for the default ServerConfig-based AsyncClient.
type XmppClient = tokio_xmpp::starttls::StartTlsAsyncClient;

/// Async function: connects to NWWS XMPP server and joins the MUC room.
/// Returns a tokio_xmpp Client on success.
async fn join_muc(user: &str, password: &str, server: &str) -> Result<XmppClient, String> {
    use std::str::FromStr;

    let jid_str = format!("{}@{}", user, server);
    let room_str = format!("nwws@conference.{}", server);
    let nick = user.to_string();

    eprintln!("[XMPP] Connecting to {} as {}", server, jid_str);

    // Create XMPP client using tokio-xmpp
    // The Jid type must be parsed from a string
    let jid = xmpp_parsers::jid::Jid::from_str(&jid_str)
        .map_err(|e| format!("Invalid JID '{}': {}", jid_str, e))?;

    let mut client = XmppClient::new(jid, password.to_owned());
    client.set_reconnect(true);

    eprintln!("[XMPP] Async client created, waiting for online event...");

    // Wait for online event (the connection is established)
    let mut online = false;
    let mut attempts = 0;
    const MAX_ATTEMPTS: u32 = 50; // 5 seconds max wait (50 * 100ms)

    while !online && attempts < MAX_ATTEMPTS {
        if let Some(event) =
            tokio::time::timeout(tokio::time::Duration::from_millis(100), client.next())
                .await
                .ok()
                .flatten()
        {
            if event.is_online() {
                online = true;
                eprintln!("[XMPP] Client online");
                break;
            }
        }
        attempts += 1;
    }

    if !online {
        return Err("Timeout waiting for XMPP online event".to_string());
    }

    // Send presence stanza to join the MUC room
    // Must address presence to room@conference.server/nick to properly join MUC
    use xmpp_parsers::jid::FullJid;

    let room_jid_str = format!("{}/{}", room_str, nick);
    let room_jid = FullJid::from_str(&room_jid_str)
        .map_err(|e| format!("Invalid room JID '{}': {}", room_jid_str, e))?;

    let mut presence = Presence::new(PresenceType::None);
    presence.to = Some(room_jid.into());

    // Convert Presence to Element via Into trait
    let presence_element: Element = presence.into();

    // Send the presence stanza
    client
        .send_stanza(presence_element)
        .await
        .map_err(|e| format!("Failed to send presence stanza: {}", e))?;

    eprintln!("[XMPP] Presence stanza sent to {}/{}", room_str, nick);

    // Wait briefly for room join confirmation
    tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

    Ok(client)
}

/// Internal async loop: maintains XMPP connection, receives messages, applies filters, forwards to channel.
#[allow(clippy::too_many_arguments)]
async fn nwws_connection_loop(
    user: String,
    password: String,
    server: String,
    is_connected: Arc<AtomicBool>,
    tx: mpsc::UnboundedSender<NwwsMessage>,
    messages_received: Arc<AtomicU64>,
    messages_filtered: Arc<AtomicU64>,
    reconnect_count: Arc<AtomicU64>,
    last_error: Arc<RwLock<String>>,
) {
    let mut backoff_ms = 1000u64;
    const MAX_BACKOFF_MS: u64 = 60000;

    loop {
        eprintln!("[XMPP] Attempting connection to {}", server);

        // Attempt join_muc
        match join_muc(&user, &password, &server).await {
            Ok(mut client) => {
                is_connected.store(true, Ordering::Relaxed);
                eprintln!("[XMPP] Connected successfully, starting message loop");
                backoff_ms = 1000; // Reset backoff on success

                // Message loop: receive events from client and process them
                let mut connection_error = false;
                while !connection_error {
                    match tokio::time::timeout(tokio::time::Duration::from_secs(30), client.next())
                        .await
                    {
                        Ok(Some(event)) => {
                            // Process event - check if it's a stanza
                            if let Some(stanza) = event.into_stanza() {
                                // Try to parse as message
                                if let Ok(message) = XmppMessage::try_from(stanza.clone()) {
                                    messages_received.fetch_add(1, Ordering::Relaxed);

                                    // Parse NWWS message
                                    if let Some(nwws_msg) = parse_xmpp_message(&message) {
                                        eprintln!(
                                            "[XMPP] Received: {} {} {}",
                                            nwws_msg.office, nwws_msg.ttaaii, nwws_msg.awipsid
                                        );

                                        // Send to channel
                                        if tx.send(nwws_msg).is_ok() {
                                            messages_filtered.fetch_add(1, Ordering::Relaxed);
                                        } else {
                                            eprintln!("[XMPP] Channel receiver dropped");
                                            connection_error = true;
                                        }
                                    }
                                }
                            }
                        }
                        Ok(None) => {
                            eprintln!("[XMPP] Connection closed by server");
                            connection_error = true;
                        }
                        Err(_) => {
                            // Timeout is normal, keep the connection alive
                            eprintln!("[XMPP] Heartbeat timeout (expected), continuing...");
                        }
                    }
                }

                is_connected.store(false, Ordering::Relaxed);
                eprintln!("[XMPP] Message loop ended, reconnecting...");
            }
            Err(e) => {
                eprintln!("[XMPP] Connection failed: {}", e);
                if let Ok(mut last_err) = last_error.write() {
                    *last_err = e.clone();
                }
                reconnect_count.fetch_add(1, Ordering::Relaxed);
            }
        }

        // Exponential backoff
        eprintln!("[XMPP] Reconnecting in {}ms...", backoff_ms);
        tokio::time::sleep(tokio::time::Duration::from_millis(backoff_ms)).await;
        backoff_ms = std::cmp::min(backoff_ms * 2, MAX_BACKOFF_MS);
    }
}

#[pyfunction]
fn nwws_start(user: &str, password: &str, server: &str) -> PyResult<()> {
    let user_str = user.to_string();
    let password_str = password.to_string();
    let server_str = server.to_string();

    // Create unbounded channel for async message forwarding
    let (tx, rx) = mpsc::unbounded_channel::<NwwsMessage>();

    // Counters and state
    let is_connected = Arc::new(AtomicBool::new(false));
    let messages_received = Arc::new(AtomicU64::new(0));
    let messages_filtered = Arc::new(AtomicU64::new(0));
    let reconnect_count = Arc::new(AtomicU64::new(0));
    let last_error = Arc::new(RwLock::new(String::new()));

    // Build tokio runtime with timers enabled for sleep/backoff
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("spc-xmpp")
        .enable_all()
        .build()
        .map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Failed to create tokio runtime: {e}"
            ))
        })?;

    // Clone arcs for async task
    let is_connected_task = Arc::clone(&is_connected);
    let messages_received_task = Arc::clone(&messages_received);
    let messages_filtered_task = Arc::clone(&messages_filtered);
    let reconnect_count_task = Arc::clone(&reconnect_count);
    let last_error_task = Arc::clone(&last_error);
    let tx_task = tx.clone();

    // Spawn connection loop on runtime
    runtime.spawn(nwws_connection_loop(
        user_str,
        password_str,
        server_str,
        is_connected_task,
        tx_task,
        messages_received_task,
        messages_filtered_task,
        reconnect_count_task,
        last_error_task,
    ));

    // Store state
    let mut state = NWWS_STATE.write().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    *state = Some(NwwsState {
        _runtime_handle: runtime,
        _sender: tx,
        receiver: Arc::new(std::sync::Mutex::new(rx)),
        is_connected,
        messages_received,
        messages_filtered,
        reconnect_count,
        last_error,
    });

    Ok(())
}

#[pyfunction]
fn nwws_stop() -> PyResult<()> {
    let mut state = NWWS_STATE.write().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;
    *state = None;
    Ok(())
}

#[pyfunction]
fn nwws_try_recv<'py>(py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let state = NWWS_STATE.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    if state.is_none() {
        return Ok(None);
    }

    let state = state.as_ref().unwrap();
    let mut receiver = state.receiver.lock().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("receiver lock poisoned: {e}"))
    })?;

    match receiver.try_recv() {
        Ok(msg) => {
            state.messages_received.fetch_add(1, Ordering::Relaxed);
            let dict = PyDict::new(py);
            dict.set_item("office", msg.office)?;
            dict.set_item("ttaaii", msg.ttaaii)?;
            dict.set_item("awipsid", msg.awipsid)?;
            dict.set_item("issue", msg.issue)?;
            dict.set_item("raw_text", msg.raw_text)?;
            Ok(Some(dict))
        }
        Err(mpsc::error::TryRecvError::Empty) => Ok(None),
        Err(mpsc::error::TryRecvError::Disconnected) => Err(
            pyo3::exceptions::PyRuntimeError::new_err("XMPP channel disconnected"),
        ),
    }
}

#[pyfunction]
fn nwws_is_connected() -> PyResult<bool> {
    let state = NWWS_STATE.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    if let Some(s) = state.as_ref() {
        Ok(s.is_connected.load(Ordering::Relaxed))
    } else {
        Ok(false)
    }
}

#[pyfunction]
fn nwws_stats<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
    let state = NWWS_STATE.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    let dict = PyDict::new(py);

    if let Some(s) = state.as_ref() {
        let msg_count = s.messages_received.load(Ordering::Relaxed);
        let reconnect_ct = s.reconnect_count.load(Ordering::Relaxed);
        let last_err = s.last_error.read().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("last_error lock poisoned: {e}"))
        })?;

        dict.set_item("messages_received", msg_count)?;
        dict.set_item(
            "messages_filtered",
            s.messages_filtered.load(Ordering::Relaxed),
        )?;
        dict.set_item("reconnect_count", reconnect_ct)?;
        dict.set_item("last_error", last_err.clone())?;
        dict.set_item("is_connected", s.is_connected.load(Ordering::Relaxed))?;
    } else {
        dict.set_item("messages_received", 0)?;
        dict.set_item("messages_filtered", 0)?;
        dict.set_item("reconnect_count", 0)?;
        dict.set_item("last_error", "")?;
        dict.set_item("is_connected", false)?;
    }

    Ok(dict)
}

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
            _clip_profile_internal(&wind_dir, &wind_spd, &altitude, 5000.0);
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
