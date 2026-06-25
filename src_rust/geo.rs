use geo::algorithm::contains::Contains;
use geo::{Coord, Point, Polygon};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use rstar::RTree;
use std::sync::RwLock;

// ── extract_latlon_coords ──

#[pyfunction]
pub fn extract_latlon_coords(text: &str) -> PyResult<Vec<(f64, f64)>> {
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

// ── RadarPoint+init_radar_index ──

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
pub fn init_radar_index(coords: &Bound<'_, PyDict>) -> PyResult<()> {
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

// ── find_nearest_radar ──

#[pyfunction]
pub fn find_nearest_radar(lat: f64, lon: f64) -> PyResult<Option<String>> {
    let index_lock = RADAR_INDEX.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("RADAR_INDEX lock poisoned: {e}"))
    })?;
    if let Some(index) = index_lock.as_ref() {
        if let Some(nearest) = index.nearest_neighbor([lat, lon]) {
            return Ok(Some(nearest.id.clone()));
        }
    }
    Ok(None)
}

// ── filter_points_in_polygons ──

#[pyfunction]
pub fn filter_points_in_polygons(
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

// ── haversine ──

#[pyfunction]
pub fn haversine(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> PyResult<f64> {
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

// ── haversine_batch ──

#[pyfunction]
pub fn haversine_batch(
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

// ── find_nearest_stations_batch ──

#[pyfunction]
pub fn find_nearest_stations_batch(
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

// ── points_in_polygon_counts ──

#[pyfunction]
pub fn points_in_polygon_counts(
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

// ── points_in_polygon_lookup ──

#[pyfunction]
pub fn points_in_polygon_lookup(
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
