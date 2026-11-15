"""
Drop-Base-Rally (DBR) demand zones on WEEKLY candles, buy only in a weekly uptrend.
NIFTY 50, Rs 1,00,000 starting capital. Also checks the user's example stocks (VBL, MOTHERSON).

Separate from dbr_nifty50_backtest.py (daily version) and the other backtests.
Rules are in DBR_RULES.md (daily) and DBR_EXAMPLES.md (examples).

Weekly rules (weekly bars = resample of daily, week ends Friday):
  - Uptrend: at the close of the prior week, close > SMA10 weekly and SMA10 > SMA40 weekly.
  - Drop: in the 5 weeks before the base, price fell at least 10% and includes a strong bearish week.
  - Base: 1 or 2 small-body weeks. Zone = base.
  - Rally: next week is strong bullish, closes above base high. Price moves 10% above proximal
           within 4 weeks, with green body total > red body total and one strong green week.
  - Entry: limit at proximal on first return (weekly low reaches proximal). Skip gap-through.
  - Stop: distal minus 0.5%. Max stop 15%. Target 1:2. Time stop 40 weeks.
  - Portfolio: max 3 open, one per stock, 1% risk per trade, 0.15% per side cost.

Usage:
  python dbr_weekly_backtest.py           # NIFTY 50 + example stock check
  python dbr_weekly_backtest.py --demo    # synthetic data, no network

Outputs (reports/dbr_weekly/): trades.csv, summary.txt, examples.txt, prices.csv
"""
from __future__ import annotations

import argparse
import heapq
import math
import os

import numpy as np
import pandas as pd

START_CAPITAL = 100_000
RISK_PCT = 1.0
MAX_CONCURRENT = 3
REWARD_RISK = 2.0
SL_BUFFER_PCT = 0.5
MAX_SL_PCT = 15.0
MAX_BASE = 2
DROP_LOOKBACK = 5
MIN_DROP_PCT = 10.0
RALLY_MOVE_PCT = 10.0
RALLY_LOOKAHEAD = 4
STRONG_BODY = 0.6
MAX_HOLD_WEEKS = 40
COST_PER_SIDE_PCT = 0.15
START_DATE = "2014-01-01"
END_DATE = "2026-11-14"
OUT_DIR = os.path.join("reports", "dbr_weekly")

EXAMPLE_TICKERS = ["VBL.NS", "MOTHERSON.NS"]

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL", "BPCL", "BRITANNIA", "CIPLA",
    "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO",
]


def download(tickers: list[str], log=print) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    data: dict[str, pd.DataFrame] = {}
    raw = yf.download(tickers, start=START_DATE, end=END_DATE, interval="1d",
                      group_by="ticker", auto_adjust=True, progress=False, threads=True)
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df[["Open", "High", "Low", "Close"]].dropna()
        except KeyError:
            log(f"no data: {t}")
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        data[t] = df
    log(f"downloaded: {len(data)}/{len(tickers)}")
    return data


def make_demo(n: int = 20, seed: int = 5) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2014-01-01", periods=3000)
    out = {}
    for s in range(n):
        steps = rng.normal(0.0004, 0.018, len(idx))
        for _ in range(12):
            p = rng.integers(0, len(idx) - 10)
            steps[p:p + 4] -= 0.03
            steps[p + 4:p + 9] += 0.025
        close = 100 * np.exp(np.cumsum(steps))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, len(idx)))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, len(idx))))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, len(idx))))
        out[f"DEMO{s:02d}.NS"] = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)
    return out


def to_weekly(d: pd.DataFrame) -> pd.DataFrame:
    d = d[~d.index.duplicated()].sort_index()
    w = d.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    return w.dropna()


def uptrend_flags(C: np.ndarray) -> np.ndarray:
    """True on week k if, at the close of week k-1, close > SMA10 and SMA10 > SMA40 (no look-ahead)."""
    s = pd.Series(C)
    sma10 = s.rolling(10).mean().to_numpy()
    sma40 = s.rolling(40).mean().to_numpy()
    flags = np.zeros(len(C), dtype=bool)
    flags[1:] = (C[:-1] > sma10[:-1]) & (sma10[:-1] > sma40[:-1])
    return flags


