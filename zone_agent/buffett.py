"""
Buffett-style agent: business quality + fair price.   *** DRAFT v1 - rules are NOT final ***

Uses only what is already in the repo: quarterly Sales / Profit / EPS (data/fundamentals/part_*.csv)
plus the AMFI top-1000 list (data/amfi/ranks.csv). Price is OPTIONAL (symbol, close[, high52]):
without it the verdict is quality-only and says so.

Checks (all thresholds live in CFG so the owner can change them in one place):
  QUALITY (60 pts)
    up_years   TTM EPS rose in how many of the last <=3 yearly steps          25 pts
    no_loss    no loss-making quarter in the last 8 quarters                  10 pts
    margin     TTM net margin not falling more than margin_drop_max points    10 pts
    growth     latest TTM EPS growth vs a year ago                            15 pts
  PRICE (40 pts, only if a price is given)
    PEG = P/E / EPS CAGR(%)  -> <=1.5 full marks, <=2.5 part marks
  FLAGS (never change the verdict): near 52W high (expensive?), stale data

Verdict:  GREEN >= green_min %,  YELLOW >= yellow_min %,  else RED.
          RED is forced when TTM EPS <= 0 or 2+ loss quarters.  NODATA when data is missing/too short/old.

No orders, no network, no secrets - it only reads CSV files.
"""
from __future__ import annotations

import csv

import numpy as np
import pandas as pd

from .fundamentals import load as load_fundamentals

CFG = dict(
    min_quarters=8,          # need at least 2 years of consecutive quarters
    max_yearly_steps=3,      # look back at most 3 yearly steps for consistency / CAGR
    eps_growth_good=10.0,    # latest TTM EPS growth (%) that earns full growth points
    margin_drop_max=2.0,     # TTM net margin may fall at most this many points vs a year ago
    peg_good=1.5,            # PEG at or below this = fairly priced
    peg_ok=2.5,              # PEG at or below this = acceptable
    pe_very_high=60.0,       # P/E above this = very expensive whatever the growth
    near_high_pct=5.0,       # within this % of the 52W high -> flag "near 52W high"
    green_min=70.0,          # score % for GREEN
    yellow_min=45.0,         # score % for YELLOW
    stale_days=200,          # newest result older than this (vs newest result in the data) -> NA
)

VERDICT_ICON = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "NODATA": "⚪"}


def top_universe(ranks_path: str = "data/amfi/ranks.csv", n: int = 1000) -> list[dict]:
    """Latest AMFI list, rank 1..n, rows that have an NSE symbol."""
    rows = list(csv.DictReader(open(ranks_path, encoding="utf-8")))
    latest = max(r["period_end"] for r in rows)
    out, seen = [], set()
    for r in rows:
        if r["period_end"] != latest or not r["nse_symbol"] or int(r["rank"]) > n:
            continue
        if r["nse_symbol"] in seen:
            continue
        seen.add(r["nse_symbol"])
        out.append({"symbol": r["nse_symbol"], "rank": int(r["rank"]), "company": r["company"],
                    "category": r["category"], "amfi_period": latest})
    return sorted(out, key=lambda x: x["rank"])


def _usable_tail(g: pd.DataFrame) -> pd.DataFrame:
    """Newest run of consecutive quarters (75-110 days apart) with finite eps/sales/profit."""
    if len(g) == 0:
        return g
    ends = g["period_end"].to_numpy()
    ok = np.isfinite(g[["eps", "sales", "profit"]].to_numpy(float)).all(axis=1)
    kinds = g["type"].to_numpy() if "type" in g.columns else None      # standalone vs consolidated
    start = len(g)
    for i in range(len(g) - 1, -1, -1):
        if not ok[i]:
            break
        if i < len(g) - 1:
            gap = int((ends[i + 1] - ends[i]).astype("timedelta64[D]").astype(int))
            if not 75 <= gap <= 110:
                break
            if kinds is not None and kinds[i] != kinds[i + 1]:      # never compare standalone with consolidated
                break
        start = i
    return g.iloc[start:]


def _na(reason: str, **extra) -> dict:
    base = dict(verdict="NODATA", icon=VERDICT_ICON["NODATA"], score_pct=np.nan, quality=np.nan, price_pts=np.nan,
                reasons=reason, eps_ttm=np.nan, eps_growth=np.nan, eps_cagr=np.nan, up_years="",
                margin_ttm=np.nan, margin_change=np.nan, loss_quarters=np.nan, pe=np.nan, peg=np.nan,
                near_high=False, price_used=False, last_result="", result_type="")
    base.update(extra)
    return base


