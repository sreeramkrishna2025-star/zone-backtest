"""
LMS (GTF Learning Management System) zone strategy - backtest.

Separate from backtest_setup.py, sepa_backtest.py and minervini_backtest.py.
The rules and how each book rule is coded are in LMS_RULES.md.

Summary of the rules (long only, daily execution):
  - Weekly trend up (weekly close above weekly 50 SMA and that SMA rising).
  - Location: a fresh weekly demand zone (WDZ) that overlaps the daily zone.
  - Execution: fresh daily demand zone (rally-base-rally, <= 2 base candles,
    leg-out closes above the base, price moved >= 5% out of the base).
  - Entry: limit at the zone's proximal line on first touch (no re-entries).
  - Stop: distal line of the zone minus a small buffer.
  - Target: 1:2 risk-reward.  Time stop after MAX_HOLD_BARS sessions.
  - Portfolio: at most MAX_CONCURRENT open trades, one trade per stock.
  - Costs: per-side cost applied to notional.

Usage:
  python lms_zone_backtest.py                     # all NSE symbols in watchlists.json
  python lms_zone_backtest.py --limit 100         # first 100 symbols (quick run)
  python lms_zone_backtest.py --symbols RELIANCE TCS
  python lms_zone_backtest.py --demo              # synthetic data, no network

Outputs (in reports/lms/): trades.csv, summary.txt
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import os
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

# ---------------- settings (change here) ----------------
START_CAPITAL = 1_000_000      # Rs
RISK_PCT = 1.0                 # % of equity risked per trade
MAX_CONCURRENT = 3             # book: max 3 trades at one time
REWARD_RISK = 2.0              # 1:2
SL_BUFFER_PCT = 0.5            # stop placed this % below the zone distal line
MAX_SL_PCT = 8.0               # skip setups whose stop is wider than this (capital block)
MAX_BASE = 2                   # max base candles in a zone
DAILY_MOVE_PCT = 5.0           # price must leave the daily base by this % ...
WEEKLY_MOVE_PCT = 10.0         # ... and the weekly base by this %
DAILY_LOOKAHEAD = 5            # bars to prove the move (daily)
WEEKLY_LOOKAHEAD = 4           # bars to prove the move (weekly)
MAX_HOLD_BARS = 40             # time stop, trading sessions
COST_PER_SIDE_PCT = 0.15       # brokerage + STT + charges + slippage, per side
PERIOD = "10y"
WATCHLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlists.json")
OUT_DIR = os.path.join("reports", "lms")


@dataclass
class Zone:
    kind: str          # "demand" (only demand is traded: long-only)
    prox: float        # top of zone (body top of base candles)
    dist: float        # bottom of zone (lowest low of base candles)
    act: int           # index from which the zone is known (daily idx or weekly idx)
    act_label: pd.Timestamp | None = None     # weekly zones: date the zone became known
    breach_label: pd.Timestamp | None = None  # weekly zones: date a weekly close broke it


# ---------------- universe ----------------
def load_universe(path: str = WATCHLIST) -> list[str]:
    """NSE symbols from watchlists.json -> Yahoo tickers (BAJAJ_AUTO -> BAJAJ-AUTO.NS)."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)["universe"]
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item.startswith("NSE:"):
            continue
        code = item.split(":", 1)[1].replace("_", "-")
        if code.endswith("!") or not code:
            continue
        out.append(code + ".NS")
    return sorted(set(out))


# ---------------- data ----------------
def download_daily(tickers: list[str], chunk: int = 80, log=print) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    data: dict[str, pd.DataFrame] = {}
    for k in range(0, len(tickers), chunk):
        part = tickers[k:k + chunk]
        raw = yf.download(part, period=PERIOD, interval="1d", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        for t in part:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close"]].dropna()
            except KeyError:
                continue
            if len(df) >= 300:
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                data[t] = df
        log(f"downloaded {min(k + chunk, len(tickers))}/{len(tickers)}  usable so far: {len(data)}")
    return data


def make_demo_data(n_symbols: int = 40, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=2600)
    out = {}
    for s in range(n_symbols):
        steps = rng.normal(0.0004, 0.018, len(idx))
        # occasional sharp drops and rallies, so zones form
        for _ in range(12):
            p = rng.integers(0, len(idx) - 10)
            steps[p:p + 4] -= 0.03
            steps[p + 4:p + 9] += 0.025
        close = 100 * np.exp(np.cumsum(steps))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, len(idx)))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, len(idx))))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, len(idx))))
        out[f"DEMO{s:03d}.NS"] = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close}, index=idx)
    return out


