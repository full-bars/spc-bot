use std::collections::HashMap;
use std::f32::consts::PI;
use std::path::{Path, PathBuf};

use ab_glyph::{FontRef, PxScale};
use clap::Parser;
use color_tables::{ColorTableFamily, ColorTableSet};
use data_source::{
    download_object, download_realtime_volume, latest_realtime_level2_volume, recent_level2_objects,
};
use image::codecs::gif::{GifEncoder, Repeat};
use image::{Delay, Frame, ImageBuffer, Rgba, RgbaImage};
use imageproc::drawing::{
    draw_filled_circle_mut, draw_filled_rect_mut, draw_hollow_circle_mut, draw_line_segment_mut,
    draw_text_mut,
};
use imageproc::rect::Rect;
use nexrad_io::decode_volume_from_path;
use product_engine::{derive_product, DerivationConfig, DerivedSweepProduct, RadarBand};
use radar_core::{ElevationCut, MomentGrid, MomentType, RadarVolume};
use render2d::{
    base_tilt_cut, color_family_for_moment, detect_rotation_sites, smooth_moment_grid,
    viewport_rgba_buffer_len, RotationSite, RotationStrength, ViewportMomentCache,
    ViewportRasterOptions,
};
use serde::Serialize;

const BG: Rgba<u8> = Rgba([10, 12, 18, 255]);
const LAND: Rgba<u8> = Rgba([18, 22, 32, 255]);
const STATE_LINE: Rgba<u8> = Rgba([40, 50, 70, 255]);
const RING: Rgba<u8> = Rgba([55, 60, 75, 255]);
const RING_LABEL: Rgba<u8> = Rgba([90, 105, 135, 255]);
const LABEL: Rgba<u8> = Rgba([180, 195, 220, 255]);
const CITY_DOT: Rgba<u8> = Rgba([200, 210, 230, 180]);
const CITY_LABEL: Rgba<u8> = Rgba([150, 165, 195, 180]);
const COMPASS: Rgba<u8> = Rgba([130, 150, 185, 255]);
const LABEL_BG: Rgba<u8> = Rgba([8, 10, 15, 255]);

const EARTH_RADIUS_KM: f32 = 6371.0;
const CACHE_DIR: &str = "cache/nexrad_volumes";

static STATES_JSON: &[u8] = include_bytes!("../assets/geo/us_states_50m.json");
static SITES_CSV: &[u8] = include_bytes!("../assets/geo/nexrad_sites.csv");

#[derive(Parser)]
#[command(name = "radar", version, about)]
struct Args {
    #[arg(short, long)]
    site: String,
    #[arg(short, long, default_value = "reflectivity")]
    product: String,
    #[arg(short, long, default_value_t = 6)]
    frames: usize,
    #[arg(short, long, default_value = "radar.gif")]
    output: PathBuf,
    #[arg(long, default_value_t = 800)]
    width: u32,
    #[arg(long, default_value_t = 800)]
    height: u32,
    #[arg(long, default_value_t = 50)]
    delay: u16,
    /// Half-width of the displayed view in km (e.g. 75 = storm-scale zoom, 460 = full range)
    #[arg(long, default_value_t = 150.0)]
    range_km: f32,
    /// Detect and overlay mesocyclone/TVS rotation markers; also writes a
    /// `<output>.rotation.json` sidecar with the latest frame's detections
    #[arg(long, default_value_t = false)]
    rotation: bool,
}

