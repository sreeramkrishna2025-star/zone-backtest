"""
Break & Bounce intraday backtest (Previous Day High/Low breakout + retest).

RULES (as described in the video, made exact so a computer can test them)
  1. DAILY : mark previous day's High (PDH) and Low (PDL).
  2. 15 MIN: wait for a 15-min candle to CLOSE above PDH (buy side) or below PDL (sell side).
  3. 5 MIN : after that 15-min candle has closed, wait for price to come back to the level:
             BUY  -> a 5-min candle whose low touches PDH (within --touch %) and closes above PDH,
                     and the candle is a Hammer or a Bullish Engulfing.
             SELL -> mirror: high touches PDL, closes below PDL, Inverted Hammer (shooting star)
                     or Bearish Engulfing.
             If a 5-min candle closes more than --fail % back on the wrong side of the level,
             the breakout is treated as failed for that day.
  ENTRY  open of the NEXT 5-min candle (the pattern is only known after its candle closes).
         No new entries after 14:30. One trade per stock per day (first signal wins).
  STOP   pattern candle low (buy) / high (sell) minus/plus --sl-buffer %.
  TARGET 1:2 and 1:3 risk-reward (both tested on the same entries).
  EXIT   stop, target, or square-off at 15:15.

HONEST TESTING
  - If stop and target are both inside the same 5-min candle, the STOP is assumed first.
  - Indian intraday costs (Dhan-style): brokerage min(Rs 20, 0.03%) per order, STT 0.025% on
    sell, NSE txn 0.00297%, SEBI 0.0001%, stamp 0.003% on buy, GST 18%, plus --slippage % per side.
  - Liquidity filter: average daily turnover >= --min-turnover crore and price >= --min-price.
  - Position size: risk --risk-pct of capital per trade, position value capped by MIS leverage.
  - A "naive" result (no costs, target assumed first) is also shown, to compare with how
    such videos usually report.

DATA  Yahoo Finance: 5-min candles are only available for the last ~60 days.
      15-min candles are built from 5-min candles aligned to 9:15.

Usage:
  python break_bounce_backtest.py                                  (Nifty 1000 list)
  python break_bounce_backtest.py --symbols RELIANCE TCS INFY
  python break_bounce_backtest.py --demo                           (offline logic check)
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from zone_agent import data as D

OUT_DIR = "backtests/break_bounce"
RRS = (2.0, 3.0)


# ----------------------------------------------------------------------------- candles
def is_hammer(o, h, l, c) -> bool:
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    return body <= rng / 3 and lower >= 2 * max(body, rng * 0.05) and upper <= rng * 0.25


def is_inv_hammer(o, h, l, c) -> bool:
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    return body <= rng / 3 and upper >= 2 * max(body, rng * 0.05) and lower <= rng * 0.25


def is_bull_engulf(po, pc, o, c) -> bool:
    return pc < po and c > o and o <= pc and c >= po and (c - o) > (po - pc)


def is_bear_engulf(po, pc, o, c) -> bool:
    return pc > po and c < o and o >= pc and c <= po and (o - c) > (pc - po)


# ----------------------------------------------------------------------------- costs
def trade_costs(entry: float, exit_: float, qty: int, side: int, slip: float) -> float:
    buy_px, sell_px = (entry, exit_) if side == 1 else (exit_, entry)
    buy_val, sell_val = buy_px * qty, sell_px * qty
    brok = min(20.0, 0.0003 * buy_val) + min(20.0, 0.0003 * sell_val)
    stt = 0.00025 * sell_val
    txn = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    stamp = 0.00003 * buy_val
    gst = 0.18 * (brok + txn + sebi)
    slippage = slip * (buy_val + sell_val)
    return brok + stt + txn + sebi + stamp + gst + slippage


# ----------------------------------------------------------------------------- signals
@dataclass
class Params:
    touch: float = 0.001      # retest: candle must come within 0.1% of the level
    fail: float = 0.003       # breakout failed if a 5m close is 0.3% beyond on the wrong side
    sl_buffer: float = 0.0005 # stop 0.05% beyond pattern candle
    min_risk: float = 0.0015  # skip if stop is closer than 0.15% (costs would eat it)
    last_entry: str = "14:30"
    squareoff: str = "15:15"


def find_signal(day5: pd.DataFrame, pdh: float, pdl: float, p: Params):
    """Return dict(side, i_pattern, sl, pattern) for the first valid signal of the day, or None."""
    t = day5.index
    o, h, l, c = (day5[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    # 15-min candles built from this day's 5-min candles
    d15 = D.resample_nse(day5, 15)
    confirm = {}
    for side, lvl in ((1, pdh), (-1, pdl)):
        hit = d15[(d15["Close"] > lvl)] if side == 1 else d15[(d15["Close"] < lvl)]
        if len(hit):
            confirm[side] = hit.index[0] + pd.Timedelta(minutes=15)
    if not confirm:
        return None
    last_entry = pd.Timestamp(f"{t[0].date()} {p.last_entry}", tz=t.tz)
    best = None
    for side, ctime in confirm.items():
        lvl = pdh if side == 1 else pdl
        start = np.searchsorted(t, ctime)
        for i in range(max(start, 1), len(t) - 1):
            if t[i + 1] > last_entry:
                break
            if side == 1:
                if c[i] < lvl * (1 - p.fail):
                    break
                touched = l[i] <= lvl * (1 + p.touch) and c[i] > lvl
                pat = ("hammer" if is_hammer(o[i], h[i], l[i], c[i]) else
                       "engulfing" if is_bull_engulf(o[i - 1], c[i - 1], o[i], c[i]) else None)
                if touched and pat:
                    sl = l[i] * (1 - p.sl_buffer)
                    if (best is None) or i < best["i"]:
                        best = dict(side=1, i=i, sl=sl, pattern=pat, level=lvl)
                    break
            else:
                if c[i] > lvl * (1 + p.fail):
                    break
                touched = h[i] >= lvl * (1 - p.touch) and c[i] < lvl
                pat = ("inv_hammer" if is_inv_hammer(o[i], h[i], l[i], c[i]) else
                       "engulfing" if is_bear_engulf(o[i - 1], c[i - 1], o[i], c[i]) else None)
                if touched and pat:
                    sl = h[i] * (1 + p.sl_buffer)
                    if (best is None) or i < best["i"]:
                        best = dict(side=-1, i=i, sl=sl, pattern=pat, level=lvl)
                    break
    return best


def manage(day5: pd.DataFrame, sig: dict, rr: float, p: Params, worst_case: bool = True):
    """Simulate exit. Returns (entry, exit, reason, exit_time) or None if entry invalid."""
    t = day5.index
    o, h, l, c = (day5[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    side, sl = sig["side"], sig["sl"]
    j = sig["i"] + 1
    entry = o[j]
    risk = (entry - sl) * side
    if risk <= 0 or risk / entry < p.min_risk:
        return None
    tgt = entry + side * rr * risk
    sq = pd.Timestamp(f"{t[0].date()} {p.squareoff}", tz=t.tz)
    for k in range(j, len(t)):
        if t[k] >= sq:
            return entry, o[k], "squareoff", t[k]
        if side == 1:
            hit_sl, hit_tg = l[k] <= sl, h[k] >= tgt
            sl_px, tg_px = min(o[k], sl) if k > j else sl, max(o[k], tgt) if k > j else tgt
        else:
            hit_sl, hit_tg = h[k] >= sl, l[k] <= tgt
            sl_px, tg_px = max(o[k], sl) if k > j else sl, min(o[k], tgt) if k > j else tgt
        if hit_sl and hit_tg:
            return (entry, sl_px, "stop", t[k]) if worst_case else (entry, tg_px, "target", t[k])
        if hit_sl:
            return entry, sl_px, "stop", t[k]
        if hit_tg:
            return entry, tg_px, "target", t[k]
    return entry, c[-1], "squareoff", t[-1]


# ----------------------------------------------------------------------------- data
def download(symbols: list[str], interval: str, period: str, chunk: int = 50) -> dict:
    import yfinance as yf
    out = {}
    ys = [D.yahoo_symbol(s) for s in symbols]
    for k in range(0, len(ys), chunk):
        part = ys[k:k + chunk]
        print(f"  {interval}: {k + 1}-{k + len(part)} of {len(ys)}", flush=True)
        try:
            df = yf.download(part, period=period, interval=interval, progress=False,
                             auto_adjust=False, group_by="ticker", threads=True)
        except Exception as e:  # noqa: BLE001
            print(f"  download error: {e}")
            continue
        for y in part:
            try:
                one = df[y] if isinstance(df.columns, pd.MultiIndex) else df
                one = one[["Open", "High", "Low", "Close", "Volume"]].dropna()
            except KeyError:
                continue
            if not len(one):
                continue
            if interval.endswith("m") and one.index.tz is not None:
                one.index = one.index.tz_convert("Asia/Kolkata")
            out[y.removesuffix(".NS")] = one
    return out


def demo_data(seed=3, days=40):
    rng = np.random.default_rng(seed)
    idx, px, rows = [], 500.0, []
    for d in pd.bdate_range("2026-09-01", periods=days):
        for m in range(75):
            ts = pd.Timestamp(d.date()).tz_localize("Asia/Kolkata") + pd.Timedelta(hours=9, minutes=15 + 5 * m)
            o = px
            c = o * (1 + rng.normal(0, 0.003))
            h = max(o, c) * (1 + abs(rng.normal(0, 0.0015)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.0015)))
            rows.append((o, h, lo, c, rng.integers(5e4, 2e5)))
            idx.append(ts)
            px = c
    m5 = pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=["Open", "High", "Low", "Close", "Volume"])
    daily = m5.groupby(m5.index.date).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    daily.index = pd.to_datetime(daily.index)
    return {"DEMO": m5}, {"DEMO": daily}


# ----------------------------------------------------------------------------- backtest
def run_signals(m5: dict, daily: dict, keep: list[str], p: Params) -> list[dict]:
    sigs = []
    for n, sym in enumerate(keep):
        df5, dd = m5[sym], daily[sym]
        dd = dd[~dd.index.duplicated()]
        dates = pd.Index(dd.index.date)
        for day, g in df5.groupby(df5.index.date):
            if len(g) < 30:
                continue
            pos = dates.searchsorted(day) - 1     # previous trading day in daily data
            if pos < 0 or dates[pos] >= day:
                continue
            pdh, pdl = float(dd["High"].iloc[pos]), float(dd["Low"].iloc[pos])
            sig = find_signal(g, pdh, pdl, p)
            if sig:
                sig.update(symbol=sym, day=day, g=g)
                sigs.append(sig)
        if (n + 1) % 100 == 0:
            print(f"  scanned {n + 1}/{len(keep)} stocks, signals so far {len(sigs)}", flush=True)
    return sigs


def build_trades(sigs, rr, p, a, worst_case=True, with_costs=True):
    rows = []
    for s in sigs:
        res = manage(s["g"], s, rr, p, worst_case)
        if res is None:
            continue
        entry, exit_, reason, xt = res
        risk_ps = abs(entry - s["sl"])
        qty = int(a.capital * a.risk_pct / 100 // risk_ps)
        qty = min(qty, int(a.capital * a.leverage // entry))
        if qty < 1:
            continue
        gross = (exit_ - entry) * s["side"] * qty
        cost = trade_costs(entry, exit_, qty, s["side"], a.slippage / 100) if with_costs else 0.0
        rows.append(dict(
            symbol=s["symbol"], date=str(s["day"]), side="BUY" if s["side"] == 1 else "SELL",
            pattern=s["pattern"], level=round(s["level"], 2),
            entry_time=s["g"].index[s["i"] + 1].strftime("%H:%M"), exit_time=xt.strftime("%H:%M"),
            entry=round(entry, 2), stop=round(s["sl"], 2), exit=round(exit_, 2), reason=reason,
            risk_pct=round(risk_ps / entry * 100, 3), qty=qty, value=round(qty * entry),
            gross_pnl=round(gross, 2), costs=round(cost, 2), net_pnl=round(gross - cost, 2),
            net_R=round((gross - cost) / (qty * risk_ps), 3),
            entry_ts=s["g"].index[s["i"] + 1], exit_ts=xt))
    return pd.DataFrame(rows)


def portfolio(tr: pd.DataFrame, a) -> pd.DataFrame:
    """Take trades in time order with margin limit (capital x leverage). Fixed risk per trade."""
    if tr.empty:
        return tr
    tr = tr.sort_values(["entry_ts", "turnover_cr"], ascending=[True, False]).reset_index(drop=True)
    taken, open_ = [], []
    limit = a.capital * a.leverage
    for r in tr.itertuples():
        open_ = [x for x in open_ if x[0] > r.entry_ts]
        used = sum(x[1] for x in open_)
        if len(open_) >= a.max_positions or used + r.value > limit:
            continue
        open_.append((r.exit_ts, r.value))
        taken.append(r.Index)
    return tr.loc[taken]


def stats(tr: pd.DataFrame, capital: float) -> dict:
    if tr.empty:
        return dict(trades=0)
    wins = tr[tr.net_pnl > 0]
    daily = tr.groupby("date").net_pnl.sum().sort_index()
    eq = capital + daily.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    gp, gl = wins.net_pnl.sum(), -tr[tr.net_pnl <= 0].net_pnl.sum()
    return dict(
        trades=len(tr), win_rate=round(len(wins) / len(tr) * 100, 1),
        target_hit=round((tr.reason == "target").mean() * 100, 1),
        stop_hit=round((tr.reason == "stop").mean() * 100, 1),
        squareoff=round((tr.reason == "squareoff").mean() * 100, 1),
        avg_R=round(tr.net_R.mean(), 3),
        net_pnl=round(tr.net_pnl.sum()), costs=round(tr.costs.sum()),
        return_pct=round(tr.net_pnl.sum() / capital * 100, 1),
        profit_factor=round(gp / gl, 2) if gl > 0 else float("inf"),
        max_dd_pct=round(dd, 1), days=tr.date.nunique())


def md_table(rows: dict) -> str:
    keys = list(next(iter(rows.values())).keys())
    out = ["| | " + " | ".join(keys) + " |", "|---" * (len(keys) + 1) + "|"]
    for name, r in rows.items():
        out.append(f"| {name} | " + " | ".join(str(r.get(k, "")) for k in keys) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--list", default="data/nifty1000_symbols.csv")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--leverage", type=float, default=5.0)
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--slippage", type=float, default=0.03, help="% per side")
    ap.add_argument("--min-turnover", type=float, default=20, help="avg daily turnover, crore")
    ap.add_argument("--min-price", type=float, default=50)
    a = ap.parse_args()
    p = Params()

    if a.demo:
        m5, daily = demo_data()
    else:
        syms = a.symbols or pd.read_csv(a.list)["Symbol"].astype(str).tolist()
        print(f"Universe: {len(syms)} symbols")
        daily = download(syms, "1d", "1y")
        m5 = download(syms, "5m", "60d")
    print(f"Got data: daily {len(daily)}, 5-min {len(m5)}")

    # liquidity filter, measured on the 20 days BEFORE the 5-min test period starts
    keep, turnover = [], {}
    for sym, df5 in m5.items():
        dd = daily.get(sym)
        if dd is None or len(df5) < 300:
            continue
        if a.demo:
            keep.append(sym)
            turnover[sym] = 0.0
            continue
        before = dd[dd.index.date < df5.index[0].date()].tail(20)
        if len(before) < 10:
            continue
        to = float((before.Close * before.Volume).mean() / 1e7)
        turnover[sym] = to
        if to >= a.min_turnover and before.Close.iloc[-1] >= a.min_price:
            keep.append(sym)
    print(f"Liquid stocks kept: {len(keep)} (turnover >= {a.min_turnover} cr, price >= {a.min_price})")
    if not keep:
        sys.exit("No stocks to test")

    sigs = run_signals(m5, daily, keep, p)
    print(f"Signals: {len(sigs)}")
    first = min(m5[s].index[0] for s in keep).date()
    last = max(m5[s].index[-1] for s in keep).date()

    os.makedirs(OUT_DIR, exist_ok=True)
    summary, per_rr = {}, {}
    for rr in RRS:
        naive = build_trades(sigs, rr, p, a, worst_case=False, with_costs=False)
        tr = build_trades(sigs, rr, p, a)
        if tr.empty:
            continue
        tr["turnover_cr"] = tr.symbol.map(turnover).round(1)
        naive["turnover_cr"] = naive.symbol.map(turnover).round(1)
        port = portfolio(tr, a)
        summary[f"1:{rr:g} naive (no costs, target first)"] = stats(naive, a.capital)
        summary[f"1:{rr:g} all signals (with costs)"] = stats(tr, a.capital)
        summary[f"1:{rr:g} portfolio (max {a.max_positions} open, with costs)"] = stats(port, a.capital)
        per_rr[rr] = (tr, port)
        tr.drop(columns=["entry_ts", "exit_ts"]).to_csv(f"{OUT_DIR}/trades_rr{rr:g}.csv", index=False)
        port.drop(columns=["entry_ts", "exit_ts"]).to_csv(f"{OUT_DIR}/portfolio_rr{rr:g}.csv", index=False)

    lines = [
        "# Break & Bounce backtest (PDH/PDL breakout + retest)", "",
        f"- Period: {first} to {last} (Yahoo 5-min data, last ~60 days only)",
        f"- Universe: Nifty 1000 list -> {len(keep)} liquid stocks "
        f"(avg turnover >= Rs {a.min_turnover:g} cr, price >= Rs {a.min_price:g})",
        f"- Capital Rs {a.capital:,.0f}, risk {a.risk_pct:g}% per trade, MIS leverage {a.leverage:g}x, "
        f"slippage {a.slippage:g}% per side", f"- Signals found: {len(sigs)}", "",
        "## Summary", "", md_table(summary) if summary else "No trades", "",
    ]
    for rr, (tr, port) in per_rr.items():
        lines += [f"## 1:{rr:g} details (all signals, with costs)", ""]
        by = {}
        for name, g in [("BUY", tr[tr.side == "BUY"]), ("SELL", tr[tr.side == "SELL"])] + \
                [(f"pattern {k}", g) for k, g in tr.groupby("pattern")]:
            by[name] = stats(g, a.capital)
        q = pd.qcut(tr.risk_pct, 3, labels=["tight stop", "medium stop", "wide stop"], duplicates="drop")
        for k, g in tr.groupby(q, observed=True):
            by[f"{k} ({g.risk_pct.min():.2f}-{g.risk_pct.max():.2f}%)"] = stats(g, a.capital)
        lines += [md_table(by), ""]
    with open(f"{OUT_DIR}/report.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5))
        for rr, (tr, port) in per_rr.items():
            for name, t_ in (("portfolio", port), ("all signals", tr)):
                eq = a.capital + t_.groupby("date").net_pnl.sum().sort_index().cumsum()
                ax.plot(pd.to_datetime(eq.index), eq.values, label=f"1:{rr:g} {name}")
        ax.axhline(a.capital, color="grey", lw=0.8, ls="--")
        ax.set_title("Break & Bounce - equity (with costs)")
        ax.set_ylabel("Rs")
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(f"{OUT_DIR}/equity.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print(f"chart error: {e}")


if __name__ == "__main__":
    main()
