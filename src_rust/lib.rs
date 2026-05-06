use pyo3::prelude::*;
use pyo3::types::PyDict;
use xxhash_rust::xxh3;

/// A Python module implemented in Rust for spc-bot.
#[pymodule]
fn spc_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sum_as_string, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_fast_hash, m)?)?;
    m.add_function(wrap_pyfunction!(find_vwp_header_offset, m)?)?;
    m.add_function(wrap_pyfunction!(parse_vwp_tabular_data, m)?)?;
    Ok(())
}

/// Formats the sum of two numbers as string.
#[pyfunction]
fn sum_as_string(a: usize, b: usize) -> PyResult<String> {
    Ok((a + b).to_string())
}

/// Calculate a high-performance XXH3 hash of a byte array and return as a hex string.
/// XXH3 is optimized for modern CPUs and is significantly faster than SHA256.
#[pyfunction]
fn calculate_fast_hash(data: &[u8]) -> PyResult<String> {
    let hash = xxh3::xxh3_64(data);
    Ok(format!("{:016x}", hash))
}

/// Locate the NIDS Product 48 (VWP) message header offset.
/// Ported from lib/vad_plotter/vad_reader.py for performance.
#[pyfunction]
fn find_vwp_header_offset(data: &[u8]) -> PyResult<Option<usize>> {
    if data.len() < 2 {
        return Ok(None);
    }
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
#[pyfunction]
fn parse_vwp_tabular_data<'py>(
    py: Python<'py>,
    data: &[u8],
    offset_tabular: usize,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    if offset_tabular == 0 || offset_tabular >= data.len() {
        return Ok(None);
    }

    let block_data = &data[offset_tabular..];
    if block_data.len() < 8 { return Ok(None); }
    
    let block_str = String::from_utf8_lossy(block_data);
    let marker = "VAD Algorithm Output";
    
    let mut records = Vec::new();

    if let Some(start_idx) = block_str.find(marker) {
        let table_section = &block_str[start_idx..];
        for line in table_section.lines().skip(2) {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 10 { continue; }
            
            let dir = parts[4].parse::<f64>().ok();
            let spd = parts[5].parse::<f64>().ok();
            let rms = parts[6].parse::<f64>().ok();
            let div = if parts[7] == "NA" { Some(f64::NAN) } else { parts[7].parse::<f64>().ok() };
            let slant = parts[8].parse::<f64>().ok();
            let elev = parts[9].parse::<f64>().ok();
            
            if let (Some(d), Some(s), Some(r), Some(v), Some(sl), Some(e)) = (dir, spd, rms, div, slant, elev) {
                // Calculate altitude
                let slant_km = sl * (6067.1 / 3281.0);
                let r_e = (4.0 / 3.0) * 6371.0;
                let elev_rad = e.to_radians();
                let alt = (r_e.powi(2) + slant_km.powi(2) + 2.0 * r_e * slant_km * elev_rad.sin()).sqrt() - r_e;
                
                records.push(VadRecord {
                    wind_dir: d,
                    wind_spd: s,
                    rms_error: r,
                    divergence: v,
                    slant_range: sl,
                    elev_angle: e,
                    altitude: alt,
                });
            }
        }
    }

    if records.is_empty() {
        return Ok(None);
    }

    // Sort by altitude (Python argsort logic)
    records.sort_by(|a, b| a.altitude.partial_cmp(&b.altitude).unwrap_or(std::cmp::Ordering::Equal));

    // Convert to PyDict
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
