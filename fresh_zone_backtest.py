"""Fresh DBR zone backtest on NIFTY 50 (public repo).

Rules (confirmed step by step in chat, Hinglish):
- DBR demand zones on monthly, quarterly, half-year and yearly candles.
- Drop start = first red candle of the drop run; legout close above its High.
- Base is at most 2 candles (config MAX_BASE_CANDLES).
- A zone exists only after its legout period has closed.
- Zone is "fresh" until the first touch (daily low <= proximal) on any timeframe.
  The first touch is the entry. Touch is also the end of the zone (tested).
  A break of the setup low needs a touch first, so it adds no separate rule.
- Fill: open if the day gaps below proximal, else proximal (limit buy).
- Stop: zone distal. Target: 1:2 reward/risk.
- Same-bar stop and target: stop is taken first (conservative).
- Capital 100000, risk 1% of equity per trade, at most 3 open trades,
  notional at most equity/3, one open trade per stock, cost 0.15% per side.
- Prices are raw (auto_adjust=false) by default, to match TradingView.
  Splits can still create false moves; this is an assumption to check.
"""
import os

import numpy as np
import pandas as pd
import yfinance as yf

from zone_agent.zones import find_zones

NIFTY50 = ["ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO",
    "BAJAJFINSV","BAJFINANCE","BEL","BHARTIARTL","BPCL","BRITANNIA","CIPLA",
    "COALINDIA","DRREDDY","EICHERMOT","ETERNAL","GRASIM","HCLTECH","HDFCBANK",
    "HDFCLIFE","HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
    "INFY","ITC","JSWSTEEL","KOTAKBANK","LT","M&M","MARUTI","NESTLEIND",
    "NTPC","ONGC","POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATAMOTORS","TATASTEEL","TCS","TECHM","TITAN",
    "TRENT","ULTRACEMCO","WIPRO"]

START_CAPITAL = 100_000.0
RISK_PCT = 1.0
MAX_OPEN = 3
REWARD_RISK = 2.0
COST_PER_SIDE = 0.0015
AUTO = os.environ.get("AUTO_ADJUST", "false") == "true"
AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}


def clean(df, t):
    d = df[t][["Open", "High", "Low", "Close"]].dropna()
    d.index = pd.to_datetime(d.index).tz_localize(None)
    return d


def tf_frames(m):
    key = m.index.year * 2 + (m.index.month > 6).astype(int)
    half = m.groupby(key).agg(AGG).dropna()
    half.index = m.groupby(key).apply(lambda g: g.index[-1]).reindex(half.index)
    return {
        "monthly": m,
        "quarterly": m.resample("QE").agg(AGG).dropna(),
        "halfyear": half,
        "yearly": m.resample("YE").agg(AGG).dropna(),
    }


def signals(sym, frames):
    out = []
    for tf, y in frames.items():
        if len(y) < 8:
            continue
        zones, _ = find_zones(y, require_high_break=True)
        for z in zones:
            if z.kind != "demand" or z.pattern != "DBR":
                continue
            # zone geometry only; touches are checked on daily data below
            out.append({
                "sym": sym, "tf": tf,
                "avail": (y.index[z.legout_idx] + pd.offsets.MonthEnd(0)).normalize(),
                "prox": float(z.proximal), "dist": float(z.distal),
            })
    return out


def first_touch(D, sg):
    """Date of the first daily touch after the zone is available, or None."""
    idx = D.index
    start = idx.searchsorted(sg["avail"], side="right")
    L = D["Low"].to_numpy()
    for i in range(start, len(idx)):
        if L[i] <= sg["prox"]:
            return idx[i]
    return None


def simulate_zone(D, sg):
    idx = D.index
    start = idx.searchsorted(sg["avail"], side="right")
    if start >= len(idx):
        return None
    O, H, L, C = D["Open"].to_numpy(), D["High"].to_numpy(), D["Low"].to_numpy(), D["Close"].to_numpy()
    prox, dist = sg["prox"], sg["dist"]
    entry = None
    for i in range(start, len(idx)):
        if L[i] <= prox:
            fill = O[i] if O[i] <= prox else prox
            entry = i
            break
    if entry is None:
        return None
    stop = dist
    if fill <= stop:
        return None
    risk = fill - stop
    target = fill + REWARD_RISK * risk
    exit_i, exit_px, reason = len(idx) - 1, C[-1], "open_end"
    for j in range(entry, len(idx)):
        if L[j] <= stop:
            exit_i, exit_px, reason = j, min(O[j], stop), "stop"
            break
        if H[j] >= target:
            exit_i, exit_px, reason = j, max(O[j], target), "target"
            break
    return {"entry_date": idx[entry], "fill": float(fill), "stop": float(stop),
            "target": float(target), "exit_date": idx[exit_i], "exit_px": float(exit_px),
            "reason": reason}


