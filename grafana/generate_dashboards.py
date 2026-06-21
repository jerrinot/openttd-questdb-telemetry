#!/usr/bin/env python3
"""Generate the OpenTTD telemetry Grafana dashboards.

Hand-writing ~30 Grafana panels as JSON is error-prone, so we build them from
small, verified panel templates (Grafana 11.6 + QuestDB postgres datasource)
with an auto-layout grid. Run this to (re)create grafana/dashboards/*.json.

    python3 grafana/generate_dashboards.py
"""
import json
import os

DS = {"type": "grafana-postgresql-datasource", "uid": "questdb"}
OUT = os.path.join(os.path.dirname(__file__), "dashboards")


def tgt(sql, fmt="table"):
    return {"refId": "A", "datasource": DS, "rawQuery": True, "rawSql": sql, "format": fmt}


# ---------------------------------------------------------------- layout ----
class Layout:
    def __init__(self):
        self.x = self.y = self.rowh = 0
        self.id = 1
        self.panels = []

    def place(self, panel, w, h):
        if self.x + w > 24:
            self.x = 0
            self.y += self.rowh
            self.rowh = 0
        panel["gridPos"] = {"h": h, "w": w, "x": self.x, "y": self.y}
        panel["id"] = self.id
        self.id += 1
        self.x += w
        self.rowh = max(self.rowh, h)
        self.panels.append(panel)
        return panel

    def row(self, title):
        if self.x > 0:
            self.x = 0
            self.y += self.rowh
            self.rowh = 0
        self.panels.append({
            "type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}, "id": self.id, "panels": [],
        })
        self.id += 1
        self.y += 1


# ---------------------------------------------------------------- panels ----
def stat(title, sql, unit="short", color="blue", decimals=None,
         fmt="time_series", text_mode="value", graph_mode="area", fields=""):
    # `fields` selects which field the stat reduces. A lone STRING value (e.g.
    # the in-game date) needs an explicit selector ("/.*/") or it shows No data.
    defaults = {
        "unit": unit,
        "color": {"mode": "fixed", "fixedColor": color},
        "thresholds": {"mode": "absolute", "steps": [{"color": color, "value": None}]},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "stat", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": fields, "values": False},
            "colorMode": "value", "graphMode": graph_mode, "justifyMode": "auto",
            "textMode": text_mode, "orientation": "auto",
        },
        "targets": [tgt(sql, fmt)],
    }


def timeseries(title, sql, unit="short", interp="smooth", fill=10, stack=False,
               width=2, points="never", maxv=None):
    custom = {
        "drawStyle": "line", "lineInterpolation": interp, "lineWidth": width,
        "fillOpacity": fill, "showPoints": points, "spanNulls": False,
        "axisPlacement": "auto", "scaleDistribution": {"type": "linear"},
    }
    if stack:
        custom["stacking"] = {"mode": "normal", "group": "A"}
    defaults = {"unit": unit, "color": {"mode": "palette-classic"}, "custom": custom}
    if maxv is not None:
        defaults["max"] = maxv
    return {
        "type": "timeseries", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [tgt(sql, "time_series")],
    }


def barchart(title, sql, x_field, unit="short", horizontal=False):
    return {
        "type": "barchart", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {
            "unit": unit, "color": {"mode": "palette-classic"},
            "custom": {"lineWidth": 1, "fillOpacity": 80, "gradientMode": "none", "axisPlacement": "auto"},
        }, "overrides": []},
        "options": {
            "orientation": "horizontal" if horizontal else "auto",
            "xField": x_field, "stacking": "none", "showValue": "auto",
            "barWidth": 0.7, "groupWidth": 0.7,
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [tgt(sql, "table")],
    }


def bargauge(title, sql, unit="short", color="continuous-GrYlRd"):
    return {
        "type": "bargauge", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {
            "unit": unit, "color": {"mode": color},
            "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
        }, "overrides": []},
        "options": {
            "orientation": "horizontal", "displayMode": "gradient", "valueMode": "color",
            "showUnfilled": True, "minVizWidth": 0, "minVizHeight": 10,
            "namePlacement": "auto", "sizing": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
        },
        "targets": [tgt(sql, "table")],
    }


def piechart(title, sql, unit="short"):
    return {
        "type": "piechart", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {
            "unit": unit, "color": {"mode": "palette-classic"},
            "custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}},
        }, "overrides": []},
        "options": {
            "pieType": "donut", "displayLabels": ["percent"],
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
            "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                       "values": ["value", "percent"]},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [tgt(sql, "table")],
    }


def histogram(title, sql, unit="short", bucket=None):
    return {
        "type": "histogram", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {
            "unit": unit, "color": {"mode": "palette-classic"},
            "custom": {"fillOpacity": 80, "lineWidth": 1, "gradientMode": "none"},
        }, "overrides": []},
        "options": {"bucketSize": bucket, "bucketOffset": 0, "combine": False,
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}},
        "targets": [tgt(sql, "table")],
    }


