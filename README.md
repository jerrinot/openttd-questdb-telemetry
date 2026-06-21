# OpenTTD → QuestDB live vehicle telemetry

A QuestDB demo that streams **live telemetry** out of a running
[OpenTTD](https://www.openttd.org/) game — every train, truck, ship and plane's
position, speed, load, reliability and profit, plus per-company economics
(value, cash, income/expenses, performance, cargo) — into
[QuestDB](https://questdb.io/), visualised live across three Grafana dashboards.

The neat part: **OpenTTD is not patched or recompiled.** Everything is done with
a stock GameScript plus an external bridge.

```
┌──────────────────── OpenTTD dedicated server (host) ───────────────────┐
│  GameScript (Squirrel, runs as "deity" → sees ALL vehicles & companies)│
│    every ~1s: per-vehicle telemetry + per-company economy as JSON      │
│    GSAdmin.Send({...})  ───────────────►  admin port (TCP :3977)       │
└────────────────────────────────────────────────┬──────────────────────┘
                                                   │ PACKET_SERVER_GAMESCRIPT (JSON)
                                       ┌───────────▼────────────┐
                                       │  bridge (Rust)         │  docker
                                       │  admin-port client     │
                                       │  → questdb-rs (ILP/HTTP)│
                                       └───────────┬────────────┘
                                       ┌───────────▼────────────┐
                                       │  QuestDB  :9000 / :8812 │  docker
                                       └───────────┬────────────┘
                                       ┌───────────▼────────────┐
                                       │  Grafana  :3000         │  docker
                                       └─────────────────────────┘
```

## Why it works (the design constraint that shapes everything)

OpenTTD GameScripts are **fully sandboxed** — no file or socket access. But they
*can* push arbitrary JSON to the network **admin port** via `GSAdmin.Send()`.
So:

- a small **GameScript** collects per-vehicle telemetry and per-company economy
  and emits them as JSON, and
- an external **bridge** connects to the admin port, receives that JSON, and
  writes it to two QuestDB tables (`vehicle_telemetry`, `company_economy`).

The only real limit is that `GSAdmin.Send()` drops any message whose JSON exceeds
**1450 bytes**, so the GameScript sends vehicles in compact batches of ~15.

There is **no native per-vehicle data on the admin port** (only company-level
aggregates), which is exactly why the GameScript relay is required rather than
just reading the admin protocol directly.

## Components

| Path | What it is |
|------|------------|
| `gamescript/questdb_telemetry/` | The OpenTTD GameScript (Squirrel). Reads all vehicles + companies, emits JSON. |
| `bridge/` | Rust admin-port → QuestDB bridge, using the official `questdb-rs` client. Containerised. |
| `docker-compose.yml` | QuestDB + Grafana + bridge. |
| `grafana/dashboards/` | Three provisioned dashboards (Overview / Fleet Operations / Economy). |
| `grafana/generate_dashboards.py` | Generator that builds the dashboard JSON from verified panel templates. |
| `config/demo.cfg` | OpenTTD server config (admin port, GameScript, AI competitors). |
| `scripts/` | `run_demo.sh`, `stop_demo.sh`, `install_gamescript.sh`. |

## Prerequisites

- **OpenTTD 15.x** (tested with 15.2). The scripts auto-detect
  `~/Downloads/openttd-15.2-linux-generic-amd64/openttd` or `openttd` on `PATH`;
  otherwise set `OPENTTD=/path/to/openttd`.
- **Docker** with the Compose plugin.
- **At least one AI** installed in OpenTTD (one-time, see below). The AI
  companies are what build networks and run the vehicles we measure.

### One-time: download an AI

The demo needs AI competitors to generate moving vehicles. Download one via the
in-game content browser:

1. Launch OpenTTD normally.
2. Main menu → **Check Online Content**.
3. Find the AIs, tick e.g. **AdmiralAI** (a solid all-rounder that builds road,
   rail, air and sea), then **Download**.
4. Quit.

(If you skip this, `run_demo.sh` stops with instructions, because OpenTTD aborts
when asked to start a random AI and none are installed.)

## Run it

```bash
./scripts/run_demo.sh
```

That will:
1. `docker compose up -d --build` — start QuestDB, Grafana and the bridge.
2. Symlink the GameScript into `~/.local/share/openttd/game/`.
3. Launch an OpenTTD **dedicated server** in the foreground (Ctrl-C to stop).

Then open:

- **Grafana** → http://localhost:3000/dashboards — Overview / Fleet Operations / Economy (anonymous, no login)
- **QuestDB console** → http://localhost:9000

Give the AIs a couple of minutes to build their first routes; rows start landing
in QuestDB the moment vehicles begin to move. The bridge reconnects on its own,
so the order you start things in doesn't matter.

Tear down the data plane (QuestDB/Grafana/bridge) with:

```bash
./scripts/stop_demo.sh          # also removes the QuestDB data volume
KEEP_DATA=1 ./scripts/stop_demo.sh   # keep the data volume
```

### Or play it yourself (your own game, in the GUI)

Prefer to drive a real game instead of the AI-only dedicated server? Use the
provided `config/play.cfg` so the admin port + GameScript are enabled without
touching your own `~/.config/openttd/openttd.cfg`:

```bash
docker compose up -d --build         # QuestDB + Grafana + bridge
./scripts/install_gamescript.sh      # so OpenTTD finds the GameScript
openttd -c config/play.cfg -x        # launch the GUI client with the demo config
```

Then, **in OpenTTD → Multiplayer → New Game** (you build the vehicles, or set
`max_no_competitors` in `play.cfg` and add AIs). Two things that will otherwise
silently give you no data:

- **You must host a Multiplayer game, not single-player.** The admin port the
  bridge reads only exists on a *server*; single-player has no admin port.
- **Launch with `-x`.** `admin_password` is a "secret" setting that OpenTTD
  strips out of the config file on exit; `-x` (don't save config) keeps it in
  `play.cfg` so the admin port stays password-protected and the bridge can log in.

The "QuestDB Telemetry" GameScript auto-loads from `play.cfg` — nothing to click.
Confirm the bridge connected with `docker compose logs -f bridge`.

### No game at all — just see the dashboards

```bash
docker compose up -d questdb grafana
python3 scripts/seed_fake_data.py    # ~15 min of realistic synthetic telemetry
```

Re-run the seeder to refresh; the 60-second "snapshot" panels need recent data.

## What you'll see

In the **QuestDB console** (http://localhost:9000):

```sql
SELECT * FROM vehicle_telemetry LIMIT 20;

-- live fleet speed
SELECT timestamp, avg(speed), max(speed)
FROM vehicle_telemetry
WHERE timestamp > dateadd('m', -5, now())
SAMPLE BY 1s;

-- latest position of every vehicle (the "map")
SELECT vid, company, vtype, x, y, speed
FROM vehicle_telemetry
LATEST ON timestamp PARTITION BY vid;
```

In **Grafana** — three auto-provisioned dashboards (anonymous, no login):

- **Overview** (`/d/openttd-overview`) — KPIs with sparklines, fleet speed, the
  live X/Y position "map", vehicle mix, company value over time.
- **Fleet Operations** (`/d/openttd-fleet`) — load factor & reliability, a
  reliability histogram, a speed-over-time heatmap, vehicle-state mix, a stacked
  count by type, and a colour-coded "worst performers" table.
- **Economy & Profitability** (`/d/openttd-economy`) — company value & cash over
  time, income vs expenses, performance ratings, profit / cargo leaderboards,
  and a colour-coded company scoreboard.

The dashboards are generated by `grafana/generate_dashboards.py` from small,
verified panel templates — edit/extend that and re-run it rather than hand-editing
the JSON.

## QuestDB Notebooks (native dashboards, no Grafana)

QuestDB's web console (the upcoming **Notebooks** feature) can host the dashboard
itself — live, auto-refreshing charts right next to the database, no Grafana.
`docker compose up` already serves that console: the `questdb` service mounts a
prebuilt console (`questdb/console/`, the `feat/notebooks` web-console build) and
points QuestDB at it via `QDB_HTTP_STATIC_PUBLIC_DIRECTORY=/console`. QuestDB
extracts its *own* console to a hardcoded `<root>/public` but *serves* from that
dir, so the two never collide — **no custom QuestDB build needed**.

To load the prebuilt dashboard:

1. Open http://localhost:9000 (the Notebooks console).
2. Tab bar **⋮ → Import tabs** → choose
   `questdb/notebook/openttd-telemetry-notebook.json`.
3. Open the new **OpenTTD Telemetry** notebook tab.

You get line/area/scatter/bar/pie chart cells (fleet speed, a live X/Y position
map, vehicles by type, company value & cash, profit leaderboard, vehicle mix),
each with **auto-refresh on** (2–60 s adaptive polling, `now()`-relative windows
= rolling live view). The notebook is generated by
`questdb/notebook/generate_notebook.py` (a Dexie-export the console's importer
accepts) — edit/extend and re-run it.

Notes: the console here is a preview build off the `feat/notebooks` branch.
Notebooks live in browser IndexedDB, so it's an *import* (one click), not an
auto-seed. Heatmap and KPI-stat tiles have no native chart type yet (the Grafana
set keeps those).

## Data model

ILP auto-creates two tables on first write.

`vehicle_telemetry` (one row per vehicle per sample):

| Column | Type | Notes |
|--------|------|-------|
| `timestamp` | TIMESTAMP | designated timestamp (ingest wall-clock) |
| `company` | SYMBOL | `Company 1` … `Company 15` |
| `vtype` | SYMBOL | `rail` / `road` / `water` / `air` |
| `state` | SYMBOL | `running` / `stopped` / `depot` / `station` / `broken` / `crashed` |
| `vid` | LONG | vehicle id |
| `x`, `y` | LONG | map tile coordinates |
| `speed`, `max_speed` | DOUBLE | km/h (converted from internal units) |
| `speed_pct` | DOUBLE | speed as % of the engine's max |
| `reliability` | LONG | 0–100 |
| `age`, `max_age` | LONG | days; `age_pct` is the ratio |
| `profit`, `profit_last` | LONG | profit this / last year |
| `value` | LONG | current resale value |
| `cargo_cap`, `cargo_load` | LONG | total capacity / load across cargo types |
| `load_pct` | DOUBLE | utilisation, `cargo_load / cargo_cap` |
| `running_cost` | LONG | engine running cost per year |
| `game_year` / `game_date` | LONG / VARCHAR | in-game calendar |

`company_economy` (one row per company per sample):

| Column | Type | Notes |
|--------|------|-------|
| `timestamp` | TIMESTAMP | designated timestamp (ingest wall-clock) |
| `company` | SYMBOL | company name |
| `money` | LONG | bank balance (cash) |
| `company_value` | LONG | net worth |
| `income`, `expenses` | LONG | current quarter |
| `profit_q` | LONG | `income − expenses` |
| `performance` | LONG | rating 0–1000 (null until the first quarter completes) |
| `cargo_delivered` | LONG | units delivered this quarter |
| `game_year` / `game_date` | LONG / VARCHAR | in-game calendar |

The designated `timestamp` is the **real ingest time**, on purpose: it keeps the
dashboards genuinely live (real-time `SAMPLE BY`, auto-refresh, `now()` windows).
In-game time runs ~43,000× faster than real time and starts before 1970 (which
QuestDB can't store as a timestamp), so the game's own clock is carried in the
`game_year` / `game_date` columns instead — queryable and shown on each dashboard.

## Configuration

Bridge (set in `docker-compose.yml`):

| Env var | Default | Meaning |
|---------|---------|---------|
| `ADMIN_HOST` | `host.docker.internal` | OpenTTD server host (the bridge reaches the host from its container) |
| `ADMIN_PORT` | `3977` | admin port |
| `ADMIN_PASSWORD` | `questdb` | must match `admin_password` in `config/demo.cfg` |
| `QUESTDB_CONF` | `http::addr=questdb:9000;` | `questdb-rs` connection string |
| `TABLE` | `vehicle_telemetry` | vehicle telemetry table |
| `COMPANY_TABLE` | `company_economy` | company economy table |

Sampling rate and batch size live in `gamescript/questdb_telemetry/main.nut`
(`SLEEP_TICKS`, `BATCH`). ~37 in-game ticks ≈ 1 second.

## Building / hacking the bridge locally

```bash
cd bridge
cargo run          # talks to 127.0.0.1:3977 and 127.0.0.1:9000 by default
```

## Troubleshooting

- **No rows in QuestDB.** Vehicles may not exist yet — wait for the AIs to build.
  Check the bridge log: `docker compose logs -f bridge`. You should see
  `authenticated; subscribing to GameScript channel` then `ingested N rows`.
- **Bridge can't reach the admin port.** Confirm the server is up and the
  GameScript is active (server console shows the GameScript start message). On
  Linux the container reaches the host via the `host.docker.internal:host-gateway`
  mapping already set in `docker-compose.yml`.
- **`server rejected admin connection`.** `admin_password` in `config/demo.cfg`
  must match `ADMIN_PASSWORD`, and `allow_insecure_admin_login` must be `true`.
- **OpenTTD aborts on start.** Usually means `max_no_competitors > 0` but no AI
  is installed — download an AI (above).
- **Grafana “datasource not found” / empty panels.** QuestDB may still be
  starting; reload after a few seconds. The "current snapshot" panels (map,
  histogram, state mix, scoreboard tables) use a 60-second window, so they need
  recent data — they fill once telemetry is flowing.
- **Port already in use.** QuestDB uses 9000/8812/9009, Grafana 3000, OpenTTD
  admin 3977 and game 3979. Free them or edit `docker-compose.yml`.

## Notes & limitations

- Grafana runs with anonymous admin and no login — **demo only**, don't expose it.
- The admin port uses the simple (insecure) password login for convenience.
- Telemetry is sampled (~1s) and batched; it is not a per-tick exact trace.
- Per-company loan isn't streamed: the GS API only exposes loan for the script's
  own company, and the GS runs as a company-less deity (net worth is covered by
  `company_value`).
- Performance rating is the *previous* quarter's (the API rule), so it is null
  for the first in-game quarter.
