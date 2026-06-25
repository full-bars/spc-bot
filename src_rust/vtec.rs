use nom::bytes::complete::{tag, tag_no_case, take};
use nom::character::complete::{digit1, multispace0, multispace1};
use nom::error::Error as NomError;
use nom::Parser as NomParser;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;

// ── parse_vtec ──

#[pyfunction]
pub fn parse_vtec<'py>(py: Python<'py>, text: &str) -> PyResult<Option<Bound<'py, PyDict>>> {
    if text.is_empty() {
        return Ok(None);
    }

    // High-performance nom parser for VTEC string (NWS Directive 10-1703):
    // Example: /O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/
    //
    // Use tuple to match segments precisely without regex state machine overhead.
    fn vtec_nom(input: &str) -> nom::IResult<&str, (&str, &str, &str, &str, &str, &str, &str)> {
        let (input, _) = tag("/O.")(input)?;
        let (input, action) = take(3usize)(input)?;
        let (input, _) = tag(".")(input)?;
        let (input, office) = take(4usize)(input)?;
        let (input, _) = tag(".")(input)?;
        let (input, phenom) = take(2usize)(input)?;
        let (input, _) = tag(".")(input)?;
        let (input, sig) = take(1usize)(input)?;
        let (input, _) = tag(".")(input)?;
        let (input, etn) = take(4usize)(input)?;
        let (input, _) = tag(".")(input)?;
        let (input, start) = take(12usize)(input)?; // YYMMDDTHHMMZ
        let (input, _) = tag("-")(input)?;
        let (input, end) = take(12usize)(input)?;
        let (input, _) = tag("/")(input)?;
        Ok((input, (action, office, phenom, sig, etn, start, end)))
    }

    // Scan for the "/O." starting marker manually (fast) then attempt the full parse.
    let mut search_ptr = text;
    while let Some(pos) = search_ptr.find("/O.") {
        let candidate = &search_ptr[pos..];
        if candidate.len() < 47 {
            // Min VTEC length: /O.NEW.KOUN.TO.W.0042.YYMMDDTHHMMZ-YYMMDDTHHMMZ/ = 47 chars
            search_ptr = &candidate[3..];
            continue;
        }

        match vtec_nom(candidate) {
            Ok((_, (action, office_raw, phenom, sig, etn, start, end))) => {
                // Normalize office: if it's 4 chars, use as-is.
                // Special case: if it starts with a letter and is 3 chars, prepend K.
                // However, our take(4usize) already captures the 4-char string.
                // The Python logic checked if it's 3 chars. In VTEC it's always 4.
                // Let's mirror the normalization if needed.
                let office = if office_raw.len() == 3
                    && office_raw
                        .chars()
                        .next()
                        .unwrap_or('Z')
                        .is_ascii_uppercase()
                {
                    format!("K{}", office_raw)
                } else if office_raw.starts_with(' ') {
                    // Handle rare cases where office might be padded
                    office_raw.trim().to_string()
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
            Err(_) => {
                // Not a valid VTEC here, skip the "/O." and keep looking
                search_ptr = &candidate[3..];
            }
        }
    }

    Ok(None)
}

// ── scan_to_ci ──

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

// ── parse_warning_polygon ──

#[pyfunction]
pub fn parse_warning_polygon(text: &str) -> PyResult<Vec<(f64, f64)>> {
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

// ── NARRATIVE_HEADER_RE ──

static NARRATIVE_HEADER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^(?:BULLETIN.*|The National Weather Service\b.*)$").unwrap());

/// Port of Python's `_extract_narrative`.
///
/// Strips WMO/AFOS transmission headers and known footer tags from a raw VTEC
/// product, returning the human-readable warning body. Returns None for empty
/// or all-whitespace input.
#[pyfunction]
pub fn extract_narrative(raw: &str) -> PyResult<Option<String>> {
    if raw.trim().is_empty() {
        return Ok(None);
    }

    // Find the narrative start (BULLETIN or NWS office line).
    let text = if let Some(m) = NARRATIVE_HEADER_RE.find(raw) {
        &raw[m.start()..]
    } else {
        raw
    };

    // Strip everything from the first footer tag onwards (case-insensitive plain scan).
    let text_upper = text.to_uppercase();
    let footers = ["LAT...LON", "ATTN...WFO", "TIME...MOT...LOC", "$$"];
    let end = footers
        .iter()
        .filter_map(|f| text_upper.find(f))
        .min()
        .unwrap_or(text.len());

    let result = text[..end].trim();
    if result.is_empty() {
        Ok(None)
    } else {
        Ok(Some(result.to_string()))
    }
}

// ── AREA_STATE_SUFFIX_RE ──

static AREA_STATE_SUFFIX_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)[\s,]+(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|\
MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|\
ALABAMA|ALASKA|ARIZONA|ARKANSAS|CALIFORNIA|COLORADO|CONNECTICUT|DELAWARE|FLORIDA|GEORGIA|\
HAWAII|IDAHO|ILLINOIS|INDIANA|IOWA|KANSAS|KENTUCKY|LOUISIANA|MAINE|MARYLAND|MASSACHUSETTS|\
MICHIGAN|MINNESOTA|MISSISSIPPI|MISSOURI|MONTANA|NEBRASKA|NEVADA|NEW HAMPSHIRE|NEW JERSEY|\
NEW MEXICO|NEW YORK|NORTH CAROLINA|NORTH DAKOTA|OHIO|OKLAHOMA|OREGON|PENNSYLVANIA|\
RHODE ISLAND|SOUTH CAROLINA|SOUTH DAKOTA|TENNESSEE|TEXAS|UTAH|VERMONT|VIRGINIA|WASHINGTON|\
WEST VIRGINIA|WISCONSIN|WYOMING)$",
    )
    .unwrap()
});

