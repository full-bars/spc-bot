use futures::stream::StreamExt;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use reqwest::blocking::Client;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use std::time::Duration;
use tokio::sync::mpsc;
use xmpp_parsers::message::Message as XmppMessage;
use xmpp_parsers::presence::{Presence, Type as PresenceType};

// ── NwwsMessage+State+NWWS_STATE ──

#[derive(Clone, Debug)]
pub struct NwwsMessage {
    pub office: String,
    pub ttaaii: String,
    pub awipsid: String,
    pub issue: String,
    pub raw_text: String,
    pub delay_stamp: Option<String>,
}

/// NwwsState manages the tokio runtime, message channel, and connection status.
struct NwwsState {
    _runtime_handle: tokio::runtime::Runtime,
    _sender: mpsc::UnboundedSender<NwwsMessage>,
    receiver: Arc<std::sync::Mutex<mpsc::UnboundedReceiver<NwwsMessage>>>,
    is_connected: Arc<AtomicBool>,
    messages_received: Arc<AtomicU64>,
    messages_filtered: Arc<AtomicU64>,
    messages_drained: Arc<AtomicU64>,
    reconnect_count: Arc<AtomicU64>,
    last_error: Arc<RwLock<String>>,
}

static NWWS_STATE: Lazy<RwLock<Option<NwwsState>>> = Lazy::new(|| RwLock::new(None));

// ── parse_xmpp_message ──

/// Helper: extract NWWS message from XMPP message stanza.
///
/// NWWS-OI products carry their metadata in an `<x xmlns="nwws-oi">` payload
/// with `cccc` (office), `ttaaii` (WMO header), `awipsid` (AFOS PIL), and
/// `issue` (timestamp) attributes. The product text is the body of that
/// element (not the message body, which iembot leaves blank or summary-only).
///
/// Messages without an `nwws-oi` payload are MUC chatter / status pings and
/// are skipped silently — they aren't products.
pub(crate) fn parse_xmpp_message(msg: &XmppMessage) -> Option<NwwsMessage> {
    // Locate the <x xmlns="nwws-oi"> payload. The namespace iembot uses is
    // literally "nwws-oi" (no URI scheme).
    let nwws_payload = msg
        .payloads
        .iter()
        .find(|p| p.name() == "x" && p.ns() == "nwws-oi")?;

    let office = nwws_payload.attr("cccc")?.trim().to_string();
    let ttaaii = nwws_payload.attr("ttaaii")?.trim().to_string();
    let awipsid = nwws_payload
        .attr("awipsid")
        .unwrap_or("")
        .trim()
        .to_string();
    let issue = nwws_payload.attr("issue").unwrap_or("").trim().to_string();

    // Product text lives inside the <x> element body. Fall back to message
    // body for older feeds that put it there.
    let raw_text = {
        let inner = nwws_payload.text();
        if inner.trim().is_empty() {
            msg.bodies
                .iter()
                .next()
                .map(|(_lang, body)| body.clone())
                .unwrap_or_default()
        } else {
            inner
        }
    };

    if office.is_empty() || ttaaii.is_empty() {
        return None;
    }

    let delay_payload = msg
        .payloads
        .iter()
        .find(|p| p.name() == "delay" && p.ns() == "urn:xmpp:delay");
    let delay_stamp = delay_payload
        .and_then(|p| p.attr("stamp"))
        .map(|s| s.trim().to_string());

    Some(NwwsMessage {
        office,
        ttaaii,
        awipsid,
        issue,
        raw_text,
        delay_stamp,
    })
}

// ── type alias ──

type XmppClient = tokio_xmpp::Client;

// ── join_muc ──