def find_zones(O, H, L, C, body_rng) -> list[dict]:
    n = len(C)
    body = np.abs(C - O)
    rng = body_rng
    small = body < 0.5 * rng
    strong_bear = (body >= 0.5 * rng) & (C < O)
    exc_up = (body >= 0.5 * rng) & (C > O)
    body_top = np.maximum(O, C)
    zones = []
    for i in range(DROP_LOOKBACK + 1, n - 2):
        drop_pct = (C[i - 1] - C[i - 1 - DROP_LOOKBACK]) / C[i - 1 - DROP_LOOKBACK] * 100
        if drop_pct > -MIN_DROP_PCT or not strong_bear[i - DROP_LOOKBACK:i].any():
            continue
        for b in range(1, MAX_BASE + 1):
            j = i + b
            if j >= n or not small[i:j].all():
                break
            if not exc_up[j] or C[j] <= H[i:j].max():
                continue
            prox = body_top[i:j].max()
            dist = L[i:j].min()
            target = prox * (1 + RALLY_MOVE_PCT / 100)
            hit = None
            for k in range(j, min(n, j + RALLY_LOOKAHEAD)):
                if H[k] >= target:
                    hit = k
                    break
            if hit is None:
                continue
            win = slice(j, hit + 1)
            green = np.maximum(C[win] - O[win], 0).sum()
            red = np.maximum(O[win] - C[win], 0).sum()
            strong_green = ((C[win] > O[win]) & (body[win] >= STRONG_BODY * rng[win])).any()
            if green > red and strong_green:
                zones.append({"prox": prox, "dist": dist, "act": hit + 1})
    return zones


def trades_for(w: pd.DataFrame, symbol: str, use_trend: bool) -> list[dict]:
    O, H, L, C = (w[c].to_numpy(float) for c in ("Open", "High", "Low", "Close"))
    rng = np.maximum(H - L, 1e-9)
    dates = w.index
    n = len(w)
    up = uptrend_flags(C)
    out = []
    for z in find_zones(O, H, L, C, rng):
        if z["act"] >= n:
            continue
        entry_k = entry_px = None
        for k in range(z["act"], n):
            if L[k] <= z["prox"]:
                if O[k] < z["dist"]:
                    break
                entry_px = min(z["prox"], O[k])
                entry_k = k
                break
            if C[k] < z["dist"]:
                break
        if entry_k is None:
            continue
        if use_trend and not up[entry_k]:
            continue
        sl = z["dist"] * (1 - SL_BUFFER_PCT / 100)
        risk = entry_px - sl
        if risk <= 0 or risk / entry_px * 100 > MAX_SL_PCT:
            continue
        tp = entry_px + REWARD_RISK * risk
        exit_k = exit_px = reason = None
        last = min(n - 1, entry_k + MAX_HOLD_WEEKS)
        for m in range(entry_k, last + 1):
            if O[m] <= sl:
                exit_k, exit_px, reason = m, O[m], "SL_GAP"
                break
            if L[m] <= sl:
                exit_k, exit_px, reason = m, sl, "SL"
                break
            if m > entry_k and H[m] >= tp:
                exit_k, exit_px, reason = m, tp, "TP"
                break
        if exit_k is None:
            exit_k, exit_px = last, C[last]
            reason = "TIME" if last < n - 1 else "END"
        out.append({
            "symbol": symbol.replace(".NS", ""),
            "entry_date": pd.Timestamp(dates[entry_k]).date(),
            "entry_px": entry_px, "sl": sl, "tp": tp, "risk_per_share": risk,
            "exit_date": pd.Timestamp(dates[exit_k]).date(), "exit_px": exit_px,
            "reason": reason,
        })
    return out


def zones_report(w: pd.DataFrame, symbol: str) -> list[str]:
    """Weekly DBR zones found for one stock, with whether the weekly uptrend held at activation."""
    O, H, L, C = (w[c].to_numpy(float) for c in ("Open", "High", "Low", "Close"))
    rng = np.maximum(H - L, 1e-9)
    up = uptrend_flags(C)
    lines = []
    for z in find_zones(O, H, L, C, rng):
        if z["act"] >= len(w):
            continue
        d0 = pd.Timestamp(w.index[z["act"] - 1]).date()
        lines.append(f"{symbol}: zone {z['dist']:.1f}-{z['prox']:.1f}, known from {d0}, "
                     f"weekly uptrend at that week: {'yes' if up[z['act']] else 'no'}")
    return lines or [f"{symbol}: no weekly DBR zone found"]


def simulate(cands: list[dict]) -> pd.DataFrame:
    cands = sorted(cands, key=lambda t: (t["entry_date"], t["symbol"]))
    equity = float(START_CAPITAL)
    heap, counter, open_syms, done = [], 0, set(), []
    for t in cands:
        while heap and heap[0][0] < t["entry_date"]:
            _, _, tr = heapq.heappop(heap)
            open_syms.discard(tr["symbol"])
            equity += tr["pnl"]
        if t["symbol"] in open_syms or len(heap) >= MAX_CONCURRENT:
            continue
        qty = math.floor(equity * RISK_PCT / 100 / t["risk_per_share"])
        qty = min(qty, math.floor(equity / MAX_CONCURRENT / t["entry_px"]))
        if qty <= 0:
            continue
        gross = qty * (t["exit_px"] - t["entry_px"])
        costs = COST_PER_SIDE_PCT / 100 * qty * (t["entry_px"] + t["exit_px"])
        tr = dict(t, qty=qty, pnl=gross - costs,
                  r_multiple=(t["exit_px"] - t["entry_px"]) / t["risk_per_share"],
                  hold_days=(t["exit_date"] - t["entry_date"]).days)
        heapq.heappush(heap, (t["exit_date"], counter, tr))
        counter += 1
        open_syms.add(t["symbol"])
        done.append(tr)
    return pd.DataFrame(done)


