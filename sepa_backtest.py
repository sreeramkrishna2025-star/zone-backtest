"""
Full Minervini SEPA backtest (rules in MINERVINI_RULES.md). No look-ahead: every decision uses data
known at that day's close.

SETUPS (all need Stage 2 = Trend Template + RS, except CHEAT which needs a rising 200 SMA)
  VCP    base 3-65 weeks, 2-6 contractions each <= 0.9x the previous, first 8-40% deep, last <= 10%,
         volume dries up in the last contraction (+ a very-low-volume day in the last 5),
         pivot = high of the last contraction; breakout close above pivot on >= 1.4x volume
  FLAT   4-7 week base, 8-15% deep, close above the base high on >= 1.4x volume
  PP     Power Play: +100% within 40 days, then 12-30 days sideways, pullback <= 25%,
         close above the flag high on >= 1.4x volume
  CHEAT  3C: prior run 25%+ ; cup 15-40% deep; price back in the lower 2/3 of the cup;
         5-15 day pause, range <= 10%; close above pause high on >= 1x volume; above rising 200 SMA
ENTRY  at the breakout day's close if <= 5% above the pivot (never chase)
STOP   low of the last contraction / pause / flag; skipped if more than MAX_STOP (10%) away
BASE COUNT: bases since the stock last closed below its 200 SMA; --max-base skips late-stage bases

FINAL RULES (kept after testing every rule on/off, 2026-11-13)
  trailing: close < 50 SMA -> sell next open (from day 1) ; break-even at +3R
  20 SMA heavy-volume break -> stop to that day's low ; largest daily drop since entry on heavy
  volume (after 10 days) -> sell ; climax (+25% from entry and +25% in 15 days) -> stop under each
  day's low ; time stop: no +1R within 20 days -> sell
  REMOVED (made results worse): selling half at 1R/2R, market filter, tough-market rules,
  progressive exposure, base-count limit, VCP-only / cheat-only
PORTFOLIO max --max-pos stocks (6), risk --risk % equity per trade (1.25), max 25% equity per stock,
        costs --cost % round trip (brokerage + STT + slippage, default 0.25)
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from zone_agent import data as D
from minervini_backtest import rs_ratings

MAX_STOP = 0.10
VOL_MULT = 1.4
MAX_CHASE = 0.05
BE_R = 3
TIME_DAYS = 20


# ------------------------------------------------------------------ indicators
def prep(df: pd.DataFrame) -> dict:
    c, h, l, v = (df[k].to_numpy(dtype=float) for k in ("Close", "High", "Low", "Volume"))
    o = df["Open"].to_numpy(dtype=float)
    C = df["Close"]
    s20, s50 = C.rolling(20).mean(), C.rolling(50).mean()
    s150, s200 = C.rolling(150).mean(), C.rolling(200).mean()
    hi52, lo52 = df["High"].rolling(252).max(), df["Low"].rolling(252).min()
    vol50 = df["Volume"].shift(1).rolling(50).mean()
    tt = ((C > s150) & (C > s200) & (s150 > s200) & (s200 > s200.shift(22)) & (s50 > s150)
          & (s50 > s200) & (C > s50) & (C >= 1.3 * lo52) & (C >= 0.75 * hi52))
    # base count since the last close below the 200 SMA
    n = len(c)
    bc = np.zeros(n, dtype=int)
    s200a = s200.to_numpy()
    count, run_hi, in_base = 0, -np.inf, False
    for i in range(n):
        if np.isnan(s200a[i]) or c[i] < s200a[i]:
            count, run_hi, in_base = 0, h[i], False
        else:
            if c[i] > run_hi:
                if in_base:
                    count += 1
                    in_base = False
                run_hi = max(run_hi, h[i])
            elif c[i] < run_hi * 0.88:
                in_base = True
            run_hi = max(run_hi, h[i]) if not in_base else run_hi
        bc[i] = count
    val50 = (df["Close"] * df["Volume"]).rolling(50).mean().to_numpy()
    gaps = pd.Series(df.index.to_series().diff().dt.days.to_numpy(), index=df.index).rolling(260, min_periods=1).max()
    return {"val50": val50, "gap": gaps.to_numpy(), "o": o, "h": h, "l": l, "c": c, "v": v, "s20": s20.to_numpy(), "s50": s50.to_numpy(),
            "s200": s200a, "s150": s150.to_numpy(), "s200_up": (s200 > s200.shift(22)).to_numpy(), "vol50": vol50.to_numpy(),
            "tt": tt.to_numpy(), "basecount": bc, "hi52": hi52.to_numpy()}


def contractions(H, L, s, e, pct=0.04):
    """Swing high -> swing low pairs from index s (a high) to e. Returns [(hi_i, hi, lo_i, lo)]."""
    out = []
    cur_hi, cur_hi_i = H[s], s
    cand_lo, cand_lo_i = np.inf, None
    seeking_low = True
    for i in range(s + 1, e + 1):
        if seeking_low:
            if H[i] > cur_hi and cand_lo == np.inf:
                cur_hi, cur_hi_i = H[i], i
                continue
            if L[i] < cand_lo:
                cand_lo, cand_lo_i = L[i], i
            if cand_lo < np.inf and H[i] >= cand_lo * (1 + pct) and cand_lo <= cur_hi * (1 - pct):
                out.append((cur_hi_i, cur_hi, cand_lo_i, cand_lo))
                seeking_low = False
                cur_hi, cur_hi_i = H[i], i
            elif H[i] > cur_hi:                       # new high before a real pullback
                cur_hi, cur_hi_i = H[i], i
                cand_lo, cand_lo_i = np.inf, None
        else:
            if H[i] > cur_hi:
                cur_hi, cur_hi_i = H[i], i
            if L[i] <= cur_hi * (1 - pct):
                seeking_low = True
                cand_lo, cand_lo_i = L[i], i
    # last (current) contraction, not yet confirmed by a rally
    if seeking_low and cand_lo < np.inf:
        out.append((cur_hi_i, cur_hi, cand_lo_i, cand_lo))
    elif not seeking_low and e - cur_hi_i >= 3:
        lo_i = cur_hi_i + 1 + int(np.argmin(L[cur_hi_i + 1:e + 1]))
        out.append((cur_hi_i, cur_hi, lo_i, L[lo_i]))
    merged = []
    for hi_i, hi, lo_i, lo in out:
        while merged and lo <= merged[-1][3]:
            ph_i, ph, _, _ = merged.pop()
            hi_i, hi = ph_i, ph
        merged.append((hi_i, hi, lo_i, lo))
    return merged


FUNNEL: dict = {}


def _f(k):
    FUNNEL[k] = FUNNEL.get(k, 0) + 1


# ------------------------------------------------------------------ setups
def find_setup(P, t, rs_t, cfg):
    """Return (type, pivot, stop) for a breakout at the close of day t, or None."""
    h, l, c, v, vol50 = P["h"], P["l"], P["c"], P["v"], P["vol50"]
    if t < 260 or np.isnan(vol50[t]) or vol50[t] <= 0:
        return None
    vx = v[t] / vol50[t]
    stage2 = bool(P["tt"][t]) and rs_t >= cfg["rs_min"]
    want = cfg["setups"]
    # base start: last 52-week high before t (and nothing higher since)
    look = h[max(0, t - 325):t]
    s = max(0, t - 325) + int(np.argmax(look))
    blen = t - s
    if stage2 and vx >= VOL_MULT:
        # ---- VCP
        if "VCP" in want and 15 <= blen <= 325:
            _f("vcp 1 stage2+volume+base length")
            cs = contractions(h, l, s, t - 1)
            if 2 <= len(cs) <= 6:
                _f("vcp 2 contractions 2-6")
                d = [(hi - lo) / hi for _, hi, _, lo in cs]
                if 0.08 <= d[0] <= 0.40 and d[-1] <= 0.10:
                    _f("vcp 3 first 8-40% last <=10%")
                    if all(d[k] <= d[k - 1] * 0.9 for k in range(1, len(d))):
                        _f("vcp 4 each smaller")
                        hi_i, piv, lo_i, lo = cs[-1]
                        if v[hi_i:t].mean() < vol50[t] and v[t - 5:t].min() < 0.75 * vol50[t]:
                            _f("vcp 5 volume dry-up")
                            fresh = h[lo_i + 1:t].max() <= piv if lo_i + 1 < t else True
                            if fresh and c[t] > piv:
                                _f("vcp 6 breakout")
                                return "VCP", piv, lo
        # ---- flat base
        if "FLAT" in want and 20 <= blen <= 35:
            bl = l[s:t].min()
            depth = (h[s] - bl) / h[s]
            if 0.08 <= depth <= 0.15 and c[t] > h[s]:
                return "FLAT", h[s], bl
    # ---- Power Play (technical only)
    if "PP" in want and vx >= VOL_MULT and bool(P["s200_up"][t]):
        for fl in range(12, 31):
            top_i = t - fl
            if top_i - 40 < 0:
                break
            if h[top_i] < h[top_i:t].max():
                continue
            low40 = l[top_i - 40:top_i].min()
            if h[top_i] >= 2.0 * low40:
                flag_lo = l[top_i:t].min()
                if (h[top_i] - flag_lo) / h[top_i] <= 0.25 and c[t] > h[top_i]:
                    return "PP", h[top_i], flag_lo
                break
    # ---- Cheat / 3C
    s200 = P["s200"]
    if ("CHEAT" in want and vx >= 1.2 and rs_t >= cfg["rs_min"] and c[t] > s200[t]
            and P["s150"][t] > s200[t] and s200[t] > s200[t - 66]):          # 200 SMA up 3+ months
        if 25 <= blen <= 315:
            _f("cheat 1 trend+volume+base length")
            top = h[s]
            bl_i = s + int(np.argmin(l[s:t]))
            bl = l[bl_i]
            depth = (top - bl) / top
            prior_lo = l[max(0, s - 750):s].min() if s > 0 else np.inf
            if 0.15 <= depth <= 0.40 and top >= 1.25 * prior_lo:
                _f("cheat 2 cup 15-40% after 25%+ run")
                for pl in range(5, 16):
                    p0 = t - pl
                    if p0 <= bl_i + 3:
                        break
                    phi, plo = h[p0:t].max(), l[p0:t].min()
                    if ((phi - plo) / phi <= 0.10 and plo >= bl * 1.03 and phi <= bl + 0.67 * (top - bl)
                            and v[p0:t].mean() < vol50[t] and c[t] > phi and c[t - 1] <= phi):
                        _f("cheat 3 pause in lower 2/3 + dry volume + breakout")
                        return "CHEAT", phi, plo
    return None


MIN_VALUE_CR = 0.5     # minimum average daily traded value (Rs crore)
MAX_GAP_DAYS = 10      # skip stocks with a hole in the data longer than this (last year)


def tradable(P, t):
    """Liquidity >= MIN_VALUE_CR a day (50-day average) and no missing-data gap > MAX_GAP_DAYS in a year."""
    val = P["val50"][t]
    return not np.isnan(val) and val >= MIN_VALUE_CR * 1e7 and P["gap"][t] <= MAX_GAP_DAYS


# ------------------------------------------------------------------ trade management
def manage(P, t, entry, stop0):
    """Simulate exits with the final rules. Returns list of (index, fraction, price, reason)."""
    o, h, l, c, v = P["o"], P["h"], P["l"], P["c"], P["v"]
    s20, s50, vol50 = P["s20"], P["s50"], P["vol50"]
    n = len(c)
    risk = entry - stop0
    stop, climax, maxh, worst_ret, sell_next = stop0, False, entry, 0.0, None
    for j in range(t + 1, n):
        if sell_next is not None:
            return [(j, 1.0, o[j], sell_next)]
        if o[j] <= stop:
            return [(j, 1.0, o[j], "stop" if stop == stop0 else "raised stop")]
        if l[j] <= stop:
            return [(j, 1.0, stop, "stop" if stop == stop0 else "raised stop")]
        maxh = max(maxh, h[j])
        if h[j] >= entry + BE_R * risk:
            stop = max(stop, entry)                               # break-even at +3R
        vol_heavy = not np.isnan(vol50[j]) and v[j] > vol50[j]
        ret = c[j] / c[j - 1] - 1
        if c[j] >= entry * 1.25 and j >= 15 and c[j] / c[j - 15] >= 1.25:
            climax = True                                         # climax run: tight stop
        if climax:
            stop = max(stop, l[j])
        if c[j] < s20[j] and ret < 0 and vol_heavy:
            stop = max(stop, l[j])                                # heavy-volume 20 SMA break
        if j - t >= 10 and ret <= -0.03 and ret < worst_ret and vol_heavy:
            sell_next = "largest decline on volume"
        worst_ret = min(worst_ret, ret)
        if c[j] < s50[j]:
            sell_next = sell_next or "close < 50 SMA"
        if j - t >= TIME_DAYS and maxh < entry + risk:
            sell_next = sell_next or f"time stop {TIME_DAYS}d"
    return [(n - 1, 1.0, c[n - 1], "open")]


# ------------------------------------------------------------------ main
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
    ap.add_argument("--setups", default="VCP,FLAT,PP,CHEAT")
    ap.add_argument("--rs-min", type=float, default=70)
    ap.add_argument("--fund", default="none", help="fundamental filter: none|has|eps25|eps25_sales25|eps25_accel|code33|eps20_or_turn|eps_pos_sales15")
    ap.add_argument("--start", default="2010-01-01", help="portfolio start date")
    ap.add_argument("--min-value", type=float, default=0.5, help="min avg daily traded value, Rs crore")
    ap.add_argument("--max-gap", type=int, default=10, help="max missing-data gap in days (last year)")
    ap.add_argument("--max-pos", type=int, default=6)
    ap.add_argument("--risk", type=float, default=1.25)
    ap.add_argument("--cost", type=float, default=0.25, help="round-trip cost %% per trade")
    a = ap.parse_args(argv)
    cfg = {"setups": set(a.setups.split(",")), "rs_min": a.rs_min}
    print("Options:", {"setups": sorted(cfg["setups"]), "rs_min": a.rs_min, "max_pos": a.max_pos,
                       "risk": a.risk, "cost": a.cost, "min_value_cr": a.min_value, "max_gap": a.max_gap})
    global MIN_VALUE_CR, MAX_GAP_DAYS
    MIN_VALUE_CR, MAX_GAP_DAYS = a.min_value, a.max_gap
    if a.watchlist or a.all_watchlists:
        from run_agent import pick_watchlists
        targets = [y for y, _ in pick_watchlists(a.watchlist, a.all_watchlists)]
    else:
        targets = [D.yahoo_symbol(s) for s in (a.symbols or [])]
    idx_words = ("NIFTY", "SENSEX", "CNX", "INDIA_VIX")
    targets = list(dict.fromkeys(t for t in targets if not t.startswith("^")
                                 and not t.upper().startswith(idx_words)))   # stocks only, no indices
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

    trades = []
    for y, df in data.items():
        sym = y.replace(".NS", "").replace(".BO", " (BSE)")
        try:
            P = prep(df)
            rsv = rs[y].reindex(df.index).to_numpy(dtype=float)
            c, h, vol50 = P["c"], P["h"], P["vol50"]
            # quick pre-filter: close at a 10-day high on decent volume
            hh10 = pd.Series(h).shift(1).rolling(10).max().to_numpy()
            cand = np.flatnonzero((c > hh10) & (P["v"] >= 1.0 * np.nan_to_num(vol50, nan=np.inf)))
            busy = -1
            for t in cand:
                if t <= busy or t + 1 >= len(c):
                    continue
                if not tradable(P, t):
                    continue
                r = rsv[t] if not np.isnan(rsv[t]) else 0
                st = find_setup(P, t, r, cfg)
                if st is None:
                    continue
                kind, piv, stop = st
                bnum = P["basecount"][t] + 1
                entry = c[t]
                if entry > piv * (1 + MAX_CHASE) or stop >= entry:
                    continue
                if (entry - stop) / entry > MAX_STOP:
                    continue
                ex = manage(P, t, entry, stop)
                busy = ex[-1][0]
                pct = sum(f * (px / entry - 1) for _, f, px, _ in ex) * 100 - a.cost
                trades.append({"symbol": sym, "y": y, "setup": kind, "base_no": int(bnum),
                               "date": str(df.index[t].date()), "entry": round(entry, 2),
                               "stop": round(stop, 2), "risk_pct": round((entry - stop) / entry * 100, 1),
                               "rs": round(r, 0),
                               "exit_date": str(df.index[ex[-1][0]].date()),
                               "exits": "; ".join(f"{df.index[j].date()} {f:.2f}@{px:.2f} {why}" for j, f, px, why in ex),
                               "days": int(ex[-1][0] - t), "pct": round(pct, 2),
                               "R": round(pct / 100 * entry / (entry - stop), 2),
                               "exl": [(df.index[j], f, px) for j, f, px, _ in ex]})
        except Exception as e:  # noqa: BLE001
            print(f"{sym}: error {str(e)[:120]}")
    if not trades:
        print("no trades")
        return 0
    T = pd.DataFrame(trades).sort_values("date")
    if a.fund != "none":
        from zone_agent import fundamentals as FU
        FD = FU.load()
        print(f"fundamentals loaded for {len(FD)} stocks")
        feats = [FU.features(FD, r.y.replace(".NS", ""), r.date) for r in T.itertuples()]
        for k in ("has_fund", "eps_g", "sales_g", "eps_accel", "code33", "turnaround"):
            T[k] = [f[k] for f in feats]
        keep = [FU.passes(f, a.fund) for f in feats]
        T = T[keep]
        print(f"fundamental filter '{a.fund}': {len(T)} trades kept")
    T = T[T.pct < 300]                                # drop a few bad old Yahoo prices

    def stat(x, lab):
        if x.empty:
            print(f"{lab}: none")
            return
        gp, gl = x.pct[x.pct > 0].sum(), -x.pct[x.pct < 0].sum()
        print(f"{lab}: trades {len(x)} | win {(x.pct > 0).mean() * 100:.1f}% | avg {x.pct.mean():+.2f}% "
              f"({x.R.mean():+.2f}R) | avg win {x.pct[x.pct > 0].mean():+.1f}% | avg loss {x.pct[x.pct <= 0].mean():+.1f}% "
              f"| PF {gp / max(gl, 1e-9):.2f} | median days {x.days.median():.0f}")

    print("\nFunnel:", FUNNEL)
    print()
    stat(T, "TRADES ALL  ")
    stat(T[T.date >= "2015"], "TRADES 2015+")
    stat(T[(T.date >= "2015") & (T.date < "2024")], "TRADES 15-23")
    stat(T[T.date >= "2024"], "TRADES 2024+")
    for k, g in T.groupby("setup"):
        stat(g[g.date >= "2015"], f"  setup {k:5s} 2015+")
    for k, g in T.groupby("base_no"):
        if k <= 5:
            stat(g[g.date >= "2015"], f"  base #{k} 2015+")

    # ---------------- portfolio simulation (from 2010)
    start = pd.Timestamp(a.start)
    dates = closes.index[closes.index >= start]
    sig = {}
    for r in T[T.date >= a.start].itertuples():
        sig.setdefault(pd.Timestamp(r.date), []).append(r)
    cl = closes.ffill()
    cash, equity, pos, hist = 100.0, 100.0, {}, []
    taken = []
    for d in dates:
        # exits
        for key in list(pos):
            p = pos[key]
            for (xd, f, px) in list(p["ex"]):
                if xd == d:
                    q = p["qty0"] * f
                    cash += q * px * (1 - a.cost / 200)
                    p["qty"] -= q
                    p["pnl"] += q * (px - p["entry"])
                    p["ex"].remove((xd, f, px))
            if p["qty"] <= 1e-9 or not p["ex"]:
                del pos[key]
        # mark to market
        mv = sum(p["qty"] * cl.at[d, p["y"]] for p in pos.values() if not np.isnan(cl.at[d, p["y"]]))
        equity = cash + mv
        # entries (best RS first)
        for r in sorted(sig.get(d, []), key=lambda r: -r.rs):
            if len(pos) >= a.max_pos:
                break
            risk_amt = equity * a.risk / 100
            qty = risk_amt / (r.entry - r.stop)
            qty = min(qty, equity * 0.25 / r.entry, cash / r.entry)
            if qty * r.entry < equity * 0.02:
                continue
            cash -= qty * r.entry * (1 + a.cost / 200)
            pos[(r.symbol, r.date)] = {"y": r.y, "entry": r.entry, "qty": qty, "qty0": qty,
                                       "ex": list(r.exl), "pnl": 0.0}
            taken.append(r)
        hist.append((d, equity))
    eq = pd.Series(dict(hist))
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    print(f"\nPORTFOLIO {a.start[:4]}-now: start 100 -> {eq.iloc[-1]:.0f} | CAGR {cagr * 100:.1f}% | max drawdown {dd * 100:.1f}% "
          f"| trades taken {len(taken)}")
    yr = eq.resample("YE").last()
    yret = yr.pct_change()
    yret.iloc[0] = yr.iloc[0] / 100 - 1
    for a0, b0 in ((("2010", "2017"), ("2018", "2026")) if a.start < "2015" else (("2020", "2022"), ("2023", "2026"))):
        e = eq[a0:b0]
        g = (e.iloc[-1] / e.iloc[0]) ** (365.25 / max((e.index[-1] - e.index[0]).days, 1)) - 1
        print(f"Portfolio CAGR {a0}-{b0}: {g * 100:.1f}% | max drawdown {(e / e.cummax() - 1).min() * 100:.1f}%")
    print("Portfolio by year: " + "  ".join(f"{i.year}:{v * 100:+.0f}%" for i, v in yret.items()))
    try:
        nifty = D.get_candles("^NSEI", "1d")["Close"]
        nifty = nifty[nifty.index >= start]
        ny = (nifty.iloc[-1] / nifty.iloc[0]) ** (1 / ((nifty.index[-1] - nifty.index[0]).days / 365.25)) - 1
        print(f"NIFTY 50 buy & hold CAGR same period: {ny * 100:.1f}%")
    except Exception:  # noqa: BLE001
        pass
    print("\nTRADES CSV")
    print(T.drop(columns=["exl", "y"]).to_csv(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