/// Async function: connects to NWWS XMPP server and joins the MUC room.
/// Returns a tokio_xmpp Client on success.
pub(crate) async fn join_muc(
    user: &str,
    password: &str,
    server: &str,
) -> Result<XmppClient, String> {
    use std::str::FromStr;

    let jid_str = format!("{}@{}", user, server);
    let room_str = format!("nwws@conference.{}", server);
    let nick = user.to_string();

    eprintln!("[XMPP] Connecting to {} as {}", server, jid_str);

    // Create XMPP client using tokio-xmpp
    // The Jid type must be parsed from a string
    let jid = xmpp_parsers::jid::Jid::from_str(&jid_str)
        .map_err(|e| format!("Invalid JID '{}': {}", jid_str, e))?;

    let mut client = XmppClient::new(jid, password.to_owned());

    eprintln!("[XMPP] Async client created, waiting for online event...");

    // Wait for online event (the connection is established)
    let mut online = false;
    let mut attempts = 0;
    const MAX_ATTEMPTS: u32 = 50; // 5 seconds max wait (50 * 100ms)

    while !online && attempts < MAX_ATTEMPTS {
        if let Some(event) =
            tokio::time::timeout(tokio::time::Duration::from_millis(100), client.next())
                .await
                .ok()
                .flatten()
        {
            if event.is_online() {
                online = true;
                eprintln!("[XMPP] Client online");
                break;
            }
        }
        attempts += 1;
    }

    if !online {
        return Err("Timeout waiting for XMPP online event".to_string());
    }

    // Send presence stanza to join the MUC room
    // Must address presence to room@conference.server/nick to properly join MUC
    use xmpp_parsers::jid::FullJid;

    let room_jid_str = format!("{}/{}", room_str, nick);
    let room_jid = FullJid::from_str(&room_jid_str)
        .map_err(|e| format!("Invalid room JID '{}': {}", room_jid_str, e))?;

    let mut presence = Presence::new(PresenceType::None);
    presence.to = Some(room_jid.into());

    // Send the presence stanza (convert via Into trait)
    client
        .send_stanza(presence.into())
        .await
        .map_err(|e| format!("Failed to send presence stanza: {}", e))?;

    eprintln!("[XMPP] Presence stanza sent to {}/{}", room_str, nick);

    // Wait briefly for room join confirmation
    tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

    Ok(client)
}

// ── nwws_connection_loop ──

#[allow(clippy::too_many_arguments)]
pub(crate) async fn nwws_connection_loop(
    user: String,
    password: String,
    server: String,
    is_connected: Arc<AtomicBool>,
    tx: mpsc::UnboundedSender<NwwsMessage>,
    messages_received: Arc<AtomicU64>,
    messages_filtered: Arc<AtomicU64>,
    reconnect_count: Arc<AtomicU64>,
    last_error: Arc<RwLock<String>>,
) {
    let mut backoff_ms = 1000u64;
    const MAX_BACKOFF_MS: u64 = 60000;

    loop {
        eprintln!("[XMPP] Attempting connection to {}", server);

        // Attempt join_muc
        match join_muc(&user, &password, &server).await {
            Ok(mut client) => {
                is_connected.store(true, Ordering::Relaxed);
                eprintln!("[XMPP] Connected successfully, starting message loop");
                backoff_ms = 1000; // Reset backoff on success

                // Message loop: receive events from client and process them.
                // A single 30s read gap is normal, so we don't log it. Only
                // warn (once) if the feed stays silent for several gaps in a
                // row — a sign the connection may be stalled (TCP up, no data).
                const QUIET_TIMEOUTS_WARN: u32 = 5; // ~2.5 min of zero events
                let mut consecutive_timeouts: u32 = 0;
                let mut quiet_warned = false;
                let mut connection_error = false;
                while !connection_error {
                    match tokio::time::timeout(tokio::time::Duration::from_secs(30), client.next())
                        .await
                    {
                        Ok(Some(event)) => {
                            if quiet_warned {
                                eprintln!("[XMPP] Feed resumed after quiet period");
                            }
                            consecutive_timeouts = 0;
                            quiet_warned = false;
                            // Process event - check if it's a stanza
                            if let Some(stanza) = event.into_stanza() {
                                // Try to parse as message
                                if let Ok(message) = XmppMessage::try_from(stanza) {
                                    messages_received.fetch_add(1, Ordering::Relaxed);

                                    // Parse NWWS message
                                    if let Some(nwws_msg) = parse_xmpp_message(&message) {
                                        eprintln!(
                                            "[XMPP] Received: {} {} {}",
                                            nwws_msg.office, nwws_msg.ttaaii, nwws_msg.awipsid
                                        );

                                        // Send to channel
                                        if tx.send(nwws_msg).is_ok() {
                                            messages_filtered.fetch_add(1, Ordering::Relaxed);
                                        } else {
                                            eprintln!("[XMPP] Channel receiver dropped");
                                            connection_error = true;
                                        }
                                    }
                                }
                            }
                        }
                        Ok(None) => {
                            eprintln!("[XMPP] Connection closed by server");
                            connection_error = true;
                        }
                        Err(_) => {
                            // A single 30s read gap is expected; stay silent
                            // unless the feed has been quiet for several gaps
                            // in a row, then warn once until it recovers.
                            consecutive_timeouts += 1;
                            if consecutive_timeouts == QUIET_TIMEOUTS_WARN {
                                eprintln!(
                                    "[XMPP] No events for ~{}s — feed unusually quiet (connection may be stalled)",
                                    consecutive_timeouts * 30
                                );
                                quiet_warned = true;
                            }
                        }
                    }
                }

                is_connected.store(false, Ordering::Relaxed);
                eprintln!("[XMPP] Message loop ended, reconnecting...");
            }
            Err(e) => {
                eprintln!("[XMPP] Connection failed: {}", e);
                if let Ok(mut last_err) = last_error.write() {
                    *last_err = e.clone();
                }
                reconnect_count.fetch_add(1, Ordering::Relaxed);
            }
        }

        // Exponential backoff
        eprintln!("[XMPP] Reconnecting in {}ms...", backoff_ms);
        tokio::time::sleep(tokio::time::Duration::from_millis(backoff_ms)).await;
        backoff_ms = std::cmp::min(backoff_ms * 2, MAX_BACKOFF_MS);
    }
}

