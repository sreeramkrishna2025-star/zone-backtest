"""
Drop-Base-Rally (DBR) demand zone backtest on NIFTY 50, starting capital Rs 1,00,000.

Separate from lms_zone_backtest.py, backtest_setup.py, minervini_backtest.py and sepa_backtest.py.
Rules are in DBR_RULES.md.

Rule summary (long only, daily bars):
  1. DROP   : in the 5 bars before the base, price fell at least MIN_DROP_PCT and the window
              contains a strong bearish candle (body >= 50% of range).
  2. BASE   : 1 or 2 consecutive small-body candles (body < 50% of range). Zone = base.
              proximal = top of base bodies, distal = lowest low of base.
  3. RALLY  : the next candle is a strong bullish candle that closes above the base high.
              Price must then move RALLY_MOVE_PCT above the proximal line within RALLY_LOOKAHEAD bars.
              Buyers must be in control in that rally window:
                - green body total > red body total, and
                - at least one green candle with body >= 60% of its range.
              The zone is only known once this is seen on that bar (no look-ahead).
  4. ENTRY  : limit at proximal on the first return of price into the zone (retest).
              Skip if the bar gaps through the zone.
  5. STOP   : distal minus SL_BUFFER_PCT.   TARGET: 1:2.   TIME STOP: MAX_HOLD_BARS sessions.
  6. PORTFOLIO: max MAX_CONCURRENT open trades, one trade per stock, 1% risk per trade,
              costs COST_PER_SIDE_PCT per side.

Usage:
  python dbr_nifty50_backtest.py            # NIFTY 50, Rs 1,00,000
  python dbr_nifty50_backtest.py --demo     # synthetic data, no network

Outputs (reports/dbr/): trades.csv, summary.txt, prices.csv (the data used, for repeat runs)
"""
from __future__ import annotations

import argparse
import heapq
import math
import os

import numpy as np
import pandas as pd

# ---------------- settings ----------------
START_CAPITAL = 100_000
RISK_PCT = 1.0
MAX_CONCURRENT = 3
REWARD_RISK = 2.0
SL_BUFFER_PCT = 0.5
MAX_SL_PCT = 8.0
MAX_BASE = 2
MIN_DROP_PCT = 5.0           # net fall in the 5 bars before the base
DROP_LOOKBACK = 5
RALLY_MOVE_PCT = 5.0         # price must leave the base by this % ...
RALLY_LOOKAHEAD = 5          # ... within this many bars
STRONG_BODY = 0.6            # rally candle body share of range
MAX_HOLD_BARS = 40
COST_PER_SIDE_PCT = 0.15
START_DATE = "2016-01-01"    # fixed range, so every run uses the same window
END_DATE = "2026-11-14"
OUT_DIR = os.path.join("reports", "dbr")

# NIFTY 50 constituents, from memory (2025). Verify against NSE before relying on it.
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


# ---------------- data ----------------
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
        if len(df) >= 300:
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            data[t] = df
    log(f"usable: {len(data)}/{len(tickers)}")
    return data


def make_demo(n: int = 20, seed: int = 11) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2016-01-01", periods=2600)
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


# ---------------- zones ----------------
def find_dbr_zones(d: pd.DataFrame) -> list[dict]:
    O, H, L, C = (d[c].to_numpy(float) for c in ("Open", "High", "Low", "Close"))
    n = len(d)
    body = np.abs(C - O)
    rng = np.maximum(H - L, 1e-9)
    small = body < 0.5 * rng
    strong_bear = (body >= 0.5 * rng) & (C < O)
    exc_up = (body >= 0.5 * rng) & (C > O)
    body_top = np.maximum(O, C)
    zones = []
    for i in range(DROP_LOOKBACK + 1, n - 2):
        # 1. DROP before the base
        drop_pct = (C[i - 1] - C[i - 1 - DROP_LOOKBACK]) / C[i - 1 - DROP_LOOKBACK] * 100
        if drop_pct > -MIN_DROP_PCT or not strong_bear[i - DROP_LOOKBACK:i].any():
            continue
        for b in range(1, MAX_BASE + 1):
            j = i + b
            if j >= n or not small[i:j].all():
                break
            # 3. RALLY: strong bullish candle closing above base high
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
            # buyers in control during the rally window j..hit
            win = slice(j, hit + 1)
            green_body = np.maximum(C[win] - O[win], 0).sum()
            red_body = np.maximum(O[win] - C[win], 0).sum()
            strong_green = ((C[win] > O[win]) & (body[win] >= STRONG_BODY * rng[win])).any()
            if not (green_body > red_body and strong_green):
                continue
            zones.append({"prox": prox, "dist": dist, "act": hit + 1})
    return zones


# ---------------- trades ----------------
def uptrend_flags(C: np.ndarray) -> np.ndarray:
    """True on bar k if, at the close of bar k-1, close > SMA50 and SMA50 > SMA200 (no look-ahead)."""
    s = pd.Series(C)
    sma50 = s.rolling(50).mean().to_numpy()
    sma200 = s.rolling(200).mean().to_numpy()
    flags = np.zeros(len(C), dtype=bool)
    flags[1:] = (C[:-1] > sma50[:-1]) & (sma50[:-1] > sma200[:-1])
    return flags


