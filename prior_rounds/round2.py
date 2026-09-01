#!/usr/bin/env python3
"""
ROUND 2 — separate analysis layer. Main simulator NOT touched.

Part 1: A-family P3 failure — regime failure vs partial-coverage artifact
        via matched-universe test.
Part 2: Panic-day rebound — strict EVENT STUDY (not a presumed strategy).

Provenance discipline:
  RECONSTRUCTED — A-family definition & period splits rebuilt from Round 1
                  config knobs found in session record (vol>3x ADV20,
                  close>20d high, TP 8% / SL 4% / max 15d). If Hridoy's
                  Round 1 A-family differs, rerun with exact defs.
  Periods: P1 2013-01-01..2017-12-31 | P2 2018-01-01..2022-07-27
           FLOOR 2022-07-28..2024-01-31 (excluded/separated)
           P3 2024-02-01..end
  Costs: round-trip tested at 0.8% / 1.0% / 1.2% (covers commission+laga
         uncertainty band). CGT still UNKNOWN=0 -> nets may overstate.
  Data: unadjusted -> per-stock 1-day drop >15% treated as suspected
        ex-date artifact, stock excluded from that event (DSE normal
        circuit ~10%, so >15% one-day is data hazard, not market).
"""
import csv, glob, os, math
from datetime import date
from collections import defaultdict

FLOOR_A, FLOOR_B = date(2022,7,28), date(2024,1,31)
P1 = (date(2013,1,1),  date(2017,12,31))
P2 = (date(2018,1,1),  date(2022,7,27))
P3 = (date(2024,2,1),  date(2030,1,1))
PERIODS = [("P1",P1),("P2",P2),("P3",P3)]
NONEQ = ("BOND","SUKUK","MF","TB1","TB2","TB5","TB10","TB15","TB20",
         "00DS","ETF","PBOND","GBF","INCOMEF","GROWTHF")
MIN_PRICE = 5.0
COSTS = (0.008, 0.010, 0.012)

def looks_equity(s): return not any(h in s for h in NONEQ)

def load():
    out={}
    for p in glob.glob("/home/claude/merged_eod/*.csv"):
        sym=os.path.basename(p)[:-4]
        if sym.startswith("_") or not looks_equity(sym): continue
        rows=[]
        with open(p) as f:
            r=csv.reader(f); next(r)
            for row in r:
                d=date.fromisoformat(row[0])
                rows.append((d,float(row[1]),float(row[2]),float(row[3]),
                             float(row[4]),int(row[5])))
        if rows: out[sym]=rows
    return out

def adv20(rows,i):
    lo=max(0,i-20); v=[r[5] for r in rows[lo:i]]
    return sum(v)/len(v) if v else 0.0

def in_floor(d): return FLOOR_A<=d<=FLOOR_B

def pinned(rows,i):
    lo=max(0,i-7); one=sum(1 for r in rows[lo:i] if r[2]==r[3])
    return one>=5

def tstat(xs):
    n=len(xs)
    if n<3: return float('nan')
    m=sum(xs)/n
    var=sum((x-m)**2 for x in xs)/(n-1)
    return m/math.sqrt(var/n) if var>0 else float('nan')

# ======================================================================
# PART 1 — A-family, trade-level (no capital dynamics: question is edge
# existence per period x universe, not portfolio path)
# ======================================================================
A_FAMILY = {  # RECONSTRUCTED
    "A1": dict(spike=3.0, hi=20, tp=0.08, sl=0.04, hold=15),
    "A2": dict(spike=2.5, hi=15, tp=0.08, sl=0.04, hold=15),
    "A3": dict(spike=4.0, hi=30, tp=0.08, sl=0.04, hold=15),
}

def a_trades(sym, rows, k):
    """Signal at t close; enter open t+1; exit open of day after trigger
    (TP/SL on close) or open after max_hold. Skips: floor era, pinned,
    one-price entry day, price<min. Suspect (any |gap|>15% in life) excluded."""
    out=[]
    n=len(rows); i=k["hi"]+21
    while i < n-2:
        d,o,h,l,c,v = rows[i]
        if in_floor(d) or pinned(rows,i) or c<MIN_PRICE: i+=1; continue
        a=adv20(rows,i)
        hi_prev=max(r[2] for r in rows[i-k["hi"]:i])
        if a>0 and v>k["spike"]*a and c>hi_prev:
            e=i+1
            if rows[e][2]==rows[e][3]: i+=1; continue   # locked entry day
            entry=rows[e][1]
            if entry<MIN_PRICE: i+=1; continue
            exit_px=None; suspect=False; j=e
            while j < min(e+k["hold"], n-1):
                pc=rows[j-1][4]; po=rows[j][1]
                if pc>0 and abs(po/pc-1)>0.15: suspect=True
                cj=rows[j][4]
                if cj>=entry*(1+k["tp"]) or cj<=entry*(1-k["sl"]):
                    if j+1<n:
                        exit_px=rows[j+1][1]; j+=1
                    break
                j+=1
            if exit_px is None and j<n: exit_px=rows[j][1]
            if exit_px and exit_px>0 and not suspect:
                out.append((rows[e][0], exit_px/entry-1.0))
            i=e+k["hold"]   # no overlapping re-entry same stock
        else:
            i+=1
    return out

