//! OpenTTD -> QuestDB telemetry bridge.
//!
//! Connects to an OpenTTD dedicated server's *admin port*, subscribes to the
//! GameScript channel, and relays the vehicle telemetry that our GameScript
//! emits via `GSAdmin.Send(...)` into QuestDB using the official `questdb-rs`
//! InfluxDB-Line-Protocol client.
//!
//! The OpenTTD side is fully sandboxed: a GameScript cannot open sockets or
//! files, but it *can* push JSON to admin-port clients. This process is that
//! client. Nothing in OpenTTD is patched.
//!
//! Admin protocol reference (OpenTTD 15.x, NETWORK_GAME_ADMIN_VERSION = 3):
//!   packet wire format = [u16 LE total_size][u8 type][payload...]
//!     - total_size INCLUDES the 2 size bytes and the 1 type byte
//!     - integers are little-endian, strings are raw bytes + a '\0' terminator
//!   see src/network/core/{tcp_admin.h,packet.cpp} in the OpenTTD source.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant};

use questdb::ingress::{Sender, TimestampNanos};

// ---- Admin packet types (PacketAdminType, src/network/core/tcp_admin.h) ----
const ADMIN_PACKET_ADMIN_JOIN: u8 = 0;
const ADMIN_PACKET_ADMIN_UPDATE_FREQUENCY: u8 = 2;

const ADMIN_PACKET_SERVER_ERROR: u8 = 102;
const ADMIN_PACKET_SERVER_PROTOCOL: u8 = 103;
const ADMIN_PACKET_SERVER_WELCOME: u8 = 104;
const ADMIN_PACKET_SERVER_NEWGAME: u8 = 105;
const ADMIN_PACKET_SERVER_SHUTDOWN: u8 = 106;
const ADMIN_PACKET_SERVER_GAMESCRIPT: u8 = 124;

// AdminUpdateType::Gamescript = 9; AdminUpdateFrequency::Automatic = enum #6 -> bit (1<<6).
const ADMIN_UPDATE_GAMESCRIPT: u16 = 9;
const ADMIN_FREQUENCY_AUTOMATIC: u16 = 1 << 6;

/// OpenTTD's internal speed unit -> km/h (see ScriptVehicle::GetCurrentSpeed docs).
const SPEED_TO_KMH: f64 = 1.00584;

struct Config {
    admin_host: String,
    admin_port: u16,
    admin_password: String,
    admin_name: String,
    questdb_conf: String,
    table: String,
    company_table: String,
}

impl Config {
    fn from_env() -> Self {
        let env = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
        Config {
            admin_host: env("ADMIN_HOST", "127.0.0.1"),
            admin_port: env("ADMIN_PORT", "3977").parse().unwrap_or(3977),
            admin_password: env("ADMIN_PASSWORD", "questdb"),
            admin_name: env("ADMIN_NAME", "questdb-bridge"),
            questdb_conf: env("QUESTDB_CONF", "http::addr=127.0.0.1:9000;"),
            table: env("TABLE", "vehicle_telemetry"),
            company_table: env("COMPANY_TABLE", "company_economy"),
        }
    }
}

fn main() {
    let cfg = Config::from_env();
    println!(
        "[bridge] OpenTTD admin {}:{}  ->  QuestDB ({})  table={}",
        cfg.admin_host, cfg.admin_port, cfg.questdb_conf, cfg.table
    );

    // The HTTP sender is created lazily; from_conf does not connect yet, so it
    // is fine if QuestDB is not up at this exact moment.
    let mut sender = match Sender::from_conf(&cfg.questdb_conf) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[bridge] FATAL: bad QUESTDB_CONF '{}': {e}", cfg.questdb_conf);
            std::process::exit(1);
        }
    };

    // Outer loop: keep (re)connecting to the OpenTTD admin port forever, so the
    // bridge survives the server not being up yet, restarts and new games.
    let mut backoff = 3u64;
    loop {
        let started = Instant::now();
        match run_session(&cfg, &mut sender) {
            Ok(()) => eprintln!("[bridge] admin connection closed"),
            Err(e) => eprintln!("[bridge] session error: {e}"),
        }
        // A session that stayed up for a while resets the backoff.
        if started.elapsed() >= Duration::from_secs(30) {
            backoff = 3;
        }
        eprintln!("[bridge] reconnecting in {backoff}s");
        std::thread::sleep(Duration::from_secs(backoff));
        backoff = (backoff * 2).min(30);
    }
}