def heatmap(title, sql, yunit="short"):
    return {
        "type": "heatmap", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {"custom": {
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "scaleDistribution": {"type": "linear"}}}, "overrides": []},
        "options": {
            "calculate": True,
            "calculation": {"xBuckets": {"mode": "size", "value": "5s"},
                            "yBuckets": {"mode": "count", "value": "20"}},
            "color": {"mode": "scheme", "scheme": "Spectral", "steps": 64,
                      "reverse": False, "exponent": 0.5, "fill": "dark-orange"},
            "cellGap": 1, "cellValues": {"unit": "short"},
            "yAxis": {"axisPlacement": "left", "unit": yunit, "reverse": False},
            "rowsFrame": {"layout": "auto"},
            "tooltip": {"mode": "single", "yHistogram": False, "showColorScale": True},
            "legend": {"show": True},
            "exemplars": {"color": "rgba(255,0,255,0.7)"},
        },
        "targets": [tgt(sql, "table")],
    }


def xychart(title, sql, color="green"):
    # Grafana 11.6 manual mapping uses plain field-name strings (NOT matchers).
    # Per-point colour-by-field is unreliable here, so we use one fixed colour
    # and keep extra SELECT columns for the hover tooltip. No panel-level unit
    # (it would mislabel the x/y tile-coordinate axes).
    return {
        "type": "xychart", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {
            "custom": {"show": "points", "pointSize": {"fixed": 6}, "pointShape": "circle",
                       "pointStrokeWidth": 1, "fillOpacity": 80, "axisPlacement": "auto",
                       "hideFrom": {"legend": False, "tooltip": False, "viz": False}},
            "color": {"mode": "fixed", "fixedColor": color},
        }, "overrides": []},
        "options": {
            "mapping": "manual",
            "series": [{"x": "x", "y": "y"}],
            "legend": {"displayMode": "list", "placement": "right", "showLegend": False},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [tgt(sql, "table")],
    }


def table(title, sql, color_cols=None):
    """color_cols: {column: (unit, [thresholds steps])}"""
    overrides = []
    for col, (unit, steps) in (color_cols or {}).items():
        overrides.append({
            "matcher": {"id": "byName", "options": col},
            "properties": [
                {"id": "unit", "value": unit},
                {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "gradient"}},
                {"id": "thresholds", "value": {"mode": "absolute", "steps": steps}},
            ],
        })
    return {
        "type": "table", "title": title, "datasource": DS,
        "fieldConfig": {"defaults": {
            "custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": False},
            "color": {"mode": "thresholds"},
        }, "overrides": overrides},
        "options": {"showHeader": True, "cellHeight": "sm",
                    "footer": {"show": False, "reducer": ["sum"], "fields": ""}},
        "targets": [tgt(sql, "table")],
    }


def dashboard(uid, title, layout):
    return {
        "annotations": {"list": []}, "editable": True, "graphTooltip": 1,
        "schemaVersion": 39, "tags": ["openttd", "questdb"],
        "time": {"from": "now-15m", "to": "now"}, "refresh": "5s",
        "title": title, "uid": uid, "templating": {"list": []}, "panels": layout.panels,
    }


# threshold palettes
PROFIT_STEPS = [{"color": "red", "value": None}, {"color": "yellow", "value": 0}, {"color": "green", "value": 1000}]
PERF_STEPS = [{"color": "red", "value": None}, {"color": "yellow", "value": 400}, {"color": "green", "value": 700}]
REL_STEPS = [{"color": "red", "value": None}, {"color": "yellow", "value": 50}, {"color": "green", "value": 80}]

KMH, PCT, USD = "velocitykmh", "percent", "currencyUSD"


def date_stat():
    return stat("In-game date", "SELECT game_date FROM vehicle_telemetry ORDER BY timestamp DESC LIMIT 1;",
                color="orange", fmt="table", graph_mode="none", fields="/.*/")