def cell(trades, cost):
    if not trades: return None
    rs=[r for _,r in trades]
    nets=[r-cost for r in rs]
    wins=sum(1 for r in nets if r>0)
    return dict(n=len(rs), raw=sum(rs)/len(rs), net=sum(nets)/len(nets),
                win=wins/len(nets), t=tstat(nets))

def part1(data):
    # per-period coverage per symbol
    cov=defaultdict(dict)
    for sym,rows in data.items():
        for nm,(a,b) in PERIODS:
            cov[sym][nm]=sum(1 for r in rows if a<=r[0]<=b)
    matched=sorted(s for s in data if all(cov[s][nm]>=150 for nm,_ in PERIODS))
    print("="*74)
    print("PART 1 — A-FAMILY: REGIME FAILURE vs COVERAGE ARTIFACT")
    print("="*74)
    full_per={nm:sorted(s for s in data if cov[s][nm]>=150) for nm,_ in PERIODS}
    print(f"Universe sizes: FULL P1={len(full_per['P1'])} P2={len(full_per['P2'])} "
          f"P3={len(full_per['P3'])} | MATCHED (>=150d in all three)={len(matched)}")
    print("Cost basis for nets in this table: 1.0% round trip\n")
    verdicts={}
    for fam,k in A_FAMILY.items():
        tr_by_sym={s:a_trades(s,data[s],k) for s in data}
        print(f"[{fam}] spike>{k['spike']}xADV20, close>{k['hi']}d-high, "
              f"TP{k['tp']:.0%}/SL{k['sl']:.0%}/{k['hold']}d   (RECONSTRUCTED)")
        print(f"  {'':10s}{'n':>6s}{'raw/tr':>9s}{'net@1%':>9s}{'win':>6s}{'t':>7s}")
        res={}
        for nm,(a,b) in PERIODS:
            for tag,uni in (("FULL",full_per[nm]),("MATCH",matched)):
                tr=[(d,r) for s in uni for d,r in tr_by_sym[s] if a<=d<=b]
                c=cell(tr,0.010)
                res[(nm,tag)]=c
                if c: print(f"  {nm}-{tag:6s}{c['n']:6d}{c['raw']:9.2%}"
                            f"{c['net']:9.2%}{c['win']:6.0%}{c['t']:7.2f}")
                else: print(f"  {nm}-{tag:6s}{'0':>6s}")
        # pre-registered decision rule
        def net(nm,tag):
            c=res.get((nm,tag)); return c['net'] if c else float('nan')
        early_full=(net('P1','FULL')+net('P2','FULL'))/2
        early_match=(net('P1','MATCH')+net('P2','MATCH'))/2
        p3=net('P3','FULL')
        if not (early_full==early_full):
            v="INSUFFICIENT DATA"
        elif early_full<=0 and p3<=0:
            v="NEVER WORKED — P3 'failure' is not special; family negative in early periods too"
        elif early_match<=0 < early_full:
            v="COVERAGE ARTIFACT — early edge lived in symbols absent from P3 data"
        elif early_match>0 and (p3<=0 or p3<0.5*early_match):
            v="TRUE REGIME FAILURE — edge survives universe matching but dies in P3"
        else:
            v="NO P3 FAILURE on this reconstruction"
        verdicts[fam]=v
        print(f"  VERDICT: {v}\n")
    return verdicts