/// One full admin-port session: connect, authenticate, subscribe, relay.
fn run_session(cfg: &Config, sender: &mut Sender) -> std::io::Result<()> {
    let addr = format!("{}:{}", cfg.admin_host, cfg.admin_port);
    let mut stream = TcpStream::connect(&addr)?;
    stream.set_nodelay(true).ok();
    println!("[bridge] connected to admin port {addr}");

    send_join(&mut stream, &cfg.admin_password, &cfg.admin_name)?;

    let mut rows_total: u64 = 0;
    let mut dropped: u64 = 0;
    let mut last_report = Instant::now();
    // company/owner id -> in-game name, learned from each company-economy message
    let mut company_names: HashMap<i64, String> = HashMap::new();

    loop {
        let (ptype, payload) = read_packet(&mut stream)?;
        match ptype {
            ADMIN_PACKET_SERVER_PROTOCOL => { /* capabilities list; ignore */ }
            ADMIN_PACKET_SERVER_WELCOME => {
                println!("[bridge] authenticated; subscribing to GameScript channel");
                send_subscribe_gamescript(&mut stream)?;
            }
            ADMIN_PACKET_SERVER_NEWGAME => {
                println!("[bridge] server started a new game; re-subscribing");
                send_subscribe_gamescript(&mut stream)?;
            }
            ADMIN_PACKET_SERVER_SHUTDOWN => {
                println!("[bridge] server is shutting down");
                return Ok(());
            }
            ADMIN_PACKET_SERVER_ERROR => {
                let code = payload.first().copied().unwrap_or(255);
                return Err(std::io::Error::other(format!(
                    "server rejected admin connection (NetworkErrorCode={code}); \
                     check admin_password / allow_insecure_admin_login"
                )));
            }
            ADMIN_PACKET_SERVER_GAMESCRIPT => {
                let json = trim_cstr(&payload);
                match relay_message(json, cfg, sender, &mut company_names) {
                    Ok(n) => {
                        rows_total += n as u64;
                        if last_report.elapsed() >= Duration::from_secs(5) {
                            println!("[bridge] ingested {rows_total} rows so far");
                            last_report = Instant::now();
                        }
                    }
                    Err(e) => {
                        dropped += 1;
                        eprintln!("[bridge] ingest error (dropped batch #{dropped}): {e}");
                    }
                }
            }
            _ => { /* date/company/chat/etc. — not subscribed, ignore */ }
        }
    }
}

/// Parse one GameScript JSON message and write its rows to QuestDB.
///
/// A message is either vehicle telemetry (`"v"`) or company economy (`"c"`).
/// The QuestDB designated timestamp stays as the real ingest time (so the
/// dashboards are genuinely "live"); the in-game calendar date is carried
/// alongside in the `game_year` (LONG) and `game_date` (STRING) columns.
fn relay_message(
    json: &str,
    cfg: &Config,
    sender: &mut Sender,
    names: &mut HashMap<i64, String>,
) -> Result<usize, Box<dyn std::error::Error>> {
    let v: serde_json::Value = serde_json::from_str(json)?;

    // In-game calendar date for this message (same for all its rows).
    let year = v.get("y").and_then(|x| x.as_i64()).unwrap_or(0);
    let month = v.get("mo").and_then(|x| x.as_i64()).unwrap_or(1);
    let day = v.get("dy").and_then(|x| x.as_i64()).unwrap_or(1);
    let game_date = format!("{year:04}-{month:02}-{day:02}");

    if let Some(rows) = v.get("v").and_then(|x| x.as_array()) {
        relay_vehicles(rows, year, &game_date, cfg, sender, names)
    } else if let Some(rows) = v.get("c").and_then(|x| x.as_array()) {
        relay_companies(rows, year, &game_date, cfg, sender, names)
    } else {
        Ok(0)
    }
}

