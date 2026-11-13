"""
Point-in-time quarterly fundamentals (from data/fundamentals/part_*.csv, downloaded by
tools/fetch_fundamentals.py). Only results PUBLISHED BEFORE the trade date are used.

Features (Minervini):
  eps_g, sales_g      latest quarter growth vs the same quarter a year earlier (%)
  eps_g_prev          the same for the quarter before (for acceleration)
  eps_accel           eps_g > eps_g_prev
  code33              EPS growth, sales growth and net margin all rising for 3 quarters in a row
  turnaround          EPS went from loss to profit, or grew 100%+
"""
from __future__ import annotations

import glob

import numpy as np
import pandas as pd


def _growth(now, before):
    if before is None or now is None or np.isnan(before) or np.isnan(now):
        return np.nan
    if before <= 0:
        return 999.0 if now > 0 else np.nan          # loss -> profit = turnaround
    return (now / before - 1) * 100


def load(path_glob: str = "data/fundamentals/part_*.csv") -> dict[str, pd.DataFrame]:
    files = glob.glob(path_glob)
    if not files:
        return {}
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df = df[df["period_end"].notna() & (df["type"] != "none")]
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["published"] = pd.to_datetime(df["published"])
    out = {}
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("period_end").drop_duplicates("period_end").reset_index(drop=True)
        ends = g["period_end"].to_numpy()
        eps, sales, prof = g["eps"].to_numpy(float), g["sales"].to_numpy(float), g["profit"].to_numpy(float)
        eg, sg, mg = [], [], []
        for i in range(len(g)):
            # same quarter last year (period end 330-400 days earlier)
            j = next((k for k in range(i - 1, -1, -1)
                      if 330 <= (ends[i] - ends[k]).astype("timedelta64[D]").astype(int) <= 400), None)
            eg.append(_growth(eps[i], eps[j]) if j is not None else np.nan)
            sg.append(_growth(sales[i], sales[j]) if j is not None else np.nan)
            mg.append(prof[i] / sales[i] * 100 if sales[i] and not np.isnan(sales[i]) and sales[i] > 0 else np.nan)
        g["eps_g"], g["sales_g"], g["margin"] = eg, sg, mg
        out[sym] = g
    return out


def features(F: dict, sym: str, date) -> dict:
    """Fundamental features known before `date` (result published at least 1 day earlier)."""
    g = F.get(sym)
    empty = {"has_fund": False, "eps_g": np.nan, "sales_g": np.nan, "eps_g_prev": np.nan,
             "eps_accel": False, "code33": False, "turnaround": False}
    if g is None:
        return empty
    known = g[g["published"] < pd.Timestamp(date)]
    if len(known) < 1 or np.isnan(known["eps_g"].iloc[-1]):
        return empty
    k = known.tail(4)
    e, s, m = k["eps_g"].to_numpy(), k["sales_g"].to_numpy(), k["margin"].to_numpy()
    accel = len(e) >= 2 and not np.isnan(e[-2]) and e[-1] > e[-2]
    c33 = (len(k) == 4 and not np.isnan(np.r_[e, s, m]).any()
           and all(e[i] > e[i - 1] and s[i] > s[i - 1] and m[i] > m[i - 1] for i in (1, 2, 3)))
    return {"has_fund": True, "eps_g": float(e[-1]), "sales_g": float(s[-1]),
            "eps_g_prev": float(e[-2]) if len(e) >= 2 else np.nan, "eps_accel": bool(accel),
            "code33": bool(c33), "turnaround": bool(e[-1] >= 100)}


def passes(f: dict, mode: str) -> bool:
    """Fundamental filters to test."""
    if mode == "none":
        return True
    if not f["has_fund"]:
        return False
    e, s = f["eps_g"], f["sales_g"]
    if mode == "has":                      # same universe, no condition (fair comparison)
        return True
    if mode == "eps25":
        return e >= 25
    if mode == "eps25_sales25":
        return e >= 25 and s >= 25
    if mode == "eps25_accel":
        return e >= 25 and f["eps_accel"]
    if mode == "code33":
        return f["code33"]
    if mode == "eps20_or_turn":
        return e >= 20 or f["turnaround"]
    if mode == "eps_pos_sales15":
        return e > 0 and s >= 15
    raise ValueError(mode)