fn main() {
    let args = Args::parse();
    let site = args.site.to_ascii_uppercase();
    let moment = parse_moment(&args.product);

    let state_polys = load_state_polys();
    let site_coords = load_site_coords();
    let (radar_lat, radar_lon) = site_coords.get(&site.as_str()).copied().unwrap_or_else(|| {
        eprintln!("Warning: unknown site '{site}', geography will be misaligned");
        (35.333, -97.278)
    });

    // Fetch volumes: try realtime first, fall back to archive
    let cache_dir = Path::new(CACHE_DIR);
    std::fs::create_dir_all(cache_dir).ok();

    let mut volumes = Vec::new();

    // Try realtime chunks feed first (most recent data)
    match latest_realtime_level2_volume(&site) {
        Ok(rt_vol) => match download_realtime_volume(&rt_vol, cache_dir) {
            Ok(downloaded) => match decode_volume_from_path(&downloaded.path) {
                Ok(vol) => volumes.push(vol),
                Err(e) => eprintln!("Warning: decode realtime: {e}"),
            },
            Err(e) => eprintln!("Warning: download realtime: {e}"),
        },
        Err(e) => eprintln!("Warning: no realtime volume for {site}: {e}"),
    }

    // Fill remaining frames from archive (oldest first)
    if volumes.len() < args.frames {
        let needed = args.frames - volumes.len();
        match recent_level2_objects(&site, 3, needed + 2) {
            Ok(objects) => {
                // Take oldest objects first (playback order)
                let take: Vec<_> = objects.iter().rev().take(needed + 2).collect();
                for obj in &take {
                    if volumes.len() >= args.frames {
                        break;
                    }
                    match download_object("unidata-nexrad-level2", (*obj).clone(), cache_dir) {
                        Ok(downloaded) => match decode_volume_from_path(&downloaded.path) {
                            Ok(vol) => volumes.push(vol),
                            Err(e) => eprintln!("Warning: decode archive: {e}"),
                        },
                        Err(e) => eprintln!("Warning: download archive: {e}"),
                    }
                }
            }
            Err(e) => eprintln!("Warning: archive listing: {e}"),
        }
    }

    if volumes.is_empty() {
        eprintln!("No radar volumes found for {site}");
        std::process::exit(1);
    }

    // Realtime is fetched first (newest) and archive fill-in appended after,
    // so the vec isn't chronological — sort oldest-to-newest for correct GIF playback order.
    // The realtime chunk and the archive's most recent object can both reference the same
    // underlying volume, so dedup by timestamp too or the GIF gets a visually "stuck" frame.
    volumes.sort_by_key(|v| v.volume_time);
    volumes.dedup_by_key(|v| v.volume_time);

    let w = args.width.max(64);
    let h = args.height.max(64);
    let cx = (w as f32 - 1.0) / 2.0;
    let cy = (h as f32 - 1.0) / 2.0;
    let max_range_km = args.range_km.max(5.0);
    let km_per_px = (2.0 * max_range_km) / w.min(h) as f32;
    // Must exactly match the radar raster's pixel scale (km_per_px above) so
    // geography/markers land on the same ground point as the echo data —
    // do not shrink this for cosmetic purposes (see land-disk fill below).
    let radius_px = cx.min(cy);
    let viewport_options = ViewportRasterOptions {
        width: w,
        height: h,
        radar_x_px: cx,
        radar_y_px: cy,
        km_per_px_x: km_per_px,
        km_per_px_y: km_per_px,
        rotation_rad: 0.0,
    };

    let font_data: &[u8] = include_bytes!("../assets/fonts/DejaVuSans.ttf");
    let font = FontRef::try_from_slice(font_data).expect("load DejaVuSans.ttf");
    let title_scale = PxScale::from(17.0);
    let label_scale = PxScale::from(13.0);
    let small_scale = PxScale::from(11.0);
    let city_scale = PxScale::from(10.0);

    let cities = get_major_cities();
    let color_tables = ColorTableSet::default();

    let mut frames: Vec<RgbaImage> = Vec::with_capacity(volumes.len());
    let mut latest_rotation: Option<(chrono::DateTime<chrono::Utc>, Vec<RotationSite>)> = None;
    let mut all_detections: Vec<(chrono::DateTime<chrono::Utc>, RotationSite)> = Vec::new();
    let mut tds_timeline: Vec<TdsScanOut> = Vec::new();
    let is_ptds = is_ptds_product(&args.product);
    for volume in &volumes {
        // Rotation/TDS detection runs off velocity and dual-pol moments independent of
        // the requested *display* product, so it must not be skipped just because that
        // display moment (e.g. zdr/kdp/phidp) is missing on this particular scan below.
        let rotation_sites = if args.rotation {
            let deduped = dedup_repeated_tilts(volume);
            let sites = detect_rotation_sites(&deduped);

            if let Some(strongest) = sites
                .iter()
                .max_by(|a, b| a.vrot_mps.total_cmp(&b.vrot_mps))
            {
                if let Some((tds_cut_index, tds_grid)) = tds_score_grid(volume) {
                    if let Some(tds_cut) = volume.cuts.get(tds_cut_index) {
                        let range_km = (strongest.ground_range_m / 1000.0) as f32;
                        if let Some(score) = sample_grid_max_near(
                            tds_cut,
                            &tds_grid,
                            strongest.azimuth_deg,
                            range_km,
                            5.0,
                            2.0,
                        ) {
                            tds_timeline.push(TdsScanOut {
                                time: volume.volume_time.to_rfc3339(),
                                tds_score: score,
                                vrot_mps: strongest.vrot_mps,
                                strength: strength_label(strongest.strength).to_string(),
                            });
                        }
                    }
                }
            }

            for site in &sites {
                all_detections.push((volume.volume_time, *site));
            }
            if latest_rotation
                .as_ref()
                .is_none_or(|(t, _)| volume.volume_time > *t)
            {
                latest_rotation = Some((volume.volume_time, sites.clone()));
            }

            Some(sites)
        } else {
            None
        };

        let cache = match build_moment_cache(volume, is_ptds, &moment, &color_tables) {
            Some(c) => c,
            None => {
                eprintln!("  Skipping volume: product unavailable (rotation/TDS detection above still recorded)");
                continue;
            }
        };
        let mut pixels = vec![0u8; viewport_rgba_buffer_len(viewport_options)];
        let radar_img = match cache.render_moment_rgba_into(volume, viewport_options, &mut pixels) {
            Ok((rw, rh)) => match RgbaImage::from_raw(rw, rh, pixels) {
                Some(img) => img,
                None => {
                    eprintln!("  Skipping volume: buffer size mismatch");
                    continue;
                }
            },
            Err(e) => {
                eprintln!("  Skipping volume: {e}");
                continue;
            }
        };

        let mut img = ImageBuffer::new(w, h);
        for p in img.pixels_mut() {
            *p = BG;
        }

        // Fill radar disk with land color (slightly inset from radius_px purely for
        // visual framing — geo/marker coordinate math below uses the un-inset radius_px
        // so it stays exactly aligned with the radar raster's pixel scale)
        let land_disk_r = radius_px * 0.99;
        for y in 0..h {
            for x in 0..w {
                let dx = x as f32 - cx;
                let dy = y as f32 - cy;
                if dx * dx + dy * dy <= land_disk_r * land_disk_r {
                    img.put_pixel(x, y, LAND);
                }
            }
        }

        // State outlines
        for state in &state_polys {
            for ring in &state.rings {
                let pts: Vec<(f32, f32)> = ring
                    .iter()
                    .map(|&(lat, lon)| {
                        geo_to_pixel(
                            lat,
                            lon,
                            cx,
                            cy,
                            radius_px,
                            radar_lat,
                            radar_lon,
                            max_range_km,
                        )
                    })
                    .collect();
                for i in 0..pts.len() {
                    let (x1, y1) = pts[i];
                    let (x2, y2) = pts[(i + 1) % pts.len()];
                    let d1 = ((x1 - cx).powi(2) + (y1 - cy).powi(2)).sqrt();
                    let d2 = ((x2 - cx).powi(2) + (y2 - cy).powi(2)).sqrt();
                    if d1 > radius_px && d2 > radius_px {
                        continue;
                    }
                    if x1 >= 0.0 && y1 >= 0.0 && x2 >= 0.0 && y2 >= 0.0 {
                        draw_line_segment_mut(&mut img, (x1, y1), (x2, y2), STATE_LINE);
                    }
                }
            }
        }

        // Range rings — step scales with zoom so ~4-6 rings are visible at any range
        let ring_step = if max_range_km <= 30.0 {
            5.0
        } else if max_range_km <= 60.0 {
            10.0
        } else if max_range_km <= 150.0 {
            25.0
        } else if max_range_km <= 300.0 {
            50.0
        } else {
            100.0
        };
        let ring_intervals: Vec<f32> = {
            let mut v = Vec::new();
            let mut km = ring_step;
            while km <= max_range_km {
                v.push(km);
                km += ring_step;
            }
            v
        };
        for &km in &ring_intervals {
            let ring_r = (radius_px * km / max_range_km).round() as i32;
            if ring_r < 5 || ring_r > radius_px as i32 + 5 {
                continue;
            }
            draw_hollow_circle_mut(&mut img, (cx as i32, cy as i32), ring_r, RING);
            let label = format!("{km:.0}km");
            draw_text_mut(
                &mut img,
                RING_LABEL,
                (cx + ring_r as f32 - 16.0) as i32,
                (cy + 4.0) as i32,
                small_scale,
                &font,
                &label,
            );
        }

        // Compass
        let compass_r = radius_px - 12.0;
        for (angle, label) in [(-90.0f32, "N"), (0.0, "E"), (90.0, "S"), (180.0, "W")] {
            let rad = angle * PI / 180.0;
            let x = cx + rad.cos() * compass_r;
            let y = cy + rad.sin() * compass_r;
            draw_text_mut(
                &mut img,
                COMPASS,
                (x - 6.0) as i32,
                (y - 8.0) as i32,
                small_scale,
                &font,
                label,
            );
        }

        // Cities
        for city in &cities {
            let (px, py) = geo_to_pixel(
                city.lat,
                city.lon,
                cx,
                cy,
                radius_px,
                radar_lat,
                radar_lon,
                max_range_km,
            );
            if px >= 0.0 && px < w as f32 && py >= 0.0 && py < h as f32 {
                let d = ((px - cx).powi(2) + (py - cy).powi(2)).sqrt();
                if d <= radius_px {
                    draw_filled_circle_mut(
                        &mut img,
                        (px as i32, py as i32),
                        if city.major { 3 } else { 2 },
                        CITY_DOT,
                    );
                    let scale = if city.major { label_scale } else { city_scale };
                    draw_text_mut(
                        &mut img,
                        CITY_LABEL,
                        (px + 4.0) as i32,
                        (py - 5.0) as i32,
                        scale,
                        &font,
                        &city.name,
                    );
                }
            }
        }

        // Overlay radar
        for (px, py, pixel) in radar_img.enumerate_pixels() {
            if pixel[3] > 10 {
                img.put_pixel(px, py, *pixel);
            }
        }

        // Rotation / mesocyclone-TVS markers. `detect_rotation_sites` can return many weak
        // circulations in a messy line — cap full ring+label treatment to the top 6 by Vrot
        // (matches the embed's cap) and draw the rest as small unlabeled dots to avoid
        // overlapping rings and colliding labels at storm-scale zoom.
        if let Some(sites) = &rotation_sites {
            let mut ranked: Vec<&RotationSite> = sites.iter().collect();
            ranked.sort_by(|a, b| b.vrot_mps.total_cmp(&a.vrot_mps));
            for (i, site) in ranked.iter().enumerate() {
                let range_km = (site.ground_range_m / 1000.0) as f32;
                if range_km > max_range_km {
                    continue;
                }
                let rad = site.azimuth_deg.to_radians();
                let frac = range_km / max_range_km;
                let px = cx + radius_px * frac * rad.sin();
                let py = cy - radius_px * frac * rad.cos();
                let (color, marker_r) = rotation_marker_style(site.strength);
                if i < 6 {
                    draw_hollow_circle_mut(&mut img, (px as i32, py as i32), marker_r, color);
                    draw_hollow_circle_mut(&mut img, (px as i32, py as i32), marker_r + 1, color);
                    let label = format!("{:.0}m/s", site.vrot_mps);
                    let label_x = (px + marker_r as f32 + 4.0) as i32;
                    let label_y = (py - 6.0) as i32;
                    let label_w = (label.len() as i32 * 7).max(24);
                    draw_filled_rect_mut(
                        &mut img,
                        Rect::at(label_x - 1, label_y - 1).of_size(label_w as u32, 15),
                        LABEL_BG,
                    );
                    draw_text_mut(
                        &mut img,
                        color,
                        label_x,
                        label_y,
                        small_scale,
                        &font,
                        &label,
                    );
                } else {
                    draw_filled_circle_mut(&mut img, (px as i32, py as i32), 2, color);
                }
            }
        }

        // Timestamp + site + product (with a backing panel so it stays legible over echoes)
        draw_filled_rect_mut(&mut img, Rect::at(0, 0).of_size(160, 78), LABEL_BG);
        let time_label = format!("{}Z", volume.volume_time.format("%H:%M"));
        draw_text_mut(&mut img, LABEL, 14, 14, title_scale, &font, &time_label);
        draw_text_mut(&mut img, COMPASS, 14, 36, label_scale, &font, &site);
        let product_label = if is_ptds {
            "PTDS".to_string()
        } else {
            args.product.clone()
        };
        draw_text_mut(
            &mut img,
            RING_LABEL,
            14,
            56,
            small_scale,
            &font,
            &product_label,
        );

        frames.push(img);
    }

    // Write rotation sidecar BEFORE the empty-frames exit so detections
    // are preserved even when no displayable moment exists.
    if args.rotation {
        if let Some((volume_time, sites)) = latest_rotation {
            let out: Vec<RotationSiteOut> = sites.iter().map(rotation_site_out).collect();

            let peak_vrot = all_detections
                .iter()
                .max_by(|a, b| a.1.vrot_mps.total_cmp(&b.1.vrot_mps))
                .map(|(t, s)| RotationPeakOut {
                    time: t.to_rfc3339(),
                    site: rotation_site_out(s),
                });
            let peak_dv = all_detections
                .iter()
                .max_by(|a, b| a.1.gate_to_gate_dv_mps.total_cmp(&b.1.gate_to_gate_dv_mps))
                .map(|(t, s)| RotationPeakOut {
                    time: t.to_rfc3339(),
                    site: rotation_site_out(s),
                });

            let payload = RotationPayload {
                volume_time: volume_time.to_rfc3339(),
                sites: out,
                peak_vrot,
                peak_dv,
                tds_timeline,
            };
            let sidecar = args.output.with_extension("rotation.json");
            if let Ok(json) = serde_json::to_string_pretty(&payload) {
                if let Err(e) = std::fs::write(&sidecar, json) {
                    eprintln!("Warning: failed to write rotation sidecar: {e}");
                }
            }
        }
    }

    if frames.is_empty() {
        eprintln!("No frames could be rendered");
        std::process::exit(1);
    }
    encode_gif(&frames, &args.output, args.delay);
    println!("Wrote {} frames to {}", frames.len(), args.output.display());
}