/// `<vrec> = [vid, vtype, owner, x, y, speed, max_speed, reliability, state,
///            age, max_age, profit, profit_last, value, cargo_cap, cargo_load,
///            running_cost]`
fn relay_vehicles(
    rows: &[serde_json::Value],
    year: i64,
    game_date: &str,
    cfg: &Config,
    sender: &mut Sender,
    names: &HashMap<i64, String>,
) -> Result<usize, Box<dyn std::error::Error>> {
    let mut buffer = sender.new_buffer();
    let mut count = 0usize;

    for row in rows {
        let a = match row.as_array() {
            Some(a) if a.len() >= 17 => a,
            _ => continue,
        };
        let g = |i: usize| a[i].as_i64().unwrap_or(0);
        let (vid, vtype, owner, x, y) = (g(0), g(1), g(2), g(3), g(4));
        let (raw_speed, raw_max_speed, reliability, state) = (g(5), g(6), g(7), g(8));
        let (age, max_age, profit, profit_last, value) = (g(9), g(10), g(11), g(12), g(13));
        let (cargo_cap, cargo_load, running_cost) = (g(14), g(15), g(16));

        // Skip vehicles with no real owner (e.g. crashed -> OWNER_NONE/INVALID).
        if !(0..=14).contains(&owner) {
            continue;
        }

        let company = names
            .get(&owner)
            .cloned()
            .unwrap_or_else(|| format!("Company {}", owner + 1));
        let speed = raw_speed as f64 * SPEED_TO_KMH;
        let max_speed = raw_max_speed as f64 * SPEED_TO_KMH;
        let speed_pct = pct(raw_speed, raw_max_speed);
        let load_pct = pct(cargo_load, cargo_cap);
        let age_pct = pct(age, max_age);

        buffer
            .table(cfg.table.as_str())?
            .symbol("company", company.as_str())?
            .symbol("vtype", vehicle_type_name(vtype))?
            .symbol("state", vehicle_state_name(state))?
            .column_i64("vid", vid)?
            .column_i64("x", x)?
            .column_i64("y", y)?
            .column_f64("speed", speed)?
            .column_f64("max_speed", max_speed)?
            .column_f64("speed_pct", speed_pct)?
            .column_i64("reliability", reliability)?
            .column_i64("age", age)?
            .column_i64("max_age", max_age)?
            .column_f64("age_pct", age_pct)?
            .column_i64("profit", profit)?
            .column_i64("profit_last", profit_last)?
            .column_i64("value", value)?
            .column_i64("cargo_cap", cargo_cap)?
            .column_i64("cargo_load", cargo_load)?
            .column_f64("load_pct", load_pct)?
            .column_i64("running_cost", running_cost)?
            .column_i64("game_year", year)?
            .column_str("game_date", game_date)?
            .at(TimestampNanos::now())?;
        count += 1;
    }

    if count > 0 {
        sender.flush(&mut buffer)?;
    }
    Ok(count)
}

/// `<crec> = [cid, money, company_value, income, expenses, performance, cargo_delivered, name]`
/// (name is optional at index 7 — older GameScripts omit it and we synthesize a label)
fn relay_companies(
    rows: &[serde_json::Value],
    year: i64,
    game_date: &str,
    cfg: &Config,
    sender: &mut Sender,
    names: &mut HashMap<i64, String>,
) -> Result<usize, Box<dyn std::error::Error>> {
    let mut buffer = sender.new_buffer();
    let mut count = 0usize;

    for row in rows {
        let a = match row.as_array() {
            Some(a) if a.len() >= 7 => a,
            _ => continue,
        };
        let g = |i: usize| a[i].as_i64().unwrap_or(0);
        let (cid, money, value, income, raw_expenses, performance, cargo) =
            (g(0), g(1), g(2), g(3), g(4), g(5), g(6));

        if !(0..=14).contains(&cid) {
            continue;
        }
        // OpenTTD reports quarterly expenses as a NEGATIVE amount. Store the
        // positive magnitude and compute profit = income + raw_expenses.
        // Checked arithmetic so a corrupt payload can never panic (debug) or wrap.
        let expenses = raw_expenses.checked_neg().unwrap_or(0);
        let profit_q = income.checked_sub(expenses).unwrap_or(0);
        // Real in-game name, appended at index 7 by newer GameScripts; older
        // messages omit it, so fall back to a synthetic label.
        let company = a
            .get(7)
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("Company {}", cid + 1));
        names.insert(cid, company.clone());

        let mut b = buffer
            .table(cfg.company_table.as_str())?
            .symbol("company", company.as_str())?
            .column_i64("money", money)?
            .column_i64("company_value", value)?
            .column_i64("income", income)?
            .column_i64("expenses", expenses)?
            .column_i64("profit_q", profit_q)?
            .column_i64("cargo_delivered", cargo)?;
        // Performance is 0 for a brand-new company and a 0-1000 rating after the
        // first completed quarter; -1 only signals an API failure (omit only then).
        if performance >= 0 {
            b = b.column_i64("performance", performance)?;
        }
        b.column_i64("game_year", year)?
            .column_str("game_date", game_date)?
            .at(TimestampNanos::now())?;
        count += 1;
    }

    if count > 0 {
        sender.flush(&mut buffer)?;
    }
    Ok(count)
}

