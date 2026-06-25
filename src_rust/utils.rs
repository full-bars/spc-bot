use pyo3::prelude::*;
use xxhash_rust::xxh3;

// ── sum_as_string ──

#[pyfunction]
pub fn sum_as_string(a: usize, b: usize) -> PyResult<String> {
    Ok((a + b).to_string())
}

// ── calculate_fast_hash ──

#[pyfunction]
pub fn calculate_fast_hash(data: &[u8]) -> PyResult<String> {
    let hash = xxh3::xxh3_64(data);
    Ok(format!("{:016x}", hash))
}

// ── validate_image_cache_batch ──

#[pyfunction]
pub fn validate_image_cache_batch(
    items: Vec<(String, Vec<u8>)>,
) -> PyResult<Vec<(String, String, bool)>> {
    let mut results = Vec::with_capacity(items.len());
    for (url, content) in items {
        let hash_hex = format!("{:016x}", xxh3::xxh3_64(&content));

        let mut is_placeholder = false;
        if content.len() < 2048 {
            is_placeholder = true;
        } else {
            let has_magic = content.starts_with(b"\x89PNG\r\n\x1a\n")
                || content.starts_with(b"GIF87a")
                || content.starts_with(b"GIF89a")
                || content.starts_with(b"\xff\xd8\xff")
                || (content.starts_with(b"RIFF")
                    && content.len() > 12
                    && &content[8..12] == b"WEBP")
                || content.starts_with(b"BM")
                || {
                    let mut start = 0;
                    while start < content.len() && content[start].is_ascii_whitespace() {
                        start += 1;
                    }
                    content[start..].starts_with(b"<svg") || content[start..].starts_with(b"<?xml")
                };

            if !has_magic
                || (content.len() >= 6
                    && content.starts_with(b"GIF")
                    && *content.last().unwrap() != 0x3B)
            {
                is_placeholder = true;
            }
        }

        results.push((url, hash_hex, is_placeholder));
    }
    Ok(results)
}

// ── normalize_product_id ──

#[pyfunction]
pub fn normalize_product_id(
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
