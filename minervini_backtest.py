"""
Mark Minervini style backtest (SEPA): Trend Template + RS rating + VCP breakout.

Everything is decided with data known on that day (no look-ahead).

TREND TEMPLATE (all must be true on the breakout day)
  1. Close > 150 SMA and > 200 SMA          5. Close > 50 SMA
  2. 150 SMA > 200 SMA                      6. Close >= 30% above 52-week low
  3. 200 SMA higher than 1 month ago        7. Close within 25% of 52-week high
  4. 50 SMA > 150 SMA and > 200 SMA         8. RS rating >= 70 (vs all scanned stocks)
     RS = 40% 3-month + 20% 6-month + 20% 9-month + 20% 12-month return, ranked 1-99

VCP / BREAKOUT (simple, objective version)
  - Base = last 40 candles, depth (high-low)/high <= 30%
  - Contraction: range of last 13 candles < range of the first 13 candles, and <= 12%
  - Volume dry-up: average volume of last 10 candles < 50-day average volume
  - Pivot = highest high of last 10 candles; breakout = close above pivot with volume >= 1.4x average
ENTRY  next day's open (skipped if it opens more than 5% above the pivot = extended)
STOP   lowest low of the last 10 candles, never more than 8% below entry (Minervini max-loss rule)
       after +2R the stop moves to entry (break-even)
EXIT   trailing: close below the chosen average (sma50 / sma20 / ema10) -> sell next open

Usage:
  python minervini_backtest.py --all-watchlists --exit sma50
  python minervini_backtest.py --all-watchlists --exit sma20 --no-tt     (breakouts without template)
  python minervini_backtest.py --all-watchlists --market-filter          (only when NIFTY > 200 SMA)
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from zone_agent import data as D

BASE_LEN, TIGHT_LEN, THIRD = 40, 10, 13
MAX_DEPTH, MAX_TIGHT = 0.30, 0.12
VOL_MULT, MAX_EXTENDED, MAX_STOP = 1.4, 0.05, 0.08
RS_MIN = 70


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    x = pd.DataFrame(index=df.index)
    x["sma50"], x["sma150"], x["sma200"] = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    x["sma20"] = c.rolling(20).mean()
    x["ema10"] = c.ewm(span=10, adjust=False).mean()
    x["lo52"], x["hi52"] = l.rolling(252).min(), h.rolling(252).max()
    x["vol50"] = v.shift(1).rolling(50).mean()
    x["vol10"] = v.shift(1).rolling(10).mean()
    hb, lb = h.shift(1).rolling(BASE_LEN).max(), l.shift(1).rolling(BASE_LEN).min()
    x["depth"] = (hb - lb) / hb
    # first third and last third of the base
    r1 = (h.shift(1 + 2 * THIRD + 1).rolling(THIRD).max() - l.shift(1 + 2 * THIRD + 1).rolling(THIRD).min()) / c
    r3 = (h.shift(1).rolling(THIRD).max() - l.shift(1).rolling(THIRD).min()) / c
    x["r1"], x["r3"] = r1, r3
    x["pivot"] = h.shift(1).rolling(TIGHT_LEN).max()
    x["tight_low"] = l.rolling(TIGHT_LEN + 1).min()
    # run BEFORE the base: base high vs lowest low of the 6 months before the base started
    x["prior_run"] = (hb / l.shift(1 + BASE_LEN).rolling(126).min() - 1) * 100
    x["sma50_up"] = x.sma50 > x.sma50.shift(22)
    x["tt"] = ((c > x.sma150) & (c > x.sma200) & (x.sma150 > x.sma200)
               & (x.sma200 > x.sma200.shift(22)) & (x.sma50 > x.sma150) & (x.sma50 > x.sma200)
               & (c > x.sma50) & (c >= 1.3 * x.lo52) & (c >= 0.75 * x.hi52))
    x["vcp"] = ((x.depth <= MAX_DEPTH) & (x.r3 < x.r1) & (x.r3 <= MAX_TIGHT) & (x.vol10 < x.vol50))
    x["breakout"] = (c > x["pivot"]) & (v >= VOL_MULT * x.vol50)
    return x


def rs_ratings(closes: pd.DataFrame) -> pd.DataFrame:
    perf = (0.4 * closes / closes.shift(63) + 0.2 * closes / closes.shift(126)
            + 0.2 * closes / closes.shift(189) + 0.2 * closes / closes.shift(252))
    cnt = perf.notna().sum(axis=1)
    rk = perf.rank(axis=1, pct=True) * 99
    rk[cnt < 100] = np.nan                    # need a real universe to rank against
    return rk


def run_stock(sym, df, x, rs, cfg, mkt_ok, breadth=None) -> list[dict]:
    O, H, L, Cl = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    trail = x[cfg["exit"]].to_numpy()
    trail2 = x["sma20"].to_numpy()          # tighter trail after a big gain (--protect)
    sig = x["breakout"] & x["vcp"]
    if not cfg["no_tt"]:
        sig &= x["tt"] & (rs.reindex(df.index) >= cfg.get("rs_min", RS_MIN))
    if cfg.get("clean"):
        sig &= ((x.depth >= 0.12) & (x.depth <= 0.25) & (x.r3 >= 0.05)
                & (df["Volume"] >= 1.8 * x.vol50) & (df["Volume"] <= 4 * x.vol50)
                & (df["Close"] >= 0.90 * x.hi52))
    if cfg.get("sma50_up"):
        sig &= x["sma50_up"]
    if cfg.get("max_prior_run"):
        sig &= x["prior_run"] <= cfg["max_prior_run"]
    if cfg["market_filter"]:
        sig &= mkt_ok.reindex(df.index).fillna(False).astype(bool)
    idx = np.flatnonzero(sig.fillna(False).to_numpy())
    piv, tl = x["pivot"].to_numpy(), x["tight_low"].to_numpy()
    rsv = rs.reindex(df.index).to_numpy(dtype=float)
    dep, r3, v50 = x["depth"].to_numpy(), x["r3"].to_numpy(), x["vol50"].to_numpy()
    hi52, s50, V = x["hi52"].to_numpy(), x["sma50"].to_numpy(), df["Volume"].to_numpy()
    brv = breadth.reindex(df.index).to_numpy(dtype=float) if breadth is not None else None
    out, busy = [], -1
    n = len(df)
    for t in idx:
        e = t + 1
        if e >= n or t <= busy:
            continue
        entry = O[e]
        if entry > piv[t] * (1 + MAX_EXTENDED):
            continue                                  # gapped up too far = extended
        stop = max(tl[t], entry * (1 - MAX_STOP))
        if stop >= entry:
            continue
        risk = entry - stop
        exit_px, j, why = None, e, ""
        for j in range(e, n):
            if L[j] <= stop:
                exit_px = min(O[j], stop) if j > e else stop
                why = "break-even" if stop >= entry else "stop"
                break
            if H[j] >= entry + 2 * risk and stop < entry:
                stop = entry                          # protect: move to break-even
            big = cfg.get("protect") and H[e:j + 1].max() >= entry * (1 + cfg["protect"] / 100)
            if big and Cl[j] < trail2[j] and j + 1 < n:
                exit_px, why = O[j + 1], "protect close<sma20"
                j += 1
                break
            if Cl[j] < trail[j] and j + 1 < n:
                exit_px, why = O[j + 1], f"close<{cfg['exit']}"
                j += 1
                break
        if exit_px is None:
            exit_px, why = Cl[-1], "open"
            j = n - 1
        busy = j
        out.append({"symbol": sym, "signal_date": str(df.index[t].date()), "entry_date": str(df.index[e].date()),
                    "entry": round(entry, 2), "stop": round(entry - risk, 2), "exit_date": str(df.index[j].date()),
                    "exit": round(exit_px, 2), "why": why, "days": j - e,
                    "R": round((exit_px - entry) / risk, 2), "pct": round((exit_px / entry - 1) * 100, 2),
                    # features on the breakout day (for analysis)
                    "rs": round(float(rsv[t]), 1) if not np.isnan(rsv[t]) else None,
                    "depth": round(float(dep[t]) * 100, 1), "tight": round(float(r3[t]) * 100, 1),
                    "vol_x": round(float(V[t] / v50[t]), 2) if v50[t] else None,
                    "from_high": round((1 - Cl[t] / hi52[t]) * 100, 1),
                    "above_sma50": round((Cl[t] / s50[t] - 1) * 100, 1),
                    "risk_pct": round(risk / entry * 100, 1),
                    "breadth": round(float(brv[t]), 1) if brv is not None and not np.isnan(brv[t]) else None})
    return out


def summary(t: pd.DataFrame, label: str):
    if t.empty:
        print(f"{label}: no trades")
        return
    w = t[t.pct > 0]
    gp, gl = t.pct[t.pct > 0].sum(), -t.pct[t.pct < 0].sum()
    print(f"{label}: trades {len(t)} | win {len(w) / len(t) * 100:.1f}% | avg {t.pct.mean():+.2f}% "
          f"({t.R.mean():+.2f}R) | avg win {w.pct.mean():+.1f}% | avg loss {t[t.pct <= 0].pct.mean():+.1f}% "
          f"| PF {gp / max(gl, 1e-9):.2f} | median days {t.days.median():.0f}")


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--watchlist", nargs="*")
    ap.add_argument("--all-watchlists", action="store_true")
    ap.add_argument("--exit", choices=["sma50", "sma20", "ema10"], default="sma50")
    ap.add_argument("--no-tt", action="store_true", help="breakouts WITHOUT trend template / RS (comparison)")
    ap.add_argument("--clean", action="store_true", help="base 12-25%% deep, tight>=5%%, volume 1.8-4x, within 10%% of 52w high")
    ap.add_argument("--rs-min", type=float, default=RS_MIN)
    ap.add_argument("--sma50-up", action="store_true", help="50 SMA higher than 1 month ago")
    ap.add_argument("--max-prior-run", type=float, default=0, help="skip late stage: run before base above this %%")
    ap.add_argument("--protect", type=float, default=0, help="after this %% gain trail with 20 SMA")
    ap.add_argument("--market-filter", action="store_true", help="only when NIFTY 50 is above its 200 SMA")
    a = ap.parse_args(argv)
    cfg = {"exit": a.exit, "no_tt": a.no_tt, "market_filter": a.market_filter, "clean": a.clean,
           "rs_min": a.rs_min, "sma50_up": a.sma50_up, "max_prior_run": a.max_prior_run, "protect": a.protect}
    print("Options:", cfg)
    if a.watchlist or a.all_watchlists:
        from run_agent import pick_watchlists
        targets = [y for y, _ in pick_watchlists(a.watchlist, a.all_watchlists)]
    else:
        targets = [D.yahoo_symbol(s) for s in (a.symbols or [])]
    targets = list(dict.fromkeys(t for t in targets if not t.startswith("^")))
    D.prefetch_daily(targets + ["^NSEI"])
    data = {}
    for y in targets:
        try:
            df = D.get_candles(y, "1d")
            df = df[df["Volume"] > 0]
            if len(df) >= 300:
                data[y] = df
        except Exception:  # noqa: BLE001
            pass
    print(f"stocks with data: {len(data)}")
    closes = pd.DataFrame({y: df["Close"] for y, df in data.items()}).sort_index()
    rs = rs_ratings(closes)
    # market breadth: % of stocks above their own 200 SMA (needs 100+ stocks)
    above = closes > closes.rolling(200).mean()
    valid = closes.rolling(200).mean().notna()
    breadth = above.sum(axis=1) / valid.sum(axis=1) * 100
    breadth[valid.sum(axis=1) < 100] = np.nan
    try:
        nifty = D.get_candles("^NSEI", "1d")["Close"]
        mkt_ok = nifty > nifty.rolling(200).mean()
    except Exception:  # noqa: BLE001
        mkt_ok = pd.Series(True, index=closes.index)
    trades = []
    for y, df in data.items():
        sym = y.replace(".NS", "").replace(".BO", " (BSE)")
        try:
            trades += run_stock(sym, df, indicators(df), rs[y], cfg, mkt_ok, breadth)
        except Exception as e:  # noqa: BLE001
            print(f"{sym}: error {str(e)[:100]}")
    t = pd.DataFrame(trades)
    if t.empty:
        print("no trades")
        return 0
    t = t.sort_values("entry_date")
    print()
    summary(t, "ALL  ")
    summary(t[t.entry_date >= "2015"], "2015+")
    summary(t[t.entry_date >= "2020"], "2020+")
    summary(t[t.entry_date >= "2024"], "2024+")
    print("\nBy year:")
    t["year"] = t.entry_date.str[:4]
    for yr, g in t.groupby("year"):
        print(f"  {yr}: {len(g):4d} trades  win {(g.pct > 0).mean() * 100:5.1f}%  avg {g.pct.mean():+6.2f}%  sum {g.pct.sum():+8.1f}%")
    print("\nBiggest winners:")
    print(t.nlargest(10, "pct")[["symbol", "entry_date", "entry", "exit_date", "exit", "pct", "days"]].to_string(index=False))
    print("\nTRADES CSV")
    print(t.drop(columns="year").to_csv(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