fn rotation_site_out(s: &RotationSite) -> RotationSiteOut {
    RotationSiteOut {
        azimuth_deg: s.azimuth_deg,
        range_km: (s.ground_range_m / 1000.0) as f32,
        vrot_mps: s.vrot_mps,
        gtg_dv_mps: s.gate_to_gate_dv_mps,
        rank: s.rank,
        depth_tilts: s.depth_tilts,
        depth_km: (s.depth_m / 1000.0) as f32,
        base_elevation_deg: s.base_elevation_deg,
        strength: strength_label(s.strength).to_string(),
    }
}

#[derive(Serialize)]
struct RotationSiteOut {
    azimuth_deg: f32,
    range_km: f32,
    vrot_mps: f32,
    gtg_dv_mps: f32,
    rank: u8,
    depth_tilts: usize,
    depth_km: f32,
    base_elevation_deg: f32,
    strength: String,
}

#[derive(Serialize)]
struct RotationPeakOut {
    time: String,
    #[serde(flatten)]
    site: RotationSiteOut,
}

#[derive(Serialize)]
struct TdsScanOut {
    time: String,
    tds_score: f32,
    vrot_mps: f32,
    strength: String,
}

#[derive(Serialize)]
struct RotationPayload {
    volume_time: String,
    sites: Vec<RotationSiteOut>,
    peak_vrot: Option<RotationPeakOut>,
    peak_dv: Option<RotationPeakOut>,
    tds_timeline: Vec<TdsScanOut>,
}

