#![allow(
    clippy::collapsible_if,
    clippy::manual_range_contains,
    clippy::type_complexity
)]

pub mod geo;
pub mod nwws;
pub mod utils;
pub mod vad;
pub mod vtec;

use pyo3::prelude::*;

#[pymodule]
fn spc_rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Utils
    m.add_function(wrap_pyfunction!(crate::utils::sum_as_string, m)?)?;
    m.add_function(wrap_pyfunction!(crate::utils::calculate_fast_hash, m)?)?;
    m.add_function(wrap_pyfunction!(
        crate::utils::validate_image_cache_batch,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(crate::utils::normalize_product_id, m)?)?;

    // VAD
    m.add_function(wrap_pyfunction!(crate::vad::find_vwp_header_offset, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::parse_vwp_tabular_data, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::vec2comp, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::comp2vec, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_bunkers, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_srh, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_critical_angle, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_dtm, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_crit_angl, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_shear_mag, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::compute_sr_flow, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vad::clip_profile, m)?)?;

    // Geo
    m.add_function(wrap_pyfunction!(crate::geo::extract_latlon_coords, m)?)?;
    m.add_function(wrap_pyfunction!(crate::geo::init_radar_index, m)?)?;
    m.add_function(wrap_pyfunction!(crate::geo::find_nearest_radar, m)?)?;
    m.add_function(wrap_pyfunction!(crate::geo::filter_points_in_polygons, m)?)?;
    m.add_function(wrap_pyfunction!(crate::geo::haversine, m)?)?;
    m.add_function(wrap_pyfunction!(crate::geo::haversine_batch, m)?)?;
    m.add_function(wrap_pyfunction!(
        crate::geo::find_nearest_stations_batch,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(crate::geo::points_in_polygon_counts, m)?)?;
    m.add_function(wrap_pyfunction!(crate::geo::points_in_polygon_lookup, m)?)?;

    // VTEC
    m.add_function(wrap_pyfunction!(crate::vtec::parse_vtec, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vtec::parse_warning_polygon, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vtec::extract_narrative, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vtec::area_with_state, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vtec::parse_md_number, m)?)?;
    m.add_function(wrap_pyfunction!(crate::vtec::parse_watch_number, m)?)?;

    // NWWS
    m.add_function(wrap_pyfunction!(crate::nwws::nwws_start, m)?)?;
    m.add_function(wrap_pyfunction!(crate::nwws::nwws_stop, m)?)?;
    m.add_function(wrap_pyfunction!(crate::nwws::nwws_try_recv, m)?)?;
    m.add_function(wrap_pyfunction!(crate::nwws::nwws_is_connected, m)?)?;
    m.add_function(wrap_pyfunction!(crate::nwws::nwws_stats, m)?)?;
    m.add_function(wrap_pyfunction!(crate::nwws::fetch_s3_vad_fast, m)?)?;

    Ok(())
}
