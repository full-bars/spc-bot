use std::path::{Path, PathBuf};
use std::time::Duration;

use chrono::{Datelike, NaiveDate, Utc};
use clap::Parser;
use image::codecs::gif::{GifEncoder, Repeat};
use image::{Delay, Frame, RgbaImage};
use radar_core::{MomentType, RadarVolume};
use render2d::{base_tilt_cut, render_moment_image, RasterOptions};

/// Fetch recent NEXRAD Level II volumes from the public AWS archive, render
/// them as polar radar images, and output a GIF animation loop.
#[derive(Parser)]
#[command(name = "radar", version, about)]
struct Args {
    /// NEXRAD site ID (e.g. KTLX, KFWS, PGUA).
    #[arg(short, long)]
    site: String,

    /// Radar product. One of: reflectivity, velocity, spectrum-width, zdr,
    /// cc, phidp, kdp.
    #[arg(short, long, default_value = "reflectivity")]
    product: String,

    /// Number of frames (most recent volumes) to include.
    #[arg(short, long, default_value_t = 6)]
    frames: usize,

    /// Output GIF path.
    #[arg(short, long, default_value = "radar.gif")]
    output: PathBuf,

    /// How many days back to search for volumes.
    #[arg(long, default_value_t = 3)]
    days_back: i64,

    /// Cut (elevation tilt) index to render. 0 = lowest.
    #[arg(long, default_value_t = 0)]
    cut: usize,

    /// Image width in pixels.
    #[arg(long, default_value_t = 800)]
    width: u32,

    /// Image height in pixels.
    #[arg(long, default_value_t = 800)]
    height: u32,

    /// Frame delay in centiseconds (100 = 1 second).
    #[arg(long, default_value_t = 100)]
    delay: u16,
}

fn main() {
    let args = Args::parse();
    let site = args.site.to_ascii_uppercase();
    let moment = parse_moment(&args.product);

    let volumes = match fetch_recent_volumes(&site, args.frames, args.days_back) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Error fetching volumes: {e}");
            std::process::exit(1);
        }
    };

    if volumes.is_empty() {
        eprintln!("No radar volumes found for {site}");
        std::process::exit(1);
    }

    let options = RasterOptions {
        width: args.width.max(64),
        height: args.height.max(64),
        range_fraction: 94,
    };

    let mut frames: Vec<RgbaImage> = Vec::with_capacity(volumes.len());
    for volume in &volumes {
        let cut_index = base_tilt_cut(volume, &moment).unwrap_or(0);
        match render_moment_image(volume, cut_index, moment.clone(), options) {
            Ok(img) => frames.push(img),
            Err(e) => eprintln!(
                "  Skipping volume {}: {e}",
                volume.volume_time.format("%H%M%SZ")
            ),
        }
    }

    if frames.is_empty() {
        eprintln!("No frames could be rendered");
        std::process::exit(1);
    }

    encode_gif(&frames, &args.output, args.delay);
    println!("Wrote {} frames to {}", frames.len(), args.output.display());
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
        _ => {
            eprintln!("Unknown product '{name}', using reflectivity");
            MomentType::Reflectivity
        }
    }
}

fn encode_gif(frames: &[RgbaImage], path: &Path, delay_cs: u16) {
    let file = std::fs::File::create(path).expect("failed to create output file");
    let mut encoder = GifEncoder::new(file);
    encoder.set_repeat(Repeat::Infinite).expect("set repeat");

    for img in frames {
        let delay_ms = delay_cs as u32 * 10;
        let delay = Delay::from_numer_denom_ms(delay_ms, 1);
        let frame = Frame::from_parts(img.clone(), 0, 0, delay);
        encoder.encode_frame(frame).expect("encode gif frame");
    }
}

/// Fetch the N most recent NEXRAD Level II archive volumes for a site.
fn fetch_recent_volumes(
    site: &str,
    count: usize,
    days_back: i64,
) -> Result<Vec<RadarVolume>, Box<dyn std::error::Error>> {
    let today = Utc::now().date_naive();
    let mut s3_keys = Vec::new();

    for offset in 0..=days_back.max(0) {
        let date = today - chrono::Duration::days(offset);
        let mut day_keys = list_archive_keys(site, date)?;
        s3_keys.append(&mut day_keys);
        if s3_keys.len() >= count {
            break;
        }
    }

    s3_keys.truncate(count);

    if s3_keys.is_empty() {
        return Ok(Vec::new());
    }

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(60))
        .connect_timeout(Duration::from_secs(10))
        .build()?;

    let mut volumes = Vec::with_capacity(s3_keys.len());
    for key in &s3_keys {
        let url = format!("https://unidata-nexrad-level2.s3.amazonaws.com/{key}");
        match fetch_and_decode_volume(&client, &url) {
            Ok(vol) => volumes.push(vol),
            Err(e) => eprintln!("  Warning: {url}: {e}"),
        }
    }

    Ok(volumes)
}

/// List NEXRAD Level II archive keys for a site on a given date.
fn list_archive_keys(
    site: &str,
    date: NaiveDate,
) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let prefix = format!(
        "{:04}/{:02}/{:02}/{}/",
        date.year(),
        date.month(),
        date.day(),
        site
    );
    let url = format!(
        "https://unidata-nexrad-level2.s3.amazonaws.com/?list-type=2&prefix={}&max-keys=200",
        prefix
    );

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(15))
        .connect_timeout(Duration::from_secs(5))
        .build()?;

    let text = client.get(&url).send()?.error_for_status()?.text()?;

    // Strip xmlns namespace so quick-xml serde can match element names
    let cleaned = text.replace(" xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"", "");
    let listing: S3Listing = quick_xml::de::from_str(&cleaned)?;

    let mut keys: Vec<String> = listing
        .contents
        .into_iter()
        .filter(|obj| obj.size > 0 && !obj.key.ends_with("_MDM"))
        .map(|obj| obj.key)
        .collect();

    keys.sort();
    keys.reverse();
    Ok(keys)
}

fn fetch_and_decode_volume(
    client: &reqwest::blocking::Client,
    url: &str,
) -> Result<RadarVolume, Box<dyn std::error::Error>> {
    let bytes = client.get(url).send()?.error_for_status()?.bytes()?;
    let volume = nexrad_io::decode_volume_from_bytes(&bytes)?;
    Ok(volume)
}

#[derive(Debug, serde::Deserialize)]
struct S3Listing {
    #[serde(rename = "Contents", default)]
    contents: Vec<S3Content>,
}

#[derive(Debug, serde::Deserialize)]
struct S3Content {
    #[serde(rename = "Key")]
    key: String,
    #[serde(rename = "Size")]
    size: u64,
}
