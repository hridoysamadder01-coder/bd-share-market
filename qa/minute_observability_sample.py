"""Descriptive observability facts on a random 40-symbol sample of the trade-minute
dataset — the numbers quoted in DATA_ACQUISITION_ARCHITECTURE.md §7.

This is documentation of evidence, not research: it computes *what the data can
show* (how many minutes carry more than one price, when prints happen), never a
footprint, precursor, signal or predictive statistic. Rows outside 10:00–14:30
(inclusive) are ignored for these counts; snapshot rows are not removed (the QA
report flags them; this script only describes observability).

    python3 qa/minute_observability_sample.py --input /path/to/dse_minute_data [--seed 7] [--n 40]

Writes qa/MINUTE_OBSERVABILITY_SAMPLE.json (seed, symbols, pooled and per-year figures).
"""
import argparse, glob, json, os, random
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--input", required=True)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--n", type=int, default=40)
ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "MINUTE_OBSERVABILITY_SAMPLE.json"))
a = ap.parse_args()

folder = os.path.join(a.input, "minute_price_unadjusted")
files = sorted(f for f in glob.glob(os.path.join(folder, "*.csv"))
               if os.path.basename(f) not in ("summary.csv", "__summary__.csv") and os.path.getsize(f) > 64)
random.seed(a.seed)
sample = random.sample(files, a.n)

pooled = dict(rows=0, multi_price=0, open_ne_close=0, volume_1=0, prints_1000_1005=0, prints_1420_1430=0,
              symbol_days=0, days_last_print_1420_1430=0, days_first_print_by_1005=0)
by_year = {}
for f in sample:
    d = pd.read_csv(f)
    d["ts"] = pd.to_datetime(d["timestamp"], errors="coerce")
    d = d.dropna(subset=["ts"])
    m = d.ts.dt.hour * 60 + d.ts.dt.minute
    d = d[(m >= 600) & (m <= 870)]                      # 10:00–14:30 inclusive
    m = d.ts.dt.hour * 60 + d.ts.dt.minute
    d = d.assign(m=m, date=d.ts.dt.date, y=d.ts.dt.year)
    pooled["rows"] += len(d)
    pooled["multi_price"] += int((d.high > d.low).sum())
    pooled["open_ne_close"] += int((d.opening != d.closing).sum())
    pooled["volume_1"] += int((d.volume == 1).sum())
    pooled["prints_1000_1005"] += int((d.m <= 605).sum())
    pooled["prints_1420_1430"] += int((d.m >= 860).sum())
    g = d.groupby("date").m.agg(["min", "max"])
    pooled["symbol_days"] += len(g)
    pooled["days_last_print_1420_1430"] += int((g["max"] >= 860).sum())
    pooled["days_first_print_by_1005"] += int((g["min"] <= 605).sum())
    for y, gg in d.groupby("y"):
        b = by_year.setdefault(int(y), dict(rows=0, multi_price=0, symbol_days=0, days_last_print_1420_1430=0, days_first_print_by_1005=0))
        b["rows"] += len(gg); b["multi_price"] += int((gg.high > gg.low).sum())
        gy = gg.groupby("date").m.agg(["min", "max"])
        b["symbol_days"] += len(gy)
        b["days_last_print_1420_1430"] += int((gy["max"] >= 860).sum())
        b["days_first_print_by_1005"] += int((gy["min"] <= 605).sum())

r = pooled["rows"]; sd = pooled["symbol_days"]
out = {
    "seed": a.seed, "n_symbols": a.n, "window": "10:00-14:30 inclusive",
    "symbols": [os.path.basename(f)[:-4] for f in sample],
    "pooled": dict(pooled,
                   share_multi_price=pooled["multi_price"] / r, share_open_ne_close=pooled["open_ne_close"] / r,
                   share_volume_1=pooled["volume_1"] / r, share_prints_1000_1005=pooled["prints_1000_1005"] / r,
                   share_prints_1420_1430=pooled["prints_1420_1430"] / r,
                   share_days_last_print_1420_1430=pooled["days_last_print_1420_1430"] / sd,
                   share_days_first_print_by_1005=pooled["days_first_print_by_1005"] / sd),
    "by_year": {y: dict(b, share_multi_price=b["multi_price"] / b["rows"],
                        share_days_last_print_1420_1430=b["days_last_print_1420_1430"] / b["symbol_days"],
                        share_days_first_print_by_1005=b["days_first_print_by_1005"] / b["symbol_days"])
                for y, b in sorted(by_year.items())},
}
pre = {k: sum(b[k] for y, b in by_year.items() if y < 2020) for k in ("rows", "multi_price")}
post = {k: sum(b[k] for y, b in by_year.items() if y >= 2020) for k in ("rows", "multi_price")}
out["pre_2020_share_multi_price"] = pre["multi_price"] / pre["rows"]
out["from_2020_share_multi_price"] = post["multi_price"] / post["rows"]
json.dump(out, open(a.out, "w"), indent=1, allow_nan=False)
print(json.dumps({k: v for k, v in out.items() if k not in ("symbols", "by_year")}, indent=1))
print("by_year share_multi_price:", {y: round(b["share_multi_price"], 4) for y, b in out["by_year"].items()})
print("by_year share_days_last_print_1420_1430:", {y: round(b["share_days_last_print_1420_1430"], 3) for y, b in out["by_year"].items()})
print("wrote", a.out)
