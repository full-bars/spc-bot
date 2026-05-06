use pyo3::prelude::*;
use pyo3::types::PyDict;
use xxhash_rust::xxh3;
use rstar::RTree;
use std::sync::RwLock;
use once_cell::sync::Lazy;
use geo::{Polygon, Point, Coord};
use geo::algorithm::contains::Contains;

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
        let mut page_has_data = false;

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
                        page_has_data = true;
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