/// Percentage of `part` over `whole`, 0.0 when `whole` is non-positive.
fn pct(part: i64, whole: i64) -> f64 {
    if whole > 0 {
        part as f64 / whole as f64 * 100.0
    } else {
        0.0
    }
}

fn vehicle_type_name(t: i64) -> &'static str {
    match t {
        0 => "rail",
        1 => "road",
        2 => "water",
        3 => "air",
        _ => "other",
    }
}

fn vehicle_state_name(s: i64) -> &'static str {
    match s {
        0 => "running",
        1 => "stopped",
        2 => "depot",
        3 => "station",
        4 => "broken",
        5 => "crashed",
        _ => "unknown",
    }
}

// ---------------------------------------------------------------------------
// Admin-port wire helpers
// ---------------------------------------------------------------------------

/// Frame and send a packet: [u16 LE size][u8 type][payload]; size includes all.
fn send_packet(stream: &mut TcpStream, ptype: u8, payload: &[u8]) -> std::io::Result<()> {
    let size = (payload.len() + 3) as u16; // 2 (size) + 1 (type) + payload
    let mut pkt = Vec::with_capacity(size as usize);
    pkt.extend_from_slice(&size.to_le_bytes());
    pkt.push(ptype);
    pkt.extend_from_slice(payload);
    stream.write_all(&pkt)
}

/// Read one packet, returning (type, payload-without-type).
fn read_packet(stream: &mut TcpStream) -> std::io::Result<(u8, Vec<u8>)> {
    let mut size_buf = [0u8; 2];
    stream.read_exact(&mut size_buf)?;
    let size = u16::from_le_bytes(size_buf) as usize;
    if size < 3 {
        return Err(std::io::Error::other("packet too small"));
    }
    let mut rest = vec![0u8; size - 2]; // type byte + payload
    stream.read_exact(&mut rest)?;
    let ptype = rest[0];
    Ok((ptype, rest[1..].to_vec()))
}

/// PACKET_ADMIN_JOIN: string password, string app-name, string app-version.
fn send_join(stream: &mut TcpStream, password: &str, name: &str) -> std::io::Result<()> {
    let mut p = Vec::new();
    push_cstr(&mut p, password);
    push_cstr(&mut p, name);
    push_cstr(&mut p, env!("CARGO_PKG_VERSION"));
    send_packet(stream, ADMIN_PACKET_ADMIN_JOIN, &p)
}

/// PACKET_ADMIN_UPDATE_FREQUENCY: u16 update type, u16 frequency bitset.
fn send_subscribe_gamescript(stream: &mut TcpStream) -> std::io::Result<()> {
    let mut p = Vec::new();
    p.extend_from_slice(&ADMIN_UPDATE_GAMESCRIPT.to_le_bytes());
    p.extend_from_slice(&ADMIN_FREQUENCY_AUTOMATIC.to_le_bytes());
    send_packet(stream, ADMIN_PACKET_ADMIN_UPDATE_FREQUENCY, &p)
}

fn push_cstr(buf: &mut Vec<u8>, s: &str) {
    buf.extend_from_slice(s.as_bytes());
    buf.push(0);
}

/// Interpret a null-terminated byte payload as &str (drops the trailing '\0').
fn trim_cstr(payload: &[u8]) -> &str {
    let end = payload.iter().position(|&b| b == 0).unwrap_or(payload.len());
    std::str::from_utf8(&payload[..end]).unwrap_or("")
}
