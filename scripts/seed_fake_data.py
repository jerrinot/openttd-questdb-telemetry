#!/usr/bin/env python3
"""Seed QuestDB with realistic synthetic telemetry — test the dashboards WITHOUT
running OpenTTD.

Writes ~15 minutes of history (48 vehicles + 6 companies) into the same two
tables the live bridge uses, so all three Grafana dashboards light up.

    python3 scripts/seed_fake_data.py            # -> http://localhost:9000
    QUESTDB_HTTP=http://host:9000 python3 scripts/seed_fake_data.py

Re-run it occasionally: the "current snapshot" panels (map, histogram, tables)
use a 60-second window, so they need data newer than ~1 minute.
"""
import math, os, random, time, urllib.request

QUESTDB_HTTP = os.environ.get("QUESTDB_HTTP", "http://localhost:9000").rstrip("/")
random.seed(7)
BASE = time.time_ns()
WINDOW_S, STEP_S, NV, NC = 900, 5, 48, 6
VTYPES = ["rail", "road", "water", "air"]
STATES = ["running", "running", "running", "running", "stopped", "depot", "station", "broken"]


def esc(s):
    return s.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


veh = [{
    "vid": i + 1, "company": i % NC, "vtype": VTYPES[i % len(VTYPES)],
    "max_speed": random.choice([96, 112, 128, 160, 200, 320]),
    "max_age": random.choice([3650, 5475, 7300]),
    "cap": random.choice([20, 40, 80, 120, 200]),
    "x0": random.randint(10, 250), "y0": random.randint(10, 250),
    "born": random.randint(0, 3000),
} for i in range(NV)]

lines = []


def flush():
    global lines
    if not lines:
        return
    req = urllib.request.Request(QUESTDB_HTTP + "/write?precision=n",
                                 data="\n".join(lines).encode(), method="POST")
    with urllib.request.urlopen(req) as r:
        assert r.status in (200, 204), r.status
    lines = []


steps = WINDOW_S // STEP_S
for s in range(steps):
    ts = BASE - (WINDOW_S - s * STEP_S) * 1_000_000_000
    year = 1960 + s // 24
    gdate = f"{year:04d}-{(1 + (s // 2) % 12):02d}-{(1 + s % 28):02d}"
    phase = s / steps
    for v in veh:
        state = "running" if random.random() > 0.15 else random.choice(STATES)
        speed = 0.0 if state != "running" else max(0, v["max_speed"] * (0.5 + 0.5 * math.sin(phase * 6 + v["vid"])))
        spct = speed / v["max_speed"] * 100 if v["max_speed"] else 0
        load = int(v["cap"] * random.uniform(0.2, 1.0)) if state in ("running", "station") else int(v["cap"] * random.uniform(0, 0.3))
        lpct = load / v["cap"] * 100 if v["cap"] else 0
        age = v["born"] + int(phase * 2000)
        apct = min(100, age / v["max_age"] * 100)
        rel = max(20, 98 - int(apct * 0.5) - (30 if state == "broken" else 0))
        profit = int(2000 * v["vid"] * (phase + 0.2) + random.randint(-3000, 4000))
        x = (v["x0"] + int(20 * math.sin(phase * 4 + v["vid"]))) % 256
        y = (v["y0"] + int(20 * math.cos(phase * 4 + v["vid"]))) % 256
        lines.append(
            f"vehicle_telemetry,company={esc('Company '+str(v['company']+1))},vtype={v['vtype']},state={state} "
            f"vid={v['vid']}i,x={x}i,y={y}i,speed={speed:.1f},max_speed={float(v['max_speed']):.1f},"
            f"speed_pct={spct:.1f},reliability={rel}i,age={age}i,max_age={v['max_age']}i,age_pct={apct:.1f},"
            f"profit={profit}i,profit_last={int(profit*0.8)}i,value={50000+v['vid']*1000}i,"
            f"cargo_cap={v['cap']}i,cargo_load={load}i,load_pct={lpct:.1f},running_cost={500+v['vid']*40}i,"
            f"game_year={year}i,game_date=\"{gdate}\" {ts}"
        )
    for c in range(NC):
        income, expenses = 50000 + c * 12000 + int(phase * 100000), 30000 + c * 8000 + int(phase * 60000)
        lines.append(
            f"company_economy,company={esc('Company '+str(c+1))} "
            f"money={100000+c*50000+int(phase*800000)+random.randint(-20000,20000)}i,"
            f"company_value={200000+c*80000+int(phase*1500000)}i,income={income}i,expenses={expenses}i,"
            f"profit_q={income-expenses}i,performance={min(1000,200+c*80+int(phase*500))}i,"
            f"cargo_delivered={int(phase*(5000+c*1200))}i,game_year={year}i,game_date=\"{gdate}\" {ts}"
        )
    if len(lines) > 2000:
        flush()
flush()
print(f"seeded {steps} steps x ({NV} vehicles + {NC} companies) -> {QUESTDB_HTTP}")