def summarize(tr: pd.DataFrame, bench: pd.Series | None, label: str) -> str:
    if tr.empty:
        return f"{label}: no trades."
    t = tr.sort_values("exit_date")
    eq = START_CAPITAL + t["pnl"].cumsum()
    max_dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    years = max((pd.Timestamp(t["exit_date"].max()) - pd.Timestamp(t["entry_date"].min())).days / 365.25, 1e-9)
    end_eq = eq.iloc[-1]
    cagr = ((end_eq / START_CAPITAL) ** (1 / years) - 1) * 100
    wins = t.loc[t["pnl"] > 0, "pnl"].sum()
    losses = -t.loc[t["pnl"] <= 0, "pnl"].sum()
    t_exit = pd.to_datetime(t["exit_date"])
    cut = pd.Timestamp(t["exit_date"].max()) - pd.Timedelta(days=365)
    eq_cut = START_CAPITAL + t.loc[t_exit <= cut, "pnl"].sum()
    last = t[t_exit > cut]
    last_pnl = last["pnl"].sum()
    yr = t.assign(yr=t_exit.dt.year).groupby("yr")["pnl"].sum() / START_CAPITAL * 100
    lines = [
        f"{label}",
        f"Trades: {len(t)}   Win rate: {(t['pnl'] > 0).mean() * 100:.1f}%",
        f"Avg R: {t['r_multiple'].mean():.2f}   Median R: {t['r_multiple'].median():.2f}",
        f"Profit factor: {(wins / losses if losses > 0 else float('inf')):.2f}",
        f"Final equity: Rs {end_eq:,.0f}   Total return: {(end_eq / START_CAPITAL - 1) * 100:.1f}%",
        f"CAGR: {cagr:.1f}%   Max drawdown: {max_dd:.1f}%",
        f"Last 12 months: trades={len(last)}  P&L=Rs {last_pnl:,.0f}  return={last_pnl / eq_cut * 100:.1f}%",
        "Year-wise P&L as % of 1L start: " + ", ".join(f"{y}: {v:.1f}%" for y, v in yr.items()),
        f"Exit reasons: {t['reason'].value_counts().to_dict()}   Stocks traded: {t['symbol'].nunique()}",
        f"Period: {t['entry_date'].min()} to {t['exit_date'].max()}",
    ]
    if bench is not None and len(bench) > 1:
        b = bench.iloc[-1] / bench.iloc[0]
        by = (bench.index[-1] - bench.index[0]).days / 365.25
        lines.append(f"Benchmark NIFTY 50 buy & hold CAGR: {((b ** (1 / by)) - 1) * 100:.1f}%")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    bench = None
    if args.demo:
        data = make_demo()
        examples = {}
    else:
        data = download([t + ".NS" for t in NIFTY50])
        ib = download(["^NSEI"])
        bench = ib["^NSEI"]["Close"] if "^NSEI" in ib else None
        examples = download(EXAMPLE_TICKERS)

    weekly = {sym: to_weekly(df) for sym, df in data.items()}
    os.makedirs(OUT_DIR, exist_ok=True)
    pd.concat([df.assign(symbol=s.replace(".NS", "")) for s, df in data.items()]).to_csv(
        os.path.join(OUT_DIR, "prices.csv"))

    for use_trend, label, folder in [(True, "WEEKLY DBR, uptrend only", "uptrend"),
                                     (False, "WEEKLY DBR, no trend filter", "no_trend")]:
        cands = []
        for sym, w in weekly.items():
            cands.extend(trades_for(w, sym, use_trend))
        trades = simulate(cands)
        out = os.path.join(OUT_DIR, folder)
        os.makedirs(out, exist_ok=True)
        trades.to_csv(os.path.join(out, "trades.csv"), index=False)
        text = summarize(trades, bench, label)
        with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(text + "\n")

    ex_lines = []
    for sym, df in examples.items():
        ex_lines += zones_report(to_weekly(df), sym.replace(".NS", ""))
    for sym, w in weekly.items():
        pass
    with open(os.path.join(OUT_DIR, "examples.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(ex_lines) + "\n")
    print("EXAMPLE CHECK (weekly zones found):")
    print("\n".join(ex_lines))


if __name__ == "__main__":
    main()