def main():
    tickers = [t + ".NS" for t in NIFTY50]
    daily = yf.download(tickers + ["^NSEI"], period="max", interval="1d", group_by="ticker",
                        auto_adjust=AUTO, progress=False, threads=True)
    monthly = yf.download(tickers, period="max", interval="1mo", group_by="ticker",
                          auto_adjust=AUTO, progress=False, threads=True)

    n_zones, trades, zone_rows = {}, [], []
    for t in tickers:
        try:
            m, d = clean(monthly, t), clean(daily, t)
        except Exception as e:
            print("skip", t, e)
            continue
        if len(m) < 24 or len(d) < 250:
            continue
        sym = t.replace(".NS", "")
        for sg in signals(sym, tf_frames(m)):
            n_zones[sg["tf"]] = n_zones.get(sg["tf"], 0) + 1
            touch = first_touch(d, sg)
            zone_rows.append({**sg, "status": "tested" if touch is not None else "fresh",
                              "touch_date": touch})
            tr = simulate_zone(d, sg)
            if tr:
                trades.append({**sg, **tr})

    zdf = pd.DataFrame(zone_rows).sort_values(["status", "tf", "sym", "avail"])
    os.makedirs("reports/fresh_backtest", exist_ok=True)
    zdf.to_csv("reports/fresh_backtest/zones.csv", index=False)
    fresh = zdf[zdf["status"] == "fresh"]
    print("ZONES fresh:", len(fresh), "tested:", len(zdf) - len(fresh))
    print(fresh[["sym", "tf", "avail", "prox", "dist"]].round(2).to_string(index=False))
    tested = zdf[zdf["status"] == "tested"]
    print("TESTED ZONES")
    print(tested[["sym", "tf", "avail", "prox", "dist", "touch_date"]].round(2).to_string(index=False))

    trades.sort(key=lambda x: x["entry_date"])
    equity = START_CAPITAL
    open_pos, taken, curve, skipped = [], [], [(None, equity)], 0

    def close_due(upto):
        nonlocal equity, open_pos
        keep = []
        for p in sorted(open_pos, key=lambda p: p["exit_date"]):
            if p["exit_date"] <= upto:
                equity += p["pnl"]
                curve.append((p["exit_date"], equity))
            else:
                keep.append(p)
        open_pos = keep

    for tr in trades:
        close_due(tr["entry_date"])
        if len(open_pos) >= MAX_OPEN or any(p["sym"] == tr["sym"] for p in open_pos):
            skipped += 1
            continue
        per_share = tr["fill"] - tr["stop"]
        shares = int(min((equity * RISK_PCT / 100) // per_share, (equity / MAX_OPEN) // tr["fill"]))
        if shares < 1:
            skipped += 1
            continue
        notional = shares * tr["fill"]
        if sum(p["notional"] for p in open_pos) + notional > equity:
            skipped += 1
            continue
        pnl = shares * (tr["exit_px"] - tr["fill"]) - COST_PER_SIDE * (notional + shares * tr["exit_px"])
        p = {**tr, "shares": shares, "notional": notional, "pnl": pnl,
             "R": (tr["exit_px"] - tr["fill"]) / per_share}
        open_pos.append(p)
        taken.append(p)
    close_due(pd.Timestamp.max)

    print("DBR zones found by timeframe:", n_zones)
    print("signals (zone touched):", len(trades), "| taken:", len(taken), "| skipped (limits):", skipped)
    if not taken:
        print("no trades")
        return
    df = pd.DataFrame(taken)
    first, last = df["entry_date"].min(), df["exit_date"].max()
    years = max((last - first).days / 365.25, 1e-9)
    cagr = (equity / START_CAPITAL) ** (1 / years) - 1
    peak, mdd = -1.0, 0.0
    for _, e in curve:
        peak = max(peak, e)
        mdd = min(mdd, (e - peak) / peak)
    pnl = df["pnl"].to_numpy()
    gain, loss = pnl[pnl > 0].sum(), abs(pnl[pnl < 0].sum())
    pf = gain / loss if loss > 0 else float("inf")
    nifty = clean(daily, "^NSEI")["Close"]
    nif = nifty[(nifty.index >= first) & (nifty.index <= last)]
    ncagr = (nif.iloc[-1] / nif.iloc[0]) ** (1 / years) - 1 if len(nif) > 1 else float("nan")

    print(f"period: {first.date()} to {last.date()} ({years:.1f} years)")
    print(f"final equity: {equity:,.0f}  total: {equity / START_CAPITAL - 1:.1%}  CAGR: {cagr:.2%}")
    print(f"max drawdown: {mdd:.1%}  win rate: {(pnl > 0).mean():.1%}  PF: {pf:.2f}  avg R: {df['R'].mean():.2f}")
    print(f"NIFTY 50 CAGR same period: {ncagr:.2%}")
    print("by timeframe:")
    print(df.groupby("tf").agg(trades=("pnl", "size"), win=("pnl", lambda s: (s > 0).mean()),
                               avg_R=("R", "mean"), pnl=("pnl", "sum")).round(2).to_string())
    os.makedirs("reports/fresh_backtest", exist_ok=True)
    df.to_csv("reports/fresh_backtest/trades.csv", index=False)
    cols = ["sym", "tf", "avail", "entry_date", "fill", "stop", "target", "exit_date", "exit_px", "reason", "R", "pnl"]
    print(df[cols].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