// ── is_state_abbrev ──

/// True if `s` looks like a bare 2-letter state abbreviation (possibly with
/// surrounding whitespace/punctuation) — used to avoid splitting "Logan, OK"
/// into two counties.
pub(crate) fn is_state_abbrev(s: &str) -> bool {
    let t = s.trim();
    t.len() == 2 && t.chars().all(|c| c.is_ascii_uppercase())
}

// ── area_with_state ──

/// Port of Python's `_area_with_state`.
///
/// Groups county names from `area_desc` by the state indicated in each UGC
/// code (first 2 chars), cleaning trailing state suffixes and filtering
/// geographic garbage. Returns a formatted string like:
///   "Logan, Lincoln [OK]"  or  "Ashley, Chicot [AR] and Washington [MS]"
#[pyfunction]
pub fn area_with_state(area_desc: &str, ugc_codes: Vec<String>) -> PyResult<String> {
    if ugc_codes.is_empty() {
        return Ok(area_desc.to_string());
    }

    // Parse county names — split on semicolons / newlines first.
    let semicolon_split: Vec<&str> = area_desc
        .split([';', '\n'])
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();

    let counties: Vec<String> = if semicolon_split.len() == 1 && area_desc.contains(',') {
        // Single comma-separated list — split by comma, but don't split before a bare
        // state abbreviation (mirrors the Python negative-lookahead).
        let raw: Vec<&str> = area_desc.split(',').collect();
        let mut merged: Vec<String> = Vec::with_capacity(raw.len());
        for part in raw {
            let stripped = part.trim();
            if stripped.is_empty() {
                continue;
            }
            if is_state_abbrev(stripped) && !merged.is_empty() {
                // Merge back with previous county ("Logan, OK" stays together).
                let last = merged.last_mut().unwrap();
                last.push_str(", ");
                last.push_str(stripped);
            } else {
                merged.push(stripped.to_string());
            }
        }
        merged
    } else {
        semicolon_split.iter().map(|s| s.to_string()).collect()
    };

    if counties.is_empty() {
        return Ok(area_desc.to_string());
    }

    // Group UGC codes by state (first 2 chars), preserving insertion order.
    let mut state_order: Vec<String> = Vec::new();
    let mut state_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    for ugc in &ugc_codes {
        if ugc.len() >= 2 {
            let state = ugc[..2].to_uppercase();
            let entry = state_counts.entry(state.clone()).or_insert(0);
            if *entry == 0 {
                state_order.push(state);
            }
            *entry += 1;
        }
    }

    if state_order.is_empty() {
        return Ok(area_desc.to_string());
    }

    // Build one formatted part per state group.
    let mut parts: Vec<String> = Vec::new();
    let mut idx: usize = 0;
    for state in &state_order {
        let count = *state_counts.get(state).unwrap_or(&0);
        let group = &counties[idx..(idx + count).min(counties.len())];
        if !group.is_empty() {
            let cleaned: Vec<String> = group
                .iter()
                .map(|c| AREA_STATE_SUFFIX_RE.replace(c, "").trim().to_string())
                .filter(|c| {
                    // Only drop bare 2-char uppercase state abbreviations that got
                    // split out of compound strings (e.g. ", OK" → "OK"). Do NOT
                    // apply the full garbage filter here — that would incorrectly
                    // drop county names like "Washington" or "Lincoln".
                    c.len() > 2 || !c.chars().all(|ch| ch.is_ascii_uppercase())
                })
                .collect();
            if !cleaned.is_empty() {
                parts.push(format!("{} [{}]", cleaned.join(", "), state));
            }
        }
        idx += count;
    }

    // Append any leftover counties to the last group (UGC/areaDesc length mismatch).
    if idx < counties.len() {
        let remainder: Vec<String> = counties[idx..]
            .iter()
            .map(|c| AREA_STATE_SUFFIX_RE.replace(c, "").trim().to_string())
            .filter(|c| c.len() > 2 || !c.chars().all(|ch| ch.is_ascii_uppercase()))
            .collect();
        if !remainder.is_empty() {
            if let Some(last) = parts.last_mut() {
                // Append to the last [STATE] group, stripping the closing bracket.
                let trimmed = last
                    .trim_end_matches(']')
                    .trim_end_matches(|c: char| !c.is_alphanumeric())
                    .to_string();
                *last = format!("{}, {} [?]", trimmed, remainder.join(", "));
            } else {
                return Ok(remainder.join(", "));
            }
        }
    }

    Ok(match parts.len() {
        0 => area_desc.to_string(),
        1 => parts.remove(0),
        2 => format!("{} and {}", parts[0], parts[1]),
        _ => {
            let last = parts.pop().unwrap();
            format!("{} and {}", parts.join(", "), last)
        }
    })
}

// ── parse_md_number ──

#[pyfunction]
pub fn parse_md_number(text: &str) -> PyResult<Option<String>> {
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

// ── parse_watch_number ──

#[pyfunction]
pub fn parse_watch_number(text: &str) -> PyResult<Option<(String, String)>> {
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