def score_symbol(g: pd.DataFrame | None, price: float | None = None, high52: float | None = None,
                 asof: pd.Timestamp | None = None, cfg: dict = CFG) -> dict:
    """Score one stock from its quarterly table (output of fundamentals.load()[symbol])."""
    if g is None or len(g) == 0:
        return _na("no fundamentals data")
    t = _usable_tail(g.sort_values("period_end"))
    n = len(t)
    last_pub = pd.Timestamp(t["published"].iloc[-1]) if n else pd.NaT
    last_txt = "" if pd.isna(last_pub) else str(last_pub.date())
    if n < cfg["min_quarters"]:
        return _na(f"only {n} consecutive quarters", last_result=last_txt)
    if asof is not None and pd.notna(last_pub) and (asof - last_pub).days > cfg["stale_days"]:
        return _na("old data (no recent result)", last_result=last_txt)

    rtype = str(t["type"].iloc[-1]) if "type" in t.columns else ""
    eps = t["eps"].to_numpy(float)
    sales = t["sales"].to_numpy(float)
    prof = t["profit"].to_numpy(float)

    steps = min(cfg["max_yearly_steps"], (n - 4) // 4)          # yearly steps we can measure
    ttm = [eps[n - 1 - 4 * k - 3: n - 4 * k].sum() for k in range(steps + 1)]   # newest first
    eps_ttm = float(ttm[0])
    ups = sum(1 for k in range(steps) if ttm[k] > ttm[k + 1])
    growth = (ttm[0] / ttm[1] - 1) * 100 if ttm[1] > 0 else (999.0 if ttm[0] > 0 else np.nan)
    cagr = ((ttm[0] / ttm[steps]) ** (1 / steps) - 1) * 100 if ttm[0] > 0 and ttm[steps] > 0 else np.nan

    def m(a, b):                                                  # TTM net margin over rows [a:b]
        s = sales[a:b].sum()
        return prof[a:b].sum() / s * 100 if s > 0 else np.nan
    margin_now, margin_prev = m(n - 4, n), m(n - 8, n - 4)
    margin_chg = margin_now - margin_prev if np.isfinite(margin_now) and np.isfinite(margin_prev) else np.nan
    loss_q = int((prof[-8:] <= 0).sum())

    reasons, q = [], 0.0
    if rtype == "standalone":
        reasons.append("standalone results only (group may differ)")
    # --- quality ---
    q += 25 * ups / steps
    reasons.append(f"EPS up {ups}/{steps} yrs")
    q += 10 if loss_q == 0 else (5 if loss_q == 1 else 0)
    reasons.append("no loss qtr" if loss_q == 0 else f"{loss_q} loss qtr in last 8")
    if np.isfinite(margin_chg):
        if margin_chg >= -cfg["margin_drop_max"]:
            q += 10
            reasons.append("margin steady")
        elif margin_chg >= -2 * cfg["margin_drop_max"]:
            q += 5
            reasons.append(f"margin {margin_chg:+.1f}pt")
        else:
            reasons.append(f"margin falling {margin_chg:+.1f}pt")
    if np.isfinite(growth):
        if growth >= cfg["eps_growth_good"]:
            q += 15
        elif growth >= 0:
            q += 7
        reasons.append("TTM EPS " + (">+500%" if growth > 500 else f"{growth:+.0f}%"))

    # --- price (optional) ---
    pe = peg = np.nan
    price_pts, price_used = 0.0, price is not None and np.isfinite(price) and price > 0
    near_high = False
    if price_used:
        if eps_ttm > 0:
            pe = price / eps_ttm
            peg = pe / cagr if np.isfinite(cagr) and cagr > 0 else np.nan
            if np.isfinite(peg):
                price_pts = 40 if peg <= cfg["peg_good"] else (25 if peg <= cfg["peg_ok"] else 0)
                if pe > cfg["pe_very_high"]:
                    price_pts = min(price_pts, 10)
                reasons.append(f"PEG {peg:.1f}")
            else:
                price_pts = 10 if pe <= 15 else 0
                reasons.append(f"P/E {pe:.0f}, no EPS growth")
        else:
            reasons.append("no positive EPS -> no P/E")
        if high52 is not None and np.isfinite(high52) and high52 > 0:
            near_high = price >= high52 * (1 - cfg["near_high_pct"] / 100)
            if near_high:
                reasons.append("near 52W high")
    else:
        reasons.append("price not checked")

    score_pct = (q + price_pts) / (100 if price_used else 60) * 100
    verdict = "GREEN" if score_pct >= cfg["green_min"] else ("YELLOW" if score_pct >= cfg["yellow_min"] else "RED")
    if eps_ttm <= 0 or loss_q >= 2:
        verdict = "RED"
    if verdict == "GREEN" and price_used and pe > cfg["pe_very_high"]:
        verdict = "YELLOW"

    return dict(verdict=verdict, icon=VERDICT_ICON[verdict], score_pct=round(score_pct, 1), quality=round(q, 1),
                price_pts=price_pts, reasons="; ".join(reasons), eps_ttm=round(eps_ttm, 2),
                eps_growth=round(growth, 1) if np.isfinite(growth) else np.nan,
                eps_cagr=round(cagr, 1) if np.isfinite(cagr) else np.nan, up_years=f"{ups}/{steps}",
                margin_ttm=round(margin_now, 1) if np.isfinite(margin_now) else np.nan,
                margin_change=round(margin_chg, 1) if np.isfinite(margin_chg) else np.nan,
                loss_quarters=loss_q, pe=round(pe, 1) if np.isfinite(pe) else np.nan,
                peg=round(peg, 2) if np.isfinite(peg) else np.nan, near_high=bool(near_high),
                price_used=bool(price_used), last_result=last_txt, result_type=rtype)


def run(top: int = 1000, prices_path: str | None = None, fundamentals_glob: str = "data/fundamentals/part_*.csv",
        ranks_path: str = "data/amfi/ranks.csv") -> pd.DataFrame:
    """Score the AMFI top-N. Returns one row per stock, best first."""
    F = load_fundamentals(fundamentals_glob)
    uni = top_universe(ranks_path, top)
    prices = {}
    if prices_path:
        pf = pd.read_csv(prices_path)
        prices = {r.symbol: (r.close, getattr(r, "high52", np.nan)) for r in pf.itertuples()}
    newest = max((g["published"].max() for g in F.values() if len(g)), default=pd.NaT)
    rows = []
    for u in uni:
        p, h = prices.get(u["symbol"], (None, None))
        s = score_symbol(F.get(u["symbol"]), p, h, asof=newest)
        rows.append({**u, **s})
    df = pd.DataFrame(rows)
    order = {"GREEN": 0, "YELLOW": 1, "RED": 2, "NODATA": 3}
    df["_o"] = df["verdict"].map(order)
    df = df.sort_values(["_o", "score_pct", "rank"], ascending=[True, False, True]).drop(columns="_o")
    return df.reset_index(drop=True)
