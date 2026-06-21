#!/usr/bin/env python3
"""Generate a QuestDB Notebook (advanced dashboard) as a Dexie-export JSON that
can be loaded via the web console's "Import tabs" button.

The console (feat/notebooks) stores notebooks in browser IndexedDB (Dexie). Its
importer (dexie-export-import) accepts a Dexie export of the `buffers` table; each
buffer with a `notebookViewState` becomes a notebook tab. Chart cells use
mode="draw" + a chartConfig {xColumn, queries:[{type,yColumns,partitionByColumn}]},
and auto-refresh (2-60s adaptive polling) gives live dashboards.

    python3 questdb/notebook/generate_notebook.py
    # then in the console: ⋮ menu -> Import tabs -> pick the generated .json

Verified against ui feat/notebooks: importTabs.ts (validate/sanitize), chartTypes.ts.
"""
import json
import os

OUT = os.path.join(os.path.dirname(__file__), "openttd-telemetry-notebook.json")

# 15-minute rolling window for time-series; 60s "latest" window for snapshots.
WIN = "timestamp > dateadd('m', -15, now())"
SNAP = "timestamp > dateadd('s', -60, now())"


def chart(cid, title, sql, xcol, ctype, ycols, partition=None, area_fill=False):
    q = {"type": ctype, "yColumns": ycols, "enabled": True, "name": title}
    if partition:
        q["partitionByColumn"] = partition
    return {
        "id": cid,
        "value": sql,
        "type": "sql",
        "mode": "draw",
        "autoRefresh": True,
        "topHeight": 64,
        "bottomHeight": 320,
        "chartConfig": {
            "name": title,
            "xColumn": xcol,
            "autoRefresh": True,
            "queries": [q],
        },
    }


def markdown(cid, text):
    return {"id": cid, "type": "markdown", "value": text, "topHeight": 60}


# ---- cells (mapped from the Grafana dashboards) ----------------------------
cells = [
    markdown("ottd-title",
             "# OpenTTD · Live Telemetry\n"
             "Live vehicle & company telemetry from a running OpenTTD game. "
             "Charts auto-refresh every few seconds."),

    chart("ottd-speed", "Fleet speed (avg & max km/h)",
          f"SELECT timestamp AS time, round(avg(speed),1) AS avg_kmh, "
          f"round(max(speed),1) AS max_kmh FROM vehicle_telemetry "
          f"WHERE {WIN} SAMPLE BY 1s",
          "time", "line", ["avg_kmh", "max_kmh"]),

    chart("ottd-map", "Live vehicle positions",
          f"SELECT x, y FROM vehicle_telemetry WHERE {SNAP} "
          f"LATEST ON timestamp PARTITION BY vid",
          "x", "scatter", ["y"]),

    chart("ottd-bytype", "Active vehicles by type",
          f"SELECT timestamp AS time, vtype, count_distinct(vid) AS vehicles "
          f"FROM vehicle_telemetry WHERE {WIN} SAMPLE BY 2s",
          "time", "line", ["vehicles"], partition="vtype"),

    chart("ottd-load", "Avg load factor (%)",
          f"SELECT timestamp AS time, round(avg(load_pct),1) AS load_pct "
          f"FROM vehicle_telemetry WHERE {WIN} SAMPLE BY 2s",
          "time", "area", ["load_pct"]),

    chart("ottd-value", "Company value over time",
          f"SELECT timestamp AS time, company, last(company_value) AS value "
          f"FROM company_economy WHERE {WIN} SAMPLE BY 5s",
          "time", "line", ["value"], partition="company"),

    chart("ottd-cash", "Bank balance over time",
          f"SELECT timestamp AS time, company, last(money) AS cash "
          f"FROM company_economy WHERE {WIN} SAMPLE BY 2s",
          "time", "line", ["cash"], partition="company"),

    chart("ottd-profit", "Quarterly profit by company",
          "SELECT company, profit_q FROM company_economy "
          "LATEST ON timestamp PARTITION BY company ORDER BY profit_q DESC",
          "company", "bar", ["profit_q"]),

    chart("ottd-mix", "Vehicle mix by type",
          f"SELECT vtype, count_distinct(vid) AS vehicles FROM vehicle_telemetry "
          f"WHERE {SNAP} GROUP BY vtype ORDER BY vehicles DESC",
          "vtype", "pie", ["vehicles"]),
]

# ---- grid layout: title full-width, charts two-per-row ----------------------
GRID_COLS, CHART_W, CHART_H = 12, 6, 32
layout = [{"i": "ottd-title", "x": 0, "y": 0, "w": 12, "h": 4}]
chart_ids = [c["id"] for c in cells if c["id"] != "ottd-title"]
y = 4
for idx, cid in enumerate(chart_ids):
    x = 0 if idx % 2 == 0 else CHART_W
    layout.append({"i": cid, "x": x, "y": y, "w": CHART_W, "h": CHART_H})
    if idx % 2 == 1:
        y += CHART_H

for i, c in enumerate(cells):
    c["position"] = i

notebook_view_state = {
    "cells": cells,
    "settings": {"layoutMode": "grid", "layout": layout, "variables": []},
}

buffer_row = {
    "id": 990001,  # dropped on import (sanitizeBuffer assigns a fresh ++id)
    "label": "OpenTTD Telemetry",
    "value": "",
    "position": 100,
    "notebookViewState": notebook_view_state,
}

dexie_export = {
    "formatName": "dexie",
    "formatVersion": 1,
    "data": {
        "databaseName": "web-console",
        "databaseVersion": 9,
        "tables": [
            {"name": "buffers", "schema": "++id,label,position,archived,archivedAt", "rowCount": 1},
        ],
        "data": [
            {"tableName": "buffers", "inbound": True, "rows": [buffer_row]},
        ],
    },
}

with open(OUT, "w") as f:
    json.dump(dexie_export, f, indent=2)
print(f"wrote {OUT}: 1 notebook, {len(cells)} cells ({len(chart_ids)} charts)")