# ---------------- zones ----------------
def find_demand_zones(d: pd.DataFrame, move_pct: float, lookahead: int) -> list[Zone]:
    """Rally-base-rally style demand zones with a proven move out of the base.

    Base = 1..MAX_BASE consecutive small-body candles.
    Leg-out = the next candle is exciting (body >= 50% of range), bullish, and
    closes above the base high (closing concept).
    The zone is only accepted once price has moved move_pct above its proximal
    line; it becomes known (act) on the bar where that move is first seen, so
    there is no look-ahead.
    """
    O, H, L, C = (d[c].to_numpy(float) for c in ("Open", "High", "Low", "Close"))
    n = len(d)
    body = np.abs(C - O)
    rng = np.maximum(H - L, 1e-9)
    small = body < 0.5 * rng
    exc_up = (body >= 0.5 * rng) & (C > O)
    body_top = np.maximum(O, C)
    zones: list[Zone] = []
    for i in range(n - 2):
        for b in range(1, MAX_BASE + 1):
            j = i + b
            if j >= n:
                break
            if not small[i:j].all():
                break
            if not exc_up[j]:
                continue
            if C[j] <= H[i:j].max():
                continue
            prox = body_top[i:j].max()
            dist = L[i:j].min()
            target = prox * (1 + move_pct / 100)
            hit = None
            for k in range(j + 1, min(n, j + 1 + lookahead)):
                if H[k] >= target:
                    hit = k
                    break
            if hit is None:
                continue
            zones.append(Zone("demand", prox, dist, act=hit + 1))
    return zones


def weekly_frame(d: pd.DataFrame) -> pd.DataFrame:
    w = d.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    return w.dropna()


def weekly_zone_list(w: pd.DataFrame) -> list[Zone]:
    zones = find_demand_zones(w, WEEKLY_MOVE_PCT, WEEKLY_LOOKAHEAD)
    labels = w.index
    out = []
    C = w["Close"].to_numpy(float)
    for z in zones:
        if z.act >= len(w):
            continue
        z.act_label = labels[z.act]
        z.breach_label = None
        for k in range(z.act, len(w)):
            if C[k] < z.dist:
                z.breach_label = labels[k]
                break
        out.append(z)
    return out


# ---------------- trade logic ----------------
def weekly_trend(w: pd.DataFrame) -> pd.Series:
    sma = w["Close"].rolling(50).mean()
    return (w["Close"] > sma) & (sma > sma.shift(4))


def run_symbol(d: pd.DataFrame, symbol: str) -> list[dict]:
    """Candidate trades for one stock (no portfolio limits yet)."""
    d = d[~d.index.duplicated()].sort_index()
    w = weekly_frame(d)
    if len(w) < 60:
        return []
    wz = weekly_zone_list(w)
    wtrend = weekly_trend(w)
    wlabels = w.index.to_numpy()
    wtr = wtrend.to_numpy()

    dates = d.index.to_numpy()
    O, H, L, C = (d[c].to_numpy(float) for c in ("Open", "High", "Low", "Close"))
    n = len(d)

    def htf_ok(date_np, prox, dist) -> bool:
        pos = np.searchsorted(wlabels, date_np, side="left") - 1   # last weekly bar label < date
        if pos < 0 or not bool(wtr[pos]):
            return False
        for z in wz:
            if z.act_label is None or not (z.act_label < date_np):
                continue
            if z.breach_label is not None and z.breach_label < date_np:
                continue
            if dist <= z.prox and prox >= z.dist:   # daily zone overlaps weekly zone
                return True
        return False

    trades = []
    for z in find_demand_zones(d, DAILY_MOVE_PCT, DAILY_LOOKAHEAD):
        if z.act >= n:
            continue
        entry_k = None
        entry_px = None
        for k in range(z.act, n):
            if L[k] <= z.prox:
                if O[k] < z.dist:            # gapped through the zone
                    break
                entry_px = min(z.prox, O[k])
                entry_k = k
                break
            if C[k] < z.dist:                # zone broken before any touch
                break
        if entry_k is None:
            continue
        if not htf_ok(dates[entry_k], z.prox, z.dist):
            continue

        sl = z.dist * (1 - SL_BUFFER_PCT / 100)
        risk = entry_px - sl
        if risk <= 0 or risk / entry_px * 100 > MAX_SL_PCT:
            continue
        tp = entry_px + REWARD_RISK * risk

        exit_k, exit_px, reason = None, None, None
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
            exit_k, exit_px, reason = last, C[last], ("TIME" if last < n - 1 else "END")

        trades.append({
            "symbol": symbol.replace(".NS", ""),
            "entry_date": pd.Timestamp(dates[entry_k]).date(),
            "entry_px": entry_px,
            "sl": sl,
            "tp": tp,
            "risk_per_share": risk,
            "exit_date": pd.Timestamp(dates[exit_k]).date(),
            "exit_px": exit_px,
            "reason": reason,
        })
    return trades


