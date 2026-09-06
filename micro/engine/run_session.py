#!/usr/bin/env python3
"""One prospective session: capture -> verify -> fuse -> features -> session artifact.
Repo is READ-ONLY; all output under /tmp/dse_micro_advantage/."""
import argparse, json, os, subprocess, sys, hashlib
sys.path.insert(0,"/tmp/dse_micro_advantage"); sys.path.insert(0,"/home/user/bd-share-market")
REPO="/home/user/bd-share-market"; ROOT="/tmp/dse_micro_advantage"

def sh(cmd):
    print("+"," ".join(cmd),flush=True)
    return subprocess.run(cmd,cwd=REPO,capture_output=True,text=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--date",required=True)
    ap.add_argument("--start",default="03:50"); ap.add_argument("--end",default="08:20")
    ap.add_argument("--skip-capture",action="store_true")
    a=ap.parse_args()
    cap=f"{ROOT}/capture/{a.date}"; st=f"{ROOT}/state/{a.date}"
    os.makedirs(st,exist_ok=True)
    from micro_features import UNIVERSE
    if not a.skip_capture:
        r=sh(["python3","-m","seeing.capture.runner","--out",cap,"--date",a.date,
              "--start",a.start,"--end",a.end,"--symbols",",".join(UNIVERSE)])
        print(r.stdout[-3000:]); print(r.stderr[-2000:])
    v=sh(["python3","-m","seeing","verify","--capture",cap]); print(v.stdout[-1500:])
    f=sh(["python3","-m","seeing","fuse","--capture",cap,"--out",st]); print(f.stdout[-2000:])
    import pandas as pd
    from micro_features import build
    fp=os.path.join(st,"frames.parquet")
    if not os.path.exists(fp):
        json.dump({"date":a.date,"accepted":False,"reason":"no frames.parquet produced"},
                  open(f"{st}/SESSION.json","w"),indent=2); print("REJECTED: no frames"); return 1
    d=build(pd.read_parquet(fp)); d["session"]=a.date
    d.to_parquet(f"{st}/features.parquet",index=False)
    # frozen session-acceptance criteria
    span=(d["t_frame"].max()-d["t_frame"].min()).total_seconds()/3600.0
    acc={"date":a.date,"hash_chain_ok":("FAIL" not in v.stdout.upper()),
         "n_frames":int(len(d)),"n_symbols":int(d["symbol"].nunique()),
         "span_hours":round(span,2),"quality_fail_share":round(float(d["q_exclude"].mean()),4),
         "eligible":int(d["eligible"].sum())}
    acc["accepted"]=bool(acc["hash_chain_ok"] and acc["n_frames"]>=2000 and acc["n_symbols"]>=12
                         and acc["span_hours"]>=3.0 and acc["quality_fail_share"]<=0.40)
    acc["reject_reasons"]=[k for k,c in [("hash_chain",acc["hash_chain_ok"]),("min_frames",acc["n_frames"]>=2000),
        ("min_symbols",acc["n_symbols"]>=12),("min_span",acc["span_hours"]>=3.0),
        ("quality",acc["quality_fail_share"]<=0.40)] if not c]
    json.dump(acc,open(f"{st}/SESSION.json","w"),indent=2)
    print(json.dumps(acc,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