fn strength_label(strength: RotationStrength) -> &'static str {
    match strength {
        RotationStrength::WeakCirculation => "weak_circulation",
        RotationStrength::ModerateCirculation => "moderate_circulation",
        RotationStrength::Mesocyclone => "mesocyclone",
        RotationStrength::Tvs => "tvs",
    }
}

fn rotation_marker_style(strength: RotationStrength) -> (Rgba<u8>, i32) {
    match strength {
        RotationStrength::WeakCirculation => (Rgba([230, 220, 60, 255]), 10),
        RotationStrength::ModerateCirculation => (Rgba([240, 150, 40, 255]), 12),
        RotationStrength::Mesocyclone => (Rgba([235, 50, 50, 255]), 14),
        RotationStrength::Tvs => (Rgba([230, 40, 230, 255]), 16),
    }
}

/// TDWR and SAILS-enabled NEXRAD VCPs rescan the same low elevation multiple
/// times per volume. `detect_rotation_sites` only examines the 8
/// lowest-elevation velocity tilts, so redundant near-duplicate low tilts
/// crowd out genuinely distinct mid-level tilts, starving vertical-continuity
/// checks of the diversity they need. Keep only the first cut per ~0.1deg
/// elevation bucket before handing the volume to the detector.
fn dedup_repeated_tilts(
    volume: &radar_core::RadarVolume,
) -> std::borrow::Cow<'_, radar_core::RadarVolume> {
    let mut seen = std::collections::HashSet::new();
    let has_dupes = volume
        .cuts
        .iter()
        .any(|c| !seen.insert((c.elevation_deg * 10.0).round() as i32));
    if !has_dupes {
        return std::borrow::Cow::Borrowed(volume);
    }
    let mut seen = std::collections::HashSet::new();
    let mut deduped = volume.clone();
    deduped
        .cuts
        .retain(|c| seen.insert((c.elevation_deg * 10.0).round() as i32));
    std::borrow::Cow::Owned(deduped)
}