// ── nwws_start ──

#[pyfunction]
pub fn nwws_start(user: &str, password: &str, server: &str) -> PyResult<()> {
    let user_str = user.to_string();
    let password_str = password.to_string();
    let server_str = server.to_string();

    // Create unbounded channel for async message forwarding
    let (tx, rx) = mpsc::unbounded_channel::<NwwsMessage>();

    // Counters and state
    let is_connected = Arc::new(AtomicBool::new(false));
    let messages_received = Arc::new(AtomicU64::new(0));
    let messages_filtered = Arc::new(AtomicU64::new(0));
    let messages_drained = Arc::new(AtomicU64::new(0));
    let reconnect_count = Arc::new(AtomicU64::new(0));
    let last_error = Arc::new(RwLock::new(String::new()));

    // Build tokio runtime with timers enabled for sleep/backoff
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("spc-xmpp")
        .enable_all()
        .build()
        .map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Failed to create tokio runtime: {e}"
            ))
        })?;

    // Clone arcs for async task
    let is_connected_task = Arc::clone(&is_connected);
    let messages_received_task = Arc::clone(&messages_received);
    let messages_filtered_task = Arc::clone(&messages_filtered);
    let reconnect_count_task = Arc::clone(&reconnect_count);
    let last_error_task = Arc::clone(&last_error);
    let tx_task = tx.clone();

    // Spawn connection loop on runtime
    runtime.spawn(nwws_connection_loop(
        user_str,
        password_str,
        server_str,
        is_connected_task,
        tx_task,
        messages_received_task,
        messages_filtered_task,
        reconnect_count_task,
        last_error_task,
    ));

    // Store state
    let mut state = NWWS_STATE.write().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    *state = Some(NwwsState {
        _runtime_handle: runtime,
        _sender: tx,
        receiver: Arc::new(std::sync::Mutex::new(rx)),
        is_connected,
        messages_received,
        messages_filtered,
        messages_drained,
        reconnect_count,
        last_error,
    });

    Ok(())
}

// ── nwws_stop ──

#[pyfunction]
pub fn nwws_stop() -> PyResult<()> {
    let mut state = NWWS_STATE.write().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;
    *state = None;
    Ok(())
}

// ── nwws_try_recv ──

#[pyfunction]
pub fn nwws_try_recv<'py>(py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
    let state = NWWS_STATE.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    if state.is_none() {
        return Ok(None);
    }

    let state = state.as_ref().unwrap();
    let mut receiver = state.receiver.lock().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("receiver lock poisoned: {e}"))
    })?;

    match receiver.try_recv() {
        Ok(msg) => {
            state.messages_drained.fetch_add(1, Ordering::Relaxed);
            let dict = PyDict::new(py);
            dict.set_item("office", msg.office.clone())?;
            dict.set_item("cccc", msg.office)?;
            dict.set_item("ttaaii", msg.ttaaii)?;
            dict.set_item("awipsid", msg.awipsid)?;
            dict.set_item("issue", msg.issue)?;
            dict.set_item("raw_text", msg.raw_text.clone())?;
            dict.set_item("text", msg.raw_text)?;
            dict.set_item("delay_stamp", msg.delay_stamp)?;
            Ok(Some(dict))
        }
        Err(mpsc::error::TryRecvError::Empty) => Ok(None),
        Err(mpsc::error::TryRecvError::Disconnected) => Err(
            pyo3::exceptions::PyRuntimeError::new_err("XMPP channel disconnected"),
        ),
    }
}

// ── nwws_is_connected ──

