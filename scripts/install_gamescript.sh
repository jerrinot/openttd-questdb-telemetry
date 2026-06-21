#!/usr/bin/env bash
# Symlink (or copy) the GameScript into OpenTTD's data directory so the server
# can find it. Symlinking means edits to the repo are picked up on next game.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/gamescript/questdb_telemetry"

# OpenTTD's personal data dir (where downloaded/own content lives).
DATA_DIR="${OPENTTD_DATA_DIR:-$HOME/.local/share/openttd}"
DEST_DIR="$DATA_DIR/game"
DEST="$DEST_DIR/questdb_telemetry"

mkdir -p "$DEST_DIR"

if [ "${1:-}" = "--copy" ]; then
  rm -rf "$DEST"
  cp -r "$SRC" "$DEST"
  echo "Copied GameScript to $DEST"
else
  ln -sfn "$SRC" "$DEST"
  echo "Linked GameScript: $DEST -> $SRC"
fi