#[allow(clippy::too_many_arguments)]
fn geo_to_pixel(
    lat: f32,
    lon: f32,
    cx: f32,
    cy: f32,
    radius_px: f32,
    radar_lat: f32,
    radar_lon: f32,
    max_range_km: f32,
) -> (f32, f32) {
    let dlat = (lat - radar_lat).to_radians();
    let dlon = (lon - radar_lon).to_radians();
    let lat1 = radar_lat.to_radians();
    let lat2 = lat.to_radians();
    let a = (dlat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (dlon / 2.0).sin().powi(2);
    let c = 2.0 * (a.sqrt().min(1.0)).asin();
    let dist_km = c * EARTH_RADIUS_KM;
    let az = if dlon.abs() < 1e-8 && dlat.abs() < 1e-8 {
        0.0
    } else {
        (dlon.sin() * lat2.cos())
            .atan2(lat1.cos() * lat2.sin() - lat1.sin() * lat2.cos() * dlon.cos())
    };
    let frac = dist_km / max_range_km;
    (
        cx + radius_px * frac * az.sin(),
        cy - radius_px * frac * az.cos(),
    )
}

struct StatePoly {
    _name: String,
    rings: Vec<Vec<(f32, f32)>>,
}

fn load_site_coords() -> HashMap<&'static str, (f32, f32)> {
    let text = std::str::from_utf8(SITES_CSV).expect("valid UTF-8");
    text.lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() < 3 {
                return None;
            }
            Some((
                parts[0].trim(),
                (parts[1].trim().parse().ok()?, parts[2].trim().parse().ok()?),
            ))
        })
        .collect()
}