def trades_for(d: pd.DataFrame, symbol: str, use_trend: bool = True) -> list[dict]:
    d = d[~d.index.duplicated()].sort_index()
    O, H, L, C = (d[c].to_numpy(float) for c in ("Open", "High", "Low", "Close"))
    dates = d.index
    n = len(d)
    up = uptrend_flags(C)
    out = []
    for z in find_dbr_zones(d):
        if z["act"] >= n:
            continue
        entry_k = None
        entry_px = None
        for k in range(z["act"], n):
            if L[k] <= z["prox"]:             # first return into the zone
                if O[k] < z["dist"]:           # gapped through
                    break
                entry_px = min(z["prox"], O[k])
                entry_k = k
                break
            if C[k] < z["dist"]:               # zone broken before any return
                break
        if entry_k is None:
            continue
        if use_trend and not up[entry_k]:      # buy only in an uptrend
            continue
        sl = z["dist"] * (1 - SL_BUFFER_PCT / 100)
        risk = entry_px - sl
        if risk <= 0 or risk / entry_px * 100 > MAX_SL_PCT:
            continue
        tp = entry_px + REWARD_RISK * risk
        exit_k = exit_px = reason = None
        last = min(n - 1, entry_k + MAX_HOLD_BARS)
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
        return "No trades. Check data and rules."
    t = tr.sort_values("exit_date")
    eq = START_CAPITAL + t["pnl"].cumsum()
    max_dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    years = max((pd.Timestamp(t["exit_date"].max()) - pd.Timestamp(t["entry_date"].min())).days / 365.25, 1e-9)
    end_eq = eq.iloc[-1]
    cagr = ((end_eq / START_CAPITAL) ** (1 / years) - 1) * 100
    wins = t.loc[t["pnl"] > 0, "pnl"].sum()
    losses = -t.loc[t["pnl"] <= 0, "pnl"].sum()
    end = pd.Timestamp(t["exit_date"].max())
    cut = end - pd.Timedelta(days=365)
    t_exit = pd.to_datetime(t["exit_date"])
    eq_cut = START_CAPITAL + t.loc[t_exit <= cut, "pnl"].sum()
    last = t[t_exit > cut]
    last_pnl = last["pnl"].sum()
    yr = t.assign(yr=t_exit.dt.year).groupby("yr")["pnl"].sum() / START_CAPITAL * 100
    lines = [
        f"DBR strategy on NIFTY 50 - Rs 1,00,000 start - {label}",
        f"Trades: {len(t)}   Win rate: {(t['pnl'] > 0).mean() * 100:.1f}%",
        f"Avg R: {t['r_multiple'].mean():.2f}   Median R: {t['r_multiple'].median():.2f}",
        f"Profit factor: {(wins / losses if losses > 0 else float('inf')):.2f}",
        f"Final equity: Rs {end_eq:,.0f}   Total return: {(end_eq / START_CAPITAL - 1) * 100:.1f}%",
        f"CAGR: {cagr:.1f}%   Max drawdown: {max_dd:.1f}%",
        f"Last 12 months: trades={len(last)}  P&L=Rs {last_pnl:,.0f}  "
        f"return={last_pnl / eq_cut * 100:.1f}%",
        "Year-wise P&L as % of 1L start (sum of trade pnl): " +
        ", ".join(f"{y}: {v:.1f}%" for y, v in yr.items()),
        f"Exit reasons: {t['reason'].value_counts().to_dict()}",
        f"Period: {t['entry_date'].min()} to {t['exit_date'].max()}   Stocks traded: {t['symbol'].nunique()}",
        f"Settings: RR={REWARD_RISK}, risk={RISK_PCT}%, max_concurrent={MAX_CONCURRENT}, "
        f"uptrend_filter={'on' if 'uptrend' in label else 'off'}, min_drop={MIN_DROP_PCT}%, base<= {MAX_BASE} candles, rally_move={RALLY_MOVE_PCT}%, "
        f"max_SL={MAX_SL_PCT}%, hold={MAX_HOLD_BARS} bars, cost/side={COST_PER_SIDE_PCT}%",
    ]
    if bench is not None and len(bench) > 1:
        b = bench.iloc[-1] / bench.iloc[0]
        by = (bench.index[-1] - bench.index[0]).days / 365.25
        lines.append(f"Benchmark NIFTY 50 index buy & hold CAGR: {((b ** (1 / by)) - 1) * 100:.1f}%")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="synthetic data, no network")
    args = ap.parse_args()

    bench = None
    if args.demo:
        data = make_demo()
    else:
        tickers = [t + ".NS" for t in NIFTY50]
        data = download(tickers)
        try:
            idx_df = download(["^NSEI"])
            bench = idx_df["^NSEI"]["Close"] if "^NSEI" in idx_df else None
        except Exception:
            bench = None

    if not args.demo:
        frames = [df.assign(symbol=sym.replace(".NS", "")) for sym, df in data.items()]
        os.makedirs(OUT_DIR, exist_ok=True)
        pd.concat(frames).to_csv(os.path.join(OUT_DIR, "prices.csv"))

    for use_trend, label, folder in [(True, "uptrend only", "uptrend"), (False, "no trend filter", "no_trend")]:
        cands = []
        for sym, df in data.items():
            cands.extend(trades_for(df, sym, use_trend=use_trend))
        print(f"[{label}] candidate setups before portfolio limits: {len(cands)}")
        trades = simulate(cands)
        out = os.path.join(OUT_DIR, folder)
        os.makedirs(out, exist_ok=True)
        trades.to_csv(os.path.join(out, "trades.csv"), index=False)
        text = summarize(trades, bench, label)
        with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(text + "\n")

if __name__ == "__main__":
    main()
