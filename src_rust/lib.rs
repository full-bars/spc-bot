use pyo3::prelude::*;
use xxhash_rust::xxh3;

/// A Python module implemented in Rust for spc-bot.
#[pymodule]
fn spc_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sum_as_string, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_fast_hash, m)?)?;
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