fn load_state_polys() -> Vec<StatePoly> {
    let data: serde_json::Value = serde_json::from_slice(STATES_JSON).expect("parse state GeoJSON");
    let features = data["features"].as_array().expect("features array");
    features
        .iter()
        .filter_map(|feat| {
            let name = feat["properties"]["name"].as_str()?.to_string();
            let geom = &feat["geometry"];
            let mut rings = Vec::new();
            match geom["type"].as_str()? {
                "Polygon" => {
                    if let Some(coords) = geom["coordinates"][0].as_array() {
                        let ring: Vec<(f32, f32)> = coords
                            .iter()
                            .filter_map(|c| Some((c[1].as_f64()? as f32, c[0].as_f64()? as f32)))
                            .collect();
                        rings.push(ring);
                    }
                }
                "MultiPolygon" => {
                    if let Some(polys) = geom["coordinates"].as_array() {
                        for poly in polys {
                            if let Some(coords) = poly[0].as_array() {
                                let ring: Vec<(f32, f32)> = coords
                                    .iter()
                                    .filter_map(|c| {
                                        Some((c[1].as_f64()? as f32, c[0].as_f64()? as f32))
                                    })
                                    .collect();
                                rings.push(ring);
                            }
                        }
                    }
                }
                _ => {}
            }
            Some(StatePoly { _name: name, rings })
        })
        .collect()
}

struct City {
    name: String,
    lat: f32,
    lon: f32,
    major: bool,
}

