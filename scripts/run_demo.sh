#!/usr/bin/env bash
# One-command demo:
#   1. brings up QuestDB + Grafana + the bridge (docker compose)
#   2. installs the telemetry GameScript into OpenTTD's data dir
#   3. launches an OpenTTD dedicated server (with AI competitors) in the foreground
#
# The bridge container auto-connects to the server's admin port as soon as it is
# up, and starts streaming vehicle telemetry into QuestDB.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- locate the OpenTTD binary ------------------------------------------------
OPENTTD_BIN="${OPENTTD:-}"
if [ -z "$OPENTTD_BIN" ]; then
  for c in \
    "$HOME/Downloads/openttd-15.2-linux-generic-amd64/openttd" \
    "$(command -v openttd 2>/dev/null || true)"; do
    if [ -n "$c" ] && [ -x "$c" ]; then OPENTTD_BIN="$c"; break; fi
  done
fi
if [ -z "$OPENTTD_BIN" ] || [ ! -x "$OPENTTD_BIN" ]; then
  echo "ERROR: OpenTTD binary not found. Set OPENTTD=/path/to/openttd and retry." >&2
  exit 1
fi
echo ">> OpenTTD: $OPENTTD_BIN"

DATA_DIR="${OPENTTD_DATA_DIR:-$HOME/.local/share/openttd}"

# --- check that at least one AI is installed (else OpenTTD aborts) -------------
if [ "${SKIP_AI_CHECK:-0}" != "1" ]; then
  ai_count=$(find "$DATA_DIR/ai" -mindepth 1 -maxdepth 1 ! -name 'library' ! -name '.*' 2>/dev/null | wc -l || echo 0)
  if [ "$ai_count" -eq 0 ]; then
    cat >&2 <<EOF
ERROR: no AI found in $DATA_DIR/ai

The demo relies on AI companies to build networks and run vehicles. Download one
(one-time, ~10s) via the in-game content browser:

  1. Launch OpenTTD normally:   $OPENTTD_BIN
  2. Main menu -> "Check Online Content"
  3. Filter/scroll to the AIs, tick e.g. "AdmiralAI" (a good all-rounder),
     then click "Download".
  4. Quit OpenTTD and re-run this script.

(To bypass this check anyway, run with SKIP_AI_CHECK=1.)
EOF
    exit 1
  fi
  echo ">> AIs installed: $ai_count"
fi

# --- bring up the data plane --------------------------------------------------
echo ">> Starting QuestDB + Grafana + bridge (docker compose) ..."
docker compose up -d --build

# --- install the GameScript ---------------------------------------------------
echo ">> Installing GameScript ..."
"$REPO_ROOT/scripts/install_gamescript.sh"

cat <<EOF

------------------------------------------------------------------------
  QuestDB console : http://localhost:9000     (SQL: SELECT * FROM vehicle_telemetry LIMIT 10)
  Grafana         : http://localhost:3000/dashboards   (anonymous, no login)
      • Overview            /d/openttd-overview
      • Fleet Operations    /d/openttd-fleet
      • Economy & Profit    /d/openttd-economy
  Admin port      : 127.0.0.1:3977  (password: questdb)
------------------------------------------------------------------------

Starting the OpenTTD dedicated server now (Ctrl-C to stop the server).
Give the AIs a couple of minutes to build their first routes; telemetry
will start flowing into QuestDB as soon as vehicles move.

The data plane (QuestDB/Grafana/bridge) keeps running after you stop the
server. Run scripts/stop_demo.sh to tear it down.
------------------------------------------------------------------------

EOF

# --- run the server (foreground) ----------------------------------------------
exec "$OPENTTD_BIN" -D -c "$REPO_ROOT/config/demo.cfg" -x
