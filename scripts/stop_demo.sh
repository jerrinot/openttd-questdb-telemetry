#!/usr/bin/env bash
# Tear down the data plane (QuestDB + Grafana + bridge).
# Stop the OpenTTD server itself with Ctrl-C in its terminal.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEEP_DATA="${KEEP_DATA:-0}"
if [ "$KEEP_DATA" = "1" ]; then
  docker compose down
  echo "Stopped containers; QuestDB data volume kept."
else
  docker compose down -v
  echo "Stopped containers and removed the QuestDB data volume."
fi