fn get_major_cities() -> Vec<City> {
    vec![
        City {
            name: "NYC".into(),
            lat: 40.713,
            lon: -74.006,
            major: true,
        },
        City {
            name: "Los Angeles".into(),
            lat: 34.052,
            lon: -118.244,
            major: true,
        },
        City {
            name: "Chicago".into(),
            lat: 41.878,
            lon: -87.630,
            major: true,
        },
        City {
            name: "Houston".into(),
            lat: 29.760,
            lon: -95.370,
            major: true,
        },
        City {
            name: "Phoenix".into(),
            lat: 33.448,
            lon: -112.074,
            major: true,
        },
        City {
            name: "San Antonio".into(),
            lat: 29.424,
            lon: -98.494,
            major: true,
        },
        City {
            name: "Dallas".into(),
            lat: 32.777,
            lon: -96.797,
            major: true,
        },
        City {
            name: "San Diego".into(),
            lat: 32.716,
            lon: -117.161,
            major: true,
        },
        City {
            name: "Austin".into(),
            lat: 30.267,
            lon: -97.743,
            major: true,
        },
        City {
            name: "Denver".into(),
            lat: 39.739,
            lon: -104.990,
            major: true,
        },
        City {
            name: "Ft Worth".into(),
            lat: 32.755,
            lon: -97.331,
            major: false,
        },
        City {
            name: "Oklahoma City".into(),
            lat: 35.468,
            lon: -97.516,
            major: false,
        },
        City {
            name: "Tulsa".into(),
            lat: 36.154,
            lon: -95.993,
            major: false,
        },
        City {
            name: "Wichita".into(),
            lat: 37.687,
            lon: -97.337,
            major: false,
        },
        City {
            name: "Kansas City".into(),
            lat: 39.100,
            lon: -94.580,
            major: false,
        },
        City {
            name: "Amarillo".into(),
            lat: 35.222,
            lon: -101.831,
            major: false,
        },
        City {
            name: "Albuquerque".into(),
            lat: 35.085,
            lon: -106.606,
            major: false,
        },
        City {
            name: "Little Rock".into(),
            lat: 34.746,
            lon: -92.290,
            major: false,
        },
        City {
            name: "Shreveport".into(),
            lat: 32.525,
            lon: -93.750,
            major: false,
        },
        City {
            name: "Memphis".into(),
            lat: 35.149,
            lon: -90.049,
            major: false,
        },
        City {
            name: "Nashville".into(),
            lat: 36.163,
            lon: -86.782,
            major: false,
        },
        City {
            name: "Knoxville".into(),
            lat: 35.960,
            lon: -83.921,
            major: false,
        },
        City {
            name: "Jackson MS".into(),
            lat: 32.299,
            lon: -90.185,
            major: false,
        },
        City {
            name: "Baton Rouge".into(),
            lat: 30.421,
            lon: -91.153,
            major: false,
        },
        City {
            name: "New Orleans".into(),
            lat: 29.949,
            lon: -90.071,
            major: false,
        },
        City {
            name: "St Louis".into(),
            lat: 38.627,
            lon: -90.199,
            major: false,
        },
        City {
            name: "Atlanta".into(),
            lat: 33.749,
            lon: -84.388,
            major: true,
        },
        City {
            name: "Minneapolis".into(),
            lat: 44.978,
            lon: -93.265,
            major: true,
        },
        City {
            name: "Omaha".into(),
            lat: 41.256,
            lon: -95.934,
            major: false,
        },
        City {
            name: "Des Moines".into(),
            lat: 41.587,
            lon: -93.624,
            major: false,
        },
        City {
            name: "Lincoln".into(),
            lat: 40.813,
            lon: -96.703,
            major: false,
        },
        City {
            name: "Topeka".into(),
            lat: 39.048,
            lon: -95.678,
            major: false,
        },
        City {
            name: "Salt Lake City".into(),
            lat: 40.760,
            lon: -111.888,
            major: false,
        },
        City {
            name: "Boise".into(),
            lat: 43.617,
            lon: -116.200,
            major: false,
        },
        City {
            name: "Seattle".into(),
            lat: 47.606,
            lon: -122.332,
            major: true,
        },
        City {
            name: "Portland".into(),
            lat: 45.515,
            lon: -122.679,
            major: false,
        },
        City {
            name: "Sacramento".into(),
            lat: 38.581,
            lon: -121.494,
            major: false,
        },
        City {
            name: "San Francisco".into(),
            lat: 37.774,
            lon: -122.419,
            major: true,
        },
        City {
            name: "Las Vegas".into(),
            lat: 36.115,
            lon: -115.173,
            major: true,
        },
        City {
            name: "Tucson".into(),
            lat: 32.222,
            lon: -110.926,
            major: false,
        },
        City {
            name: "El Paso".into(),
            lat: 31.761,
            lon: -106.485,
            major: false,
        },
        City {
            name: "Corpus Christi".into(),
            lat: 27.784,
            lon: -97.510,
            major: false,
        },
        City {
            name: "Miami".into(),
            lat: 25.761,
            lon: -80.192,
            major: true,
        },
        City {
            name: "Tampa".into(),
            lat: 27.951,
            lon: -82.458,
            major: false,
        },
        City {
            name: "Jacksonville".into(),
            lat: 30.332,
            lon: -81.656,
            major: false,
        },
        City {
            name: "Charlotte".into(),
            lat: 35.227,
            lon: -80.843,
            major: false,
        },
        City {
            name: "Raleigh".into(),
            lat: 35.780,
            lon: -78.639,
            major: false,
        },
        City {
            name: "Norfolk".into(),
            lat: 36.851,
            lon: -76.285,
            major: false,
        },
        City {
            name: "Washington DC".into(),
            lat: 38.907,
            lon: -77.037,
            major: true,
        },
        City {
            name: "Baltimore".into(),
            lat: 39.290,
            lon: -76.612,
            major: false,
        },
        City {
            name: "Philadelphia".into(),
            lat: 39.952,
            lon: -75.165,
            major: true,
        },
        City {
            name: "New York".into(),
            lat: 40.713,
            lon: -74.006,
            major: true,
        },
        City {
            name: "Boston".into(),
            lat: 42.360,
            lon: -71.059,
            major: true,
        },
        City {
            name: "Pittsburgh".into(),
            lat: 40.441,
            lon: -79.996,
            major: false,
        },
        City {
            name: "Cincinnati".into(),
            lat: 39.103,
            lon: -84.512,
            major: false,
        },
        City {
            name: "Indianapolis".into(),
            lat: 39.768,
            lon: -86.158,
            major: false,
        },
        City {
            name: "Detroit".into(),
            lat: 42.331,
            lon: -83.046,
            major: true,
        },
        City {
            name: "Cleveland".into(),
            lat: 41.499,
            lon: -81.694,
            major: false,
        },
        City {
            name: "Milwaukee".into(),
            lat: 43.039,
            lon: -87.906,
            major: false,
        },
        City {
            name: "Louisville".into(),
            lat: 38.253,
            lon: -85.758,
            major: false,
        },
        City {
            name: "Lexington".into(),
            lat: 38.041,
            lon: -84.458,
            major: false,
        },
        City {
            name: "Bowling Green".into(),
            lat: 36.990,
            lon: -86.444,
            major: false,
        },
        City {
            name: "Paducah".into(),
            lat: 37.083,
            lon: -88.600,
            major: false,
        },
        City {
            name: "Owensboro".into(),
            lat: 37.771,
            lon: -87.110,
            major: false,
        },
        City {
            name: "Richmond KY".into(),
            lat: 37.748,
            lon: -84.294,
            major: false,
        },
    ]
}

fn parse_moment(name: &str) -> MomentType {
    match name.to_lowercase().as_str() {
        "reflectivity" | "ref" | "dbz" => MomentType::Reflectivity,
        "velocity" | "vel" => MomentType::Velocity,
        "spectrum-width" | "sw" => MomentType::SpectrumWidth,
        "zdr" => MomentType::DifferentialReflectivity,
        "cc" | "rhohv" => MomentType::CorrelationCoefficient,
        "phidp" => MomentType::DifferentialPhase,
        "kdp" => MomentType::SpecificDifferentialPhase,
        // PTDS isn't a native moment on the volume — build_moment_cache branches on
        // is_ptds_product and derives it separately, this is just an unused placeholder.
        "tds" | "ptds" => MomentType::Reflectivity,
        _ => {
            eprintln!("Unknown product '{name}', using reflectivity");
            MomentType::Reflectivity
        }
    }
}

fn is_ptds_product(product: &str) -> bool {
    matches!(product.to_lowercase().as_str(), "tds" | "ptds")
}