# ---------------- portfolio ----------------
def simulate_portfolio(cands: list[dict]) -> pd.DataFrame:
    cands = sorted(cands, key=lambda t: (t["entry_date"], t["symbol"]))
    equity = float(START_CAPITAL)
    open_heap: list[tuple] = []          # (exit_date, counter, trade)
    open_syms: set[str] = set()
    counter = 0
    done = []

    def close_until(date):
        nonlocal equity
        while open_heap and open_heap[0][0] < date:
            _, _, tr = heapq.heappop(open_heap)
            open_syms.discard(tr["symbol"])
            equity += tr["pnl"]

    for t in cands:
        close_until(t["entry_date"])
        if t["symbol"] in open_syms or len(open_heap) >= MAX_CONCURRENT:
            continue
        risk_amt = equity * RISK_PCT / 100
        qty = math.floor(risk_amt / t["risk_per_share"])
        qty = min(qty, math.floor(equity / MAX_CONCURRENT / t["entry_px"]))
        if qty <= 0:
            continue
        gross = qty * (t["exit_px"] - t["entry_px"])
        costs = COST_PER_SIDE_PCT / 100 * qty * (t["entry_px"] + t["exit_px"])
        pnl = gross - costs
        tr = dict(t, qty=qty, pnl=pnl,
                  r_multiple=(t["exit_px"] - t["entry_px"]) / t["risk_per_share"],
                  hold_days=(t["exit_date"] - t["entry_date"]).days)
        heapq.heappush(open_heap, (t["exit_date"], counter, tr))
        counter += 1
        open_syms.add(t["symbol"])
        done.append(tr)
    # close everything at the end
    while open_heap:
        _, _, tr = heapq.heappop(open_heap)
        equity += tr["pnl"]
    return pd.DataFrame(done)


# ---------------- report ----------------
def summarize(trades: pd.DataFrame, bench: pd.Series | None) -> str:
    if trades.empty:
        return "No trades taken. Check data and rules."
    t = trades.sort_values("exit_date")
    eq = START_CAPITAL + t["pnl"].cumsum()
    peak = eq.cummax()
    max_dd = ((eq - peak) / peak).min() * 100
    years = max((pd.Timestamp(t["exit_date"].max()) - pd.Timestamp(t["entry_date"].min())).days / 365.25, 1e-9)
    end_eq = eq.iloc[-1]
    cagr = ((end_eq / START_CAPITAL) ** (1 / years) - 1) * 100
    wins = t[t["pnl"] > 0]["pnl"].sum()
    losses = -t[t["pnl"] <= 0]["pnl"].sum()
    pf = wins / losses if losses > 0 else float("inf")
    lines = [
        "LMS zone strategy - backtest summary",
        f"Trades: {len(t)}   Win rate: {(t['pnl'] > 0).mean() * 100:.1f}%",
        f"Avg R: {t['r_multiple'].mean():.2f}   Median R: {t['r_multiple'].median():.2f}",
        f"Profit factor: {pf:.2f}",
        f"Total return: {(end_eq / START_CAPITAL - 1) * 100:.1f}%   CAGR: {cagr:.1f}%   Max drawdown: {max_dd:.1f}%",
        f"Avg hold (calendar days): {t['hold_days'].mean():.0f}",
        f"Exit reasons: {t['reason'].value_counts().to_dict()}",
        f"Period: {t['entry_date'].min()} to {t['exit_date'].max()}",
        f"Stocks traded: {t['symbol'].nunique()}",
        "Settings: " + f"RR={REWARD_RISK}, risk={RISK_PCT}%, max_concurrent={MAX_CONCURRENT}, "
                       f"SL buffer={SL_BUFFER_PCT}%, max SL={MAX_SL_PCT}%, hold={MAX_HOLD_BARS} bars, "
                       f"cost/side={COST_PER_SIDE_PCT}%",
    ]
    if bench is not None and len(bench) > 1:
        b = bench.iloc[-1] / bench.iloc[0]
        by = (bench.index[-1] - bench.index[0]).days / 365.25
        lines.append(f"Benchmark NIFTY buy & hold CAGR: {((b ** (1 / by)) - 1) * 100:.1f}%")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="*", help="NSE symbols, e.g. RELIANCE TCS")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N symbols")
    ap.add_argument("--demo", action="store_true", help="synthetic data, no network")
    args = ap.parse_args()

    if args.demo:
        data = make_demo_data()
        bench = None
    else:
        tickers = load_universe()
        if args.symbols:
            tickers = [s.upper() + ".NS" for s in args.symbols]
        if args.limit:
            tickers = tickers[: args.limit]
        print(f"Universe: {len(tickers)} symbols")
        data = download_daily(tickers)
        try:
            bench_df = download_daily(["^NSEI"], chunk=1)
            bench = bench_df["^NSEI"]["Close"] if "^NSEI" in bench_df else None
        except Exception:
            bench = None

    cands = []
    for sym, df in data.items():
        cands.extend(run_symbol(df, sym))
    print(f"Candidate setups (before portfolio limits): {len(cands)}")

    trades = simulate_portfolio(cands)
    os.makedirs(OUT_DIR, exist_ok=True)
    trades.to_csv(os.path.join(OUT_DIR, "trades.csv"), index=False)
    text = summarize(trades, bench if not args.demo else None)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
