use pyo3::prelude::*;
use xxhash_rust::xxh3;

/// A Python module implemented in Rust for spc-bot.
#[pymodule]
fn spc_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sum_as_string, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_fast_hash, m)?)?;
    m.add_function(wrap_pyfunction!(find_vwp_header_offset, m)?)?;
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
    
    // Scan up to 200 bytes as in the original Python logic
    let search_limit = std::cmp::min(data.len() - 2, 200);
    
    for i in 0..=search_limit {
        // Look for Product Code 48 (0x0030)
        if data[i] == 0x00 && data[i+1] == 0x30 {
            // Potential match. Verify if the Message Code at -30 bytes is also 48.
            if i >= 30 {
                if data[i-30] == 0x00 && data[i-30+1] == 0x30 {
                    return Ok(Some(i));
                }
            }
        }
    }
    Ok(None)
}