/// TDS (tornadic debris signature) confidence: BowEcho's `product_engine` combines
/// reflectivity + CC dropout + ZDR into a single 0-100 score on the lowest reflectivity
/// tilt. Velocity/rotation isn't part of this score directly — that's folded in
/// separately by sampling this grid at each detected rotation site's position.
/// The lowest-elevation *reflectivity-only surveillance* cut (what `base_tilt_cut`
/// picks for MomentType::Reflectivity) often lacks CC/ZDR — those ride on a separate
/// Doppler cut at the same elevation. TDS needs REF+CC(+ZDR) together on one cut, so
/// pick the lowest-elevation cut that actually carries both.
fn lowest_dual_pol_cut(volume: &RadarVolume) -> Option<usize> {
    volume
        .cuts
        .iter()
        .enumerate()
        .filter(|(_, c)| {
            c.moments.contains_key(&MomentType::Reflectivity)
                && c.moments.contains_key(&MomentType::CorrelationCoefficient)
        })
        .min_by(|a, b| a.1.elevation_deg.total_cmp(&b.1.elevation_deg))
        .map(|(i, _)| i)
}

fn tds_score_grid(volume: &RadarVolume) -> Option<(usize, MomentGrid)> {
    let cut_index = lowest_dual_pol_cut(volume)?;
    let cut = volume.cuts.get(cut_index)?;
    let config =
        DerivationConfig::with_products(RadarBand::S, [DerivedSweepProduct::TdsConfidence]);
    let grid = derive_product(cut, DerivedSweepProduct::TdsConfidence, &config)?;
    Some((cut_index, grid))
}

fn build_moment_cache(
    volume: &RadarVolume,
    is_ptds: bool,
    moment: &MomentType,
    color_tables: &ColorTableSet,
) -> Option<ViewportMomentCache> {
    if is_ptds {
        let (cut_index, grid) = tds_score_grid(volume)?;
        ViewportMomentCache::new_derived(
            volume,
            cut_index,
            grid,
            ColorTableFamily::Probability,
            color_tables,
        )
        .ok()
    } else {
        let cut_index = base_tilt_cut(volume, moment).unwrap_or(0);
        let cut = volume.cuts.get(cut_index)?;
        let grid = cut.moments.get(moment)?;
        let smoothed = smooth_moment_grid(grid);
        let family = color_family_for_moment(moment);
        ViewportMomentCache::new_derived(volume, cut_index, smoothed, family, color_tables).ok()
    }
}

fn angular_diff(a: f32, b: f32) -> f32 {
    let d = (a - b).abs() % 360.0;
    d.min(360.0 - d)
}

/// Sample a MomentGrid's value at the nearest radial/gate to a given azimuth+range.
/// Debris balls and the couplet's exact flagged gate rarely land on the same pixel —
/// search a small azimuth/range window around the site and take the max, rather than
/// sampling one nearest gate (which reliably undershoots true colocated TDS score).
fn sample_grid_max_near(
    cut: &ElevationCut,
    grid: &MomentGrid,
    azimuth_deg: f32,
    range_km: f32,
    az_window_deg: f32,
    range_window_km: f32,
) -> Option<f32> {
    let target_range_m = range_km * 1000.0;
    let range_window_m = range_window_km * 1000.0;
    let gate_spacing = grid.gate_range.gate_spacing_m as f32;
    let first_gate = grid.gate_range.first_gate_m as f32;
    let gate_lo = (((target_range_m - range_window_m - first_gate) / gate_spacing)
        .floor()
        .max(0.0)) as usize;
    let gate_hi = (((target_range_m + range_window_m - first_gate) / gate_spacing)
        .ceil()
        .max(0.0)) as usize;
    let gate_hi = gate_hi.min(grid.gate_range.gate_count.saturating_sub(1));

    let mut best: Option<f32> = None;
    for (row, &radial_idx) in grid.radial_indices.iter().enumerate() {
        let Some(radial) = cut.radials.get(radial_idx) else {
            continue;
        };
        if angular_diff(radial.azimuth_deg, azimuth_deg) > az_window_deg {
            continue;
        }
        for gate in gate_lo..=gate_hi {
            if let Some(v) = grid.scaled_value(row, gate) {
                if v.is_finite() {
                    best = Some(best.map_or(v, |b| b.max(v)));
                }
            }
        }
    }
    best
}

fn encode_gif(frames: &[RgbaImage], path: &Path, delay_cs: u16) {
    let file = std::fs::File::create(path).expect("failed to create output file");
    let mut encoder = GifEncoder::new_with_speed(file, 30);
    encoder.set_repeat(Repeat::Infinite).expect("set repeat");
    for img in frames {
        let delay = Delay::from_numer_denom_ms(delay_cs as u32 * 10, 1);
        encoder
            .encode_frame(Frame::from_parts(img.clone(), 0, 0, delay))
            .expect("encode gif frame");
    }
}