# ======================================================================
# PART 2 — PANIC-DAY REBOUND, strict event study
# ======================================================================
def part2(data):
    print("="*74)
    print("PART 2 — PANIC-DAY REBOUND EVENT STUDY (not a strategy)")
    print("="*74)
    # calendar
    by_day=defaultdict(list)   # day -> [(sym, i)]
    idx={s:{r[0]:i for i,r in enumerate(rows)} for s,rows in data.items()}
    for s,rows in data.items():
        for i,r in enumerate(rows):
            if i>0 and r[4]>=MIN_PRICE and rows[i-1][4]>0:
                by_day[r[0]].append((s,i))
    days=sorted(d for d,lst in by_day.items() if len(lst)>=50)
    # market stats per day
    mret={}; breadth={}
    for d in days:
        rs=[]; dn=0
        for s,i in by_day[d]:
            r=data[s][i][4]/data[s][i-1][4]-1
            rs.append(r); dn+=r<0
        mret[d]=sum(rs)/len(rs); breadth[d]=dn/len(rs)
    # panic definition (pre-registered): EW mean <= -2% AND >=70% decliners
    raw_events=[d for d in days if mret[d]<=-0.02 and breadth[d]>=0.70]
    # independence: first day of cluster (no panic in prior 5 trading days)
    day_pos={d:i for i,d in enumerate(days)}
    events=[]
    for d in raw_events:
        if not any(day_pos[d]-day_pos[e]<=5 for e in events if e in day_pos):
            events.append(d)
    ev_pre  =[d for d in events if d< FLOOR_A]
    ev_floor=[d for d in events if in_floor(d)]
    ev_post =[d for d in events if d> FLOOR_B]
    print(f"Panic days raw={len(raw_events)}  independent(>=5d apart)={len(events)}")
    print(f"  pre-floor={len(ev_pre)}  FLOOR-ERA(separated)={len(ev_floor)}  post-floor={len(ev_post)}")
    print("Def: EW mkt ret<=-2% & >=70% decliners & >=50 names. Entry next OPEN;")
    print("only stocks tradeable+unlocked at entry; 1-day drop>15% -> ex-date suspect, excluded.")
    print("NOTE: horizons 1d/2d are MEASUREMENT ONLY — T+2 saleability makes them")
    print("non-executable live; executable horizons are 3/5/10d.\n")

    def liquid_set(d):
        # top-100 by ADV20 among tradeable that day
        pool=[]
        for s,i in by_day[d]:
            a=adv20(data[s],i)*data[s][i][4]
            pool.append((a,s))
        pool.sort(reverse=True)
        return set(s for _,s in pool[:100])

    def event_returns(evs, horizons, liquid=False):
        """-> {H: [per-event EW mean gross return]}, plus per-event names count."""
        out={H:[] for H in horizons}; ncounts=[]; locked_excl=0
        for d in evs:
            liq=liquid_set(d) if liquid else None
            per={H:[] for H in horizons}
            for s,i in by_day[d]:
                rows=data[s]
                r0=rows[i][4]/rows[i-1][4]-1
                if r0<-0.15: continue                      # ex-date suspect
                if liquid and s not in liq: continue
                e=i+1
                if e>=len(rows): continue
                if rows[e][2]==rows[e][3]: locked_excl+=1; continue  # locked at entry
                entry=rows[e][1]
                if entry<MIN_PRICE or entry<=0: continue
                for H in horizons:
                    j=e+H
                    if j<len(rows) and rows[j][4]>0:
                        per[H].append(rows[j][4]/entry-1)
            ns=len(per[horizons[0]])
            if ns>=20:
                ncounts.append(ns)
                for H in horizons:
                    if per[H]: out[H].append(sum(per[H])/len(per[H]))
        return out, ncounts, locked_excl

    HZ=(1,2,3,5,10)
    for label, evs, liquid in (("PRE-FLOOR  all-tradeable", ev_pre,  False),
                               ("PRE-FLOOR  liquid-100",    ev_pre,  True),
                               ("POST-FLOOR all-tradeable", ev_post, False),
                               ("POST-FLOOR liquid-100",    ev_post, True),
                               ("FLOOR-ERA  (separated, do not pool)", ev_floor, False)):
        res,ncounts,locked=event_returns(evs,HZ,liquid)
        n_ev=len(res[1])
        print(f"--- {label}: {n_ev} usable events "
              f"(avg {sum(ncounts)/len(ncounts):.0f} names/event, {locked} locked-at-entry excluded)"
              if n_ev else f"--- {label}: 0 usable events")
        if not n_ev: print(); continue
        print(f"  {'H':>3s}{'gross':>8s}{'net.8%':>8s}{'net1%':>8s}{'net1.2%':>9s}"
              f"{'hit':>6s}{'t(net1%)':>10s}{'drop-top2':>11s}{'exec':>6s}")
        for H in HZ:
            xs=res[H]
            if not xs: continue
            g=sum(xs)/len(xs)
            nets={c:g-c for c in COSTS}
            hit=sum(1 for x in xs if x-0.010>0)/len(xs)
            t=tstat([x-0.010 for x in xs])
            srt=sorted(xs)
            d2=(sum(srt[:-2])/len(srt[:-2])-0.010) if len(srt)>4 else float('nan')
            ex="YES" if H>=3 else "no"
            print(f"  {H:3d}{g:8.2%}{nets[0.008]:8.2%}{nets[0.010]:8.2%}"
                  f"{nets[0.012]:9.2%}{hit:6.0%}{t:10.2f}{d2:11.2%}{ex:>6s}")
        print()

    print("PROMOTION GATE (pre-registered): candidate-strategy status ONLY if at an")
    print("executable horizon (>=3d): net@1.0% mean>0, t>=2, hit>=60%, survives")
    print("drop-top-2 (>0), survives liquid-100, positive in BOTH pre- and post-floor,")
    print(">=15 independent events per regime. Anything less: remains an observation.")

data=load()
print(f"Loaded {len(data)} equity-like instruments "
      f"(index/bond/MF filtered)\n")
v=part1(data)
part2(data)
print()
print("-"*74)
print("REGISTRY / CAVEATS: A-family & periods RECONSTRUCTED (rerun with exact")
print("Round 1 defs if they differ). CGT UNKNOWN=0. Unadjusted prices: ex-date")
print("filter is heuristic (>15% 1-day). Trade-level stats cluster in time —")
print("Part 1 t-stats optimistic; Part 2 aggregates by event day (correct unit).")
print("Fresh container: data re-derived from same two GitHub sources as Round 1.")