# ============================================================ OVERVIEW ====
def overview():
    L = Layout()
    L.row("At a glance")
    L.place(stat("Active vehicles",
                 "SELECT timestamp AS time, count_distinct(vid) AS active FROM vehicle_telemetry "
                 "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", color="blue"), 4, 4)
    L.place(stat("Avg speed",
                 "SELECT timestamp AS time, round(avg(speed),1) AS kmh FROM vehicle_telemetry "
                 "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", unit=KMH, color="green", decimals=1), 4, 4)
    L.place(stat("Fleet value",
                 "SELECT sum(value) AS v FROM (SELECT vid, value FROM vehicle_telemetry "
                 "LATEST ON timestamp PARTITION BY vid);", unit=USD, color="purple",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(stat("Companies",
                 "SELECT count_distinct(company) AS c FROM vehicle_telemetry "
                 "WHERE timestamp > dateadd('s',-60,now());", color="blue",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(stat("Cargo delivered (qtr)",
                 "SELECT sum(cargo_delivered) AS c FROM (SELECT company, cargo_delivered "
                 "FROM company_economy LATEST ON timestamp PARTITION BY company);", color="yellow",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(date_stat(), 4, 4)

    L.row("Fleet")
    L.place(timeseries("Fleet speed (avg & max)",
                       'SELECT timestamp AS time, round(avg(speed),1) AS "avg km/h", '
                       'round(max(speed),1) AS "max km/h" FROM vehicle_telemetry '
                       "WHERE $__timeFilter(timestamp) SAMPLE BY 1s;", unit=KMH), 12, 9)
    L.place(xychart("Live vehicle positions",
                    "SELECT x, y, company, vtype, round(speed,1) AS speed FROM vehicle_telemetry "
                    "WHERE timestamp > dateadd('s',-60,now()) LATEST ON timestamp PARTITION BY vid;",
                    color="green"), 12, 9)
    L.place(piechart("Vehicle mix by type",
                     "SELECT vtype, count_distinct(vid) AS vehicles FROM vehicle_telemetry "
                     "WHERE timestamp > dateadd('s',-60,now()) GROUP BY vtype ORDER BY vehicles DESC;"), 8, 9)
    L.place(timeseries("Company value over time",
                       "SELECT timestamp AS time, company, last(company_value) AS value "
                       "FROM company_economy WHERE $__timeFilter(timestamp) SAMPLE BY 5s;", unit=USD), 16, 9)
    return dashboard("openttd-overview", "OpenTTD · Overview", L)


# ===================================================== FLEET OPERATIONS ====
def fleet():
    L = Layout()
    L.row("Fleet KPIs")
    L.place(stat("Active vehicles",
                 "SELECT timestamp AS time, count_distinct(vid) AS active FROM vehicle_telemetry "
                 "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", color="blue"), 4, 4)
    L.place(stat("Avg load factor",
                 "SELECT timestamp AS time, round(avg(load_pct),1) AS l FROM vehicle_telemetry "
                 "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", unit=PCT, color="green", decimals=1), 4, 4)
    L.place(stat("Avg reliability",
                 "SELECT timestamp AS time, round(avg(reliability),1) AS r FROM vehicle_telemetry "
                 "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", unit=PCT, color="yellow", decimals=1), 4, 4)
    L.place(stat("Avg speed vs max",
                 "SELECT timestamp AS time, round(avg(speed_pct),1) AS s FROM vehicle_telemetry "
                 "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", unit=PCT, color="blue", decimals=1), 4, 4)
    L.place(stat("Stopped / broken",
                 "SELECT count() AS n FROM (SELECT vid, state FROM vehicle_telemetry "
                 "WHERE timestamp > dateadd('s',-60,now()) LATEST ON timestamp PARTITION BY vid) "
                 "WHERE state IN ('stopped','broken','crashed');", color="red",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(date_stat(), 4, 4)

    L.row("Movement & utilisation")
    L.place(xychart("Live positions",
                    "SELECT x, y, company, vtype, round(load_pct,0) AS load_pct, state FROM vehicle_telemetry "
                    "WHERE timestamp > dateadd('s',-60,now()) LATEST ON timestamp PARTITION BY vid;",
                    color="yellow"), 12, 9)
    L.place(timeseries("Load factor by type",
                       "SELECT timestamp AS time, vtype, round(avg(load_pct),1) AS load "
                       "FROM vehicle_telemetry WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", unit=PCT), 12, 9)
    L.place(histogram("Reliability distribution (current fleet)",
                      "SELECT reliability FROM vehicle_telemetry WHERE timestamp > dateadd('s',-60,now()) "
                      "LATEST ON timestamp PARTITION BY vid;", unit=PCT), 8, 8)
    L.place(heatmap("Speed distribution over time",
                    "SELECT timestamp AS time, speed FROM vehicle_telemetry WHERE $__timeFilter(timestamp);",
                    yunit=KMH), 16, 8)

    L.row("Status")
    L.place(piechart("Vehicle state mix",
                     "SELECT state, count() AS n FROM (SELECT vid, state FROM vehicle_telemetry "
                     "WHERE timestamp > dateadd('s',-60,now()) LATEST ON timestamp PARTITION BY vid) "
                     "GROUP BY state ORDER BY n DESC;"), 8, 9)
    L.place(timeseries("Active vehicles by type",
                       "SELECT timestamp AS time, vtype, count_distinct(vid) AS vehicles "
                       "FROM vehicle_telemetry WHERE $__timeFilter(timestamp) SAMPLE BY 2s;",
                       stack=True, interp="stepAfter", fill=25), 16, 9)
    L.place(table("Worst performers (lowest profit)",
                  "SELECT vid, company, vtype, state, round(speed,0) AS speed, reliability, "
                  "round(load_pct,0) AS load_pct, profit FROM vehicle_telemetry "
                  "WHERE timestamp > dateadd('s',-60,now()) LATEST ON timestamp PARTITION BY vid "
                  "ORDER BY profit ASC LIMIT 25;",
                  color_cols={"profit": (USD, PROFIT_STEPS), "reliability": (PCT, REL_STEPS)}), 24, 10)
    return dashboard("openttd-fleet", "OpenTTD · Fleet Operations", L)


# ================================================ ECONOMY & PROFITABILITY ==
def economy():
    L = Layout()
    L.row("Economy KPIs")
    L.place(stat("Total company value",
                 "SELECT sum(company_value) AS v FROM (SELECT company, company_value FROM company_economy "
                 "LATEST ON timestamp PARTITION BY company);", unit=USD, color="purple",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(stat("Total cash",
                 "SELECT sum(money) AS m FROM (SELECT company, money FROM company_economy "
                 "LATEST ON timestamp PARTITION BY company);", unit=USD, color="green",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(stat("Quarterly income",
                 "SELECT sum(income) AS i FROM (SELECT company, income FROM company_economy "
                 "LATEST ON timestamp PARTITION BY company);", unit=USD, color="blue",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(stat("Quarterly expenses",
                 "SELECT sum(expenses) AS e FROM (SELECT company, expenses FROM company_economy "
                 "LATEST ON timestamp PARTITION BY company);", unit=USD, color="red",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(stat("Cargo delivered (qtr)",
                 "SELECT sum(cargo_delivered) AS c FROM (SELECT company, cargo_delivered FROM company_economy "
                 "LATEST ON timestamp PARTITION BY company);", color="yellow",
                 fmt="table", graph_mode="none"), 4, 4)
    L.place(date_stat(), 4, 4)

    L.row("Wealth over time")
    L.place(timeseries("Company value",
                       "SELECT timestamp AS time, company, last(company_value) AS value FROM company_economy "
                       "WHERE $__timeFilter(timestamp) SAMPLE BY 5s;", unit=USD), 12, 9)
    L.place(timeseries("Bank balance (cash)",
                       "SELECT timestamp AS time, company, last(money) AS cash FROM company_economy "
                       "WHERE $__timeFilter(timestamp) SAMPLE BY 2s;", unit=USD), 12, 9)

    L.row("Profitability")
    L.place(barchart("Income vs expenses (latest quarter)",
                     "SELECT company, income, expenses FROM company_economy "
                     "LATEST ON timestamp PARTITION BY company ORDER BY company;",
                     x_field="company", unit=USD), 12, 9)
    L.place(timeseries("Performance rating (0-1000)",
                       "SELECT timestamp AS time, company, last(performance) AS perf FROM company_economy "
                       "WHERE $__timeFilter(timestamp) AND performance >= 0 SAMPLE BY 10s;",
                       unit="short", maxv=1000), 12, 9)
    L.place(bargauge("Quarterly profit leaderboard",
                     "SELECT company, profit_q FROM company_economy "
                     "LATEST ON timestamp PARTITION BY company ORDER BY profit_q DESC;", unit=USD), 12, 9)
    L.place(bargauge("Cargo delivered leaderboard",
                     "SELECT company, cargo_delivered FROM company_economy "
                     "LATEST ON timestamp PARTITION BY company ORDER BY cargo_delivered DESC;",
                     color="continuous-BlPu"), 12, 9)
    L.place(table("Company scoreboard",
                  "SELECT company, money, company_value, income, expenses, profit_q, performance, "
                  "cargo_delivered FROM company_economy LATEST ON timestamp PARTITION BY company "
                  "ORDER BY company_value DESC;",
                  color_cols={"profit_q": (USD, PROFIT_STEPS), "performance": ("short", PERF_STEPS)}), 24, 9)
    return dashboard("openttd-economy", "OpenTTD · Economy & Profitability", L)


def main():
    os.makedirs(OUT, exist_ok=True)
    # Remove the old single dashboard if present.
    old = os.path.join(OUT, "openttd_telemetry.json")
    if os.path.exists(old):
        os.remove(old)
    boards = {
        "openttd_overview.json": overview(),
        "openttd_fleet.json": fleet(),
        "openttd_economy.json": economy(),
    }
    for name, board in boards.items():
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(board, f, indent=2)
        print(f"wrote {name}: {len([p for p in board['panels'] if p['type'] != 'row'])} panels")


if __name__ == "__main__":
    main()
