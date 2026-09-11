"""§12 live product output — one object per symbol per frame."""
import json, numpy as np, pandas as pd
from micro_features import BASELINES, PRIMARY_H

def reason_codes(r):
    c=[]
    if r.get("r_dup"): c.append("DUP_PAYLOAD")
    if r.get("q_degraded"): c.append("SENSOR_DEGRADED")
    if abs(float(r.get("imb_top5") or 0))>0.20: c.append("BOOK_IMBALANCE")
    if float(r.get("signed_flow_w") or 0)>0.10: c.append("BUY_FLOW")
    if float(r.get("signed_flow_w") or 0)<-0.10: c.append("SELL_FLOW")
    if float(r.get("ofi_w") or 0)>0: c.append("OFI_POSITIVE")
    if r.get("multi_level_transition"): c.append("MULTI_LEVEL_TRANSITION")
    if float(r.get("mkt_breadth") or 0)>0.10: c.append("MARKET_BREADTH_UP")
    if float(r.get("mkt_breadth") or 0)<-0.10: c.append("MARKET_BREADTH_DOWN")
    return c or ["NONE"]

def emit(df, probs, threshold, best_baseline):
    """df: eligible frames; probs: DataFrame p_down/p_flat/p_up. Returns list of live objects."""
    out=[]
    marg=(probs["p_up"]-probs["p_down"]).to_numpy()
    for i,(idx,r) in enumerate(df.iterrows()):
        m=float(marg[i])
        state="BID_PRESSURE" if m>threshold else ("ASK_PRESSURE" if m<-threshold else "NEUTRAL")
        if r["q_exclude"]: q="INVALID"
        elif r.get("q_degraded"): q="DEGRADED"
        else: q="GOOD"
        bb=float(r.get(best_baseline) or 0.0)
        out.append({
          "symbol": r["symbol"],
          "timestamp": pd.Timestamp(r["t_frame"]).isoformat(),
          "state": state,
          "confidence": round(abs(m),4),
          "p_up_180": round(float(probs["p_up"].iloc[i]),4),
          "p_down_180": round(float(probs["p_down"].iloc[i]),4),
          "book_pressure": round(float(r.get("book_pressure") or 0),4),
          "flow_pressure": round(float(r.get("signed_flow_w") or 0),4),
          "market_confirmation": round(float(r.get("mkt_breadth") or 0),4),
          "best_baseline_score": round(bb,4),
          "advantage_over_baseline": round(m-np.tanh(bb),4),
          "quality": q,
          "reason_codes": reason_codes(r),
          "truth": {"OBSERVED":["book levels","spread","depth","microprice","cumulative totals",
                                "LTP/OHLC","circuit limits","breadth"],
                    "INFERRED":["signed flow","OFI","sweep/consumption proxy","replenishment",
                                "resilience","pressure","state"],
                    "NEVER_CLAIMED":["queue position","order IDs","individual orders",
                                     "add/cancel decomposition","hidden liquidity","identity"]}})
    return out