#[pyfunction]
pub fn nwws_is_connected() -> PyResult<bool> {
    let state = NWWS_STATE.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    if let Some(s) = state.as_ref() {
        Ok(s.is_connected.load(Ordering::Relaxed))
    } else {
        Ok(false)
    }
}

// ── nwws_stats ──

#[pyfunction]
pub fn nwws_stats<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
    let state = NWWS_STATE.read().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("NWWS_STATE lock poisoned: {e}"))
    })?;

    let dict = PyDict::new(py);

    if let Some(s) = state.as_ref() {
        let msg_received = s.messages_received.load(Ordering::Relaxed);
        let msg_drained = s.messages_drained.load(Ordering::Relaxed);
        let reconnect_ct = s.reconnect_count.load(Ordering::Relaxed);
        let last_err = s.last_error.read().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("last_error lock poisoned: {e}"))
        })?;

        dict.set_item("messages_received", msg_received)?;
        dict.set_item("messages_drained", msg_drained)?;
        dict.set_item("queue_depth", msg_received.saturating_sub(msg_drained))?;
        dict.set_item(
            "messages_filtered",
            s.messages_filtered.load(Ordering::Relaxed),
        )?;
        dict.set_item("reconnect_count", reconnect_ct)?;
        dict.set_item("last_error", last_err.clone())?;
        dict.set_item("is_connected", s.is_connected.load(Ordering::Relaxed))?;
    } else {
        dict.set_item("messages_received", 0)?;
        dict.set_item("messages_drained", 0)?;
        dict.set_item("queue_depth", 0u64)?;
        dict.set_item("messages_filtered", 0)?;
        dict.set_item("reconnect_count", 0)?;
        dict.set_item("last_error", "")?;
        dict.set_item("is_connected", false)?;
    }

    Ok(dict)
}

// ── S3_CLIENT+imports ──

static S3_CLIENT: Lazy<Client> = Lazy::new(|| {
    Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap_or_else(|_| Client::new())
});

// ── fetch_s3_vad_fast ──

#[pyfunction]
pub fn fetch_s3_vad_fast<'py>(
    py: Python<'py>,
    rid: &str,
) -> PyResult<Option<pyo3::Py<pyo3::types::PyBytes>>> {
    let rid_upper = rid.to_uppercase();

    let bucket =
        std::env::var("VAD_S3_BUCKET").unwrap_or_else(|_| "unidata-nexrad-level3".to_string());
    let now = chrono::Utc::now();

    let mut candidates = vec![rid_upper.clone()];
    if rid_upper.starts_with('K') && rid_upper.len() == 4 {
        candidates.push(rid_upper[1..].to_string());
    }

    for days_back in 0..3 {
        let date = now - chrono::Duration::try_days(days_back).unwrap_or_default();
        let date_str = date.format("%Y_%m_%d").to_string();

        for site_id in &candidates {
            // Predictive fetch: we can guess the exact time, but the minute/second varies.
            // Since we can't reliably guess, we list the bucket.
            let prefix = format!("{}_NVW_{}", site_id, date_str);
            let url = format!(
                "https://{}.s3.amazonaws.com/?list-type=2&prefix={}",
                bucket, prefix
            );

            if let Ok(resp) = S3_CLIENT.get(&url).send() {
                if let Ok(text) = resp.text() {
                    if let Ok(root) = text.parse::<minidom::Element>() {
                        let mut latest_key = None;
                        for child in root.children() {
                            if child.name() == "Contents" {
                                if let Some(key_elem) = child.get_child("Key", child.ns().as_str())
                                {
                                    let key = key_elem.text();
                                    if key.starts_with(&format!("{}_NVW", site_id)) {
                                        latest_key = Some(key);
                                    }
                                }
                            }
                        }

                        if let Some(key) = latest_key {
                            let obj_url = format!("https://{}.s3.amazonaws.com/{}", bucket, key);
                            if let Ok(mut obj_resp) = S3_CLIENT.get(&obj_url).send() {
                                let mut buf = Vec::new();
                                if obj_resp.copy_to(&mut buf).is_ok() {
                                    // Depending on PyO3 version, creating a PyBytes
                                    // In PyO3 0.20, PyBytes::new returns &PyBytes and we call .into()
                                    // In PyO3 0.21, we use new_bound.
                                    // Let's use `pyo3::types::PyBytes::new` which works in both if we handle the return type.
                                    let py_bytes = pyo3::types::PyBytes::new(py, &buf);
                                    return Ok(Some(py_bytes.into()));
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(None)
}
