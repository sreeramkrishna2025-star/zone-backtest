"""
Backtest of the user's exact setup (no look-ahead):

  EXECUTION  Daily zone, Trade Score 7/7 (fresh 3 + strength 2 + base 2), max 2 base
             candles, closing concept, no garbage area, price left the base by the
             daily minimum move.
  TREND      Weekly trend (50 SMA clock AND structure HH/HL) up -> BUY, down -> SELL.
  LOCATION   The daily zone sits inside a Monthly zone of the same type, or within
             LOCATION_PCT (2%) of it (above a demand / below a supply).
  TARGET     Proximal line of the nearest Weekly opposite zone.  Trade only if it is
             at least MIN_RR (2:1) away.  No opposite weekly zone (all-time high /
             low) -> no target, trailing stop loss.
  STOP       Distal line of the daily zone +/- SL_BUFFER_PCT.
  TRAILING   ATH/ATL trades: stop moves below each new fresh daily demand zone
             (above each new daily supply zone for SELL) formed after entry.
  ENTRY      Type 1 (score 7): limit order at proximal, first touch only.

Usage:
  python backtest_setup.py --watchlist "Red list"
  python backtest_setup.py --symbols ABREL CESC
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

from zone_agent import config as C
from zone_agent import data as D
from zone_agent.zones import find_zones
from zone_agent.data import resample_ohlc
from zone_agent.system import zone_quality, move_sizes, structure_trend
from zone_agent.system import trend_detail as _trend_both


def trend_detail(f):
    """--trend-mode both (SMA and structure agree) or structure (swing HH/HL only)."""
    if CFG.get("trend_mode") == "structure":
        t = structure_trend(f)
        return t, f"structure {t}"
    return _trend_both(f)

LOCATION_PCT = 2.0
MIN_RR = 2.0
SL_BUFFER_PCT = 0.5
ENTRY_BUFFER_PCT = 0.05
# time-frame sets: execution / trend / location / target
TF_SETS = {
    "daily":  {"exec": None,    "trend": "W-FRI", "loc": "MS", "tgt": "W-FRI", "move": 5,  "label": "Daily exec, Weekly trend, Monthly location, Weekly target"},
    "weekly": {"exec": "W-FRI", "trend": "MS",    "loc": "QS", "tgt": "MS",    "move": 10, "label": "Weekly exec, Monthly trend, Quarterly location, Monthly target"},
}
CFG = {"tf": "daily", "min_score": 7, "garbage": True}
C_HTF_MAX_BASE = 5
DAILY_MIN_MOVE = C.MIN_MOVE_PCT.get("Daily", 5)
MAX_HOLD_DAYS = 750             # safety: close a trade after ~3 years


def daily_candidates(d: pd.DataFrame, zones):
    """Daily zones with Trade Score 7 and clean structure.  Returns
    (zone, active_from_index) - the zone may be traded only from that index on
    (after the 2nd exciting candle / departure is known)."""
    out = []
    H, L = d["High"].to_numpy(), d["Low"].to_numpy()
    for z in zones:
        if z.base_count > C.MAX_BASE_CANDLES:
            continue
        strength = 2 if (z.gap_out or z.two_exciting) else 1
        if 3 + strength + 2 < CFG["min_score"]:  # fresh 3 + strength + base(<=2 candles) 2
            continue
        if CFG["garbage"]:
            ok, _ = zone_quality(z, d)
            if not ok:
                continue
        mi, mo = move_sizes(z, d)
        mv = TF_SETS[CFG["tf"]]["move"]
        if mi < mv or mo < mv:
            continue
        # zone is known only after the move out happened
        act = None
        for k in range(z.legout_idx + 1, min(len(d), z.base_end + 1 + C.MOVE_LOOKBACK + 1)):
            moved = (H[k] - z.proximal) / z.proximal * 100 if z.kind == "demand" \
                else (z.proximal - L[k]) / z.proximal * 100
            if moved >= mv:
                act = k + 1
                break
        if act is None:
            continue
        act = max(act, z.legout_idx + 2)        # 2nd exciting candle must be closed
        out.append((z, act))
    return out


def first_touch(z, d, start):
    H, L = d["High"].to_numpy(), d["Low"].to_numpy()
    for k in range(start, len(d)):
        if z.kind == "demand" and L[k] <= z.proximal * (1 + ENTRY_BUFFER_PCT / 100):
            return k
        if z.kind == "supply" and H[k] >= z.proximal * (1 - ENTRY_BUFFER_PCT / 100):
            return k
    return None


def htf_zones_asof(daily_upto: pd.DataFrame, rule: str):
    f = resample_ohlc(daily_upto, rule)
    if len(f) < 8:
        return [], f
    zones, fd = find_zones(f, max_base=C_HTF_MAX_BASE)    # big boxes may have up to 5 base candles
    zones = [z for z in zones if not z.breached]
    if CFG.get("box_proof"):
        # the box must have PROVED itself: price left it by >= 10% (achievement)
        zones = [z for z in zones if move_sizes(z, fd)[1] >= CFG["box_proof"]]
    return zones, f


def location_ok(z, mzones, tag="M"):
    lo, hi = sorted((z.proximal, z.distal))
    for m in mzones:
        if m.kind != z.kind:
            continue
        mlo, mhi = sorted((m.proximal, m.distal))
        if z.kind == "demand":
            # daily zone inside monthly demand, or its bottom within 2% above it
            if lo <= mhi * (1 + LOCATION_PCT / 100) and hi >= mlo:
                return True, f"{tag}-DZ {mlo:.0f}-{mhi:.0f}", m
        else:
            if hi >= mlo * (1 - LOCATION_PCT / 100) and lo <= mhi:
                return True, f"{tag}-SZ {mlo:.0f}-{mhi:.0f}", m
    return False, "", None


def box_info(m, upto, rule):
    """Monthly box quality: garbage overlap before its legin, and where price sits on
    the 36-month curve (0 = at the low, 100 = at the high)."""
    _, fd = find_zones(resample_ohlc(upto, rule), max_base=C_HTF_MAX_BASE)
    H, L = fd["High"].to_numpy(), fd["Low"].to_numpy()
    lo, hi = sorted((m.proximal, m.distal))
    st = max(0, m.legin_idx - 10)
    overlap = sum(1 for k in range(st, m.legin_idx) if L[k] <= hi and H[k] >= lo)
    w = fd.iloc[-36:]
    return overlap, float(w["Low"].min()), float(w["High"].max())


def weekly_target(side, entry, wzones):
    if side == "BUY":
        tops = [w.proximal for w in wzones if w.kind == "supply" and w.proximal > entry]
        return min(tops) if tops else None
    bots = [w.proximal for w in wzones if w.kind == "demand" and w.proximal < entry]
    return max(bots) if bots else None


def simulate(side, d, k, entry, stop, target, trail_zones):
    """Walk forward from the entry day.  Returns (exit_idx, exit_price, reason)."""
    O, H, L = d["Open"].to_numpy(), d["High"].to_numpy(), d["Low"].to_numpy()
    sl = stop
    for j in range(k, min(len(d), k + MAX_HOLD_DAYS)):
        if target is None:                       # trailing: raise stop under new zones
            for tz, known in trail_zones:
                if known <= j:
                    new = tz.distal * (1 - SL_BUFFER_PCT / 100) if side == "BUY" \
                        else tz.distal * (1 + SL_BUFFER_PCT / 100)
                    if side == "BUY" and new > sl and new < L[j - 1 if j > k else j]:
                        sl = new
                    if side == "SELL" and new < sl and new > H[j - 1 if j > k else j]:
                        sl = new
        if side == "BUY":
            if L[j] <= sl:                       # stop first (conservative)
                px = min(sl, O[j]) if j > k else sl
                return j, px, "SL" if sl <= stop else "Trail SL"
            if target is not None and H[j] >= target:
                return j, max(target, O[j]) if j > k else target, "Target"
        else:
            if H[j] >= sl:
                px = max(sl, O[j]) if j > k else sl
                return j, px, "SL" if sl >= stop else "Trail SL"
            if target is not None and L[j] <= target:
                return j, min(target, O[j]) if j > k else target, "Target"
    j = min(len(d), k + MAX_HOLD_DAYS) - 1
    return j, float(d["Close"].iloc[j]), "Open" if j == len(d) - 1 else "Time"


FUNNEL: dict = {}
OPTS = {"buy_only": False, "entry": "limit"}


def confirm_entry(z, d, k, mode):
    """PDF entry types for the first visit that starts on day k.
    type2: a candle CLOSES inside the zone and the NEXT candle OPENS inside the zone
           -> buy at that open.
    type3: a candle CLOSES inside the zone and a later candle CLOSES back outside on
           the trade side (reversal) -> buy at that close.
    Returns (fill_price, first_day_to_simulate, entry_day) or None."""
    O, H, L, Cl = (d[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    lo, hi = sorted((z.proximal, z.distal))
    demand = z.kind == "demand"
    n = len(d)

    def inside(x):
        return lo <= x <= hi

    def broken(x):
        return x < lo if demand else x > hi

    def left_ok(x):
        return x > hi if demand else x < lo

    c = None
    for j in range(k, min(n, k + 10)):
        if broken(Cl[j]):
            return None
        if inside(Cl[j]):
            c = j
            break
        if left_ok(Cl[j]):
            return None                   # only a wick touch, no close in zone -> no trade
    if c is None:
        return None
    if mode == "type2":
        if c + 1 < n and inside(O[c + 1]):
            return float(O[c + 1]), c + 1, c + 1
        return None
    for j in range(c + 1, min(n, c + 10)):  # type3: wait for the reversal close
        if broken(Cl[j]):
            return None
        if left_ok(Cl[j]):
            return float(Cl[j]), j + 1, j
    return None


def _f(key):
    FUNNEL[key] = FUNNEL.get(key, 0) + 1


def backtest_symbol(sym: str, daily: pd.DataFrame) -> list[dict]:
    T = TF_SETS[CFG["tf"]]
    if T["exec"]:
        daily = resample_ohlc(daily, T["exec"])     # execution frame (e.g. weekly)
    d_zones, d = find_zones(daily)
    cands = daily_candidates(d, d_zones)
    for _ in cands:
        _f("1 daily 7/7 zones")
    trades, busy_until = [], -1
    for z, act in sorted(cands, key=lambda t: t[1]):
        k = first_touch(z, d, z.legout_idx + 1)
        if k is None or k < act or k <= busy_until:
            continue                             # touched before it was confirmed -> not fresh
        _f("2 fresh touch")
        if CFG.get("latest_only"):
            # only the NEWEST confirmed zone of the same type counts (last best setup)
            if any(z2.kind == z.kind and z2.legout_idx > z.legout_idx and a2 <= k
                   for z2, a2 in cands):
                continue
            _f("2b latest zone")
        upto = daily.iloc[:k]                    # only data known before the touch day
        side = "BUY" if z.kind == "demand" else "SELL"
        if OPTS["buy_only"] and side == "SELL":
            continue
        w = resample_ohlc(upto, CFG.get("trend_tf") or T["trend"])
        tr, tr_why = trend_detail(w)
        if (side == "BUY" and tr != "up") or (side == "SELL" and tr != "down"):
            continue
        if CFG.get("trend2"):                    # bigger time frame structure must agree too
            t2 = structure_trend(resample_ohlc(upto, CFG["trend2"]))
            if (side == "BUY" and t2 != "up") or (side == "SELL" and t2 != "down"):
                continue
        _f("3 weekly trend ok")
        mz, _ = htf_zones_asof(upto, T["loc"])
        ok, loc, mbox = location_ok(z, mz)
        box_rule = T["loc"]
        if not ok and CFG.get("loc_q"):          # also accept a Quarterly box
            qz, _ = htf_zones_asof(upto, "QS")
            ok, loc, mbox = location_ok(z, qz, "Q")
            box_rule = "QS"
        if not ok:
            continue
        if CFG.get("max_box_overlap") is not None:
            if box_info(mbox, upto, box_rule)[0] > CFG["max_box_overlap"]:
                continue
        _f("4 monthly location ok")
        wz, _ = htf_zones_asof(upto, T["tgt"])
        entry = z.proximal * (1 + ENTRY_BUFFER_PCT / 100) if side == "BUY" \
            else z.proximal * (1 - ENTRY_BUFFER_PCT / 100)
        stop = z.distal * (1 - SL_BUFFER_PCT / 100) if side == "BUY" \
            else z.distal * (1 + SL_BUFFER_PCT / 100)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        target = weekly_target(side, entry, wz)
        if target is not None:
            rr = abs(target - entry) / risk
            if rr < MIN_RR:
                continue
            _f("5 weekly target >= 2R")
        else:
            rr = None
            _f("5 no weekly target (ATH/ATL trail)")
        if OPTS["entry"] == "limit":
            o = float(d["Open"].iloc[k])
            if (side == "BUY" and o <= stop) or (side == "SELL" and o >= stop):
                _f("x gapped through whole zone (skipped)")
                continue                         # opened beyond the stop: zone broken, no trade
            fill = min(entry, o) if side == "BUY" else max(entry, o)
            sim_from, e_day = k, k
        else:
            ce = confirm_entry(z, d, k, OPTS["entry"])
            if ce is None:
                _f("x no confirmation (skipped)")
                continue
            fill, sim_from, e_day = ce
            if (side == "BUY" and fill <= stop) or (side == "SELL" and fill >= stop):
                continue
            risk = abs(fill - stop)
            if target is not None:
                rr = abs(target - fill) / risk
                if rr < MIN_RR or (side == "BUY" and target <= fill) or (side == "SELL" and target >= fill):
                    _f("x confirmation used up the 2R room (skipped)")
                    continue
            _f("6 confirmed entry")
        if CFG.get("min_rr") and rr is not None and rr < CFG["min_rr"]:
            _f("x R:R below min_rr (skipped)")
            continue
        if CFG.get("min_rr") and rr is None and CFG.get("no_trail"):
            continue
        if sim_from >= len(d):
            continue
        # trailing zones: same-type daily zones formed after entry, known 2 days later
        trail = [(t, t.legout_idx + 2) for t in d_zones
                 if t.kind == z.kind and t.legout_idx > e_day] if target is None else []
        j, px, why = simulate(side, d, sim_from, fill, stop, target, trail)
        bx = box_info(mbox, upto, box_rule)
        r_mult = ((px - fill) if side == "BUY" else (fill - px)) / risk
        k = e_day
        trades.append({
            "symbol": sym, "side": side, "pattern": z.pattern,
            "zone_low": round(min(z.proximal, z.distal), 2), "zone_high": round(max(z.proximal, z.distal), 2),
            "zone_date": str(d.index[z.legout_idx])[:10], "entry_date": str(d.index[k])[:10],
            "entry": round(fill, 2), "stop": round(stop, 2),
            "target": round(target, 2) if target else "trail (no weekly zone)",
            "planned_rr": round(rr, 2) if rr else None,
            "exit_date": str(d.index[j])[:10], "exit": round(px, 2), "result": why,
            "R": round(r_mult, 2), "days": int(j - k),
            "weekly_trend": tr_why, "monthly_location": loc,
            "box_overlap": bx[0],
            "curve_pos": round((fill - bx[1]) / max(bx[2] - bx[1], 1e-9) * 100, 1),
        })
        busy_until = j
    return trades


def explain(sym: str, daily: pd.DataFrame, start: str, end: str):
    """Print every daily demand/supply zone formed between start and end and why
    it was (not) traded."""
    zones, d = find_zones(daily)
    print(f"\n=== EXPLAIN {sym} {start} .. {end}")
    for z in zones:
        dt = str(d.index[z.legout_idx])[:10]
        if not (start <= dt <= end):
            continue
        lo, hi = sorted((z.proximal, z.distal))
        strength = 2 if (z.gap_out or z.two_exciting) else 1
        score = 3 + strength + (2 if z.base_count <= 3 else 1)
        ok, why = zone_quality(z, d)
        mi, mo = move_sizes(z, d)
        k = first_touch(z, d, z.legout_idx + 1)
        line = (f"{z.kind:6s} {z.pattern} {lo:.2f}-{hi:.2f} legout {dt} base {z.base_count} "
                f"score {score} gap {z.gap_out} 2exc {z.two_exciting} move in {mi:.1f}% out {mo:.1f}%"
                f" quality {'ok' if ok else why}")
        if k is not None:
            upto = daily.iloc[:k]
            T = TF_SETS[CFG["tf"]]
            tr, tr_why = trend_detail(resample_ohlc(upto, CFG.get("trend_tf") or T["trend"]))
            mz, _ = htf_zones_asof(upto, T["loc"])
            if CFG.get("loc_q"):
                mz = mz + htf_zones_asof(upto, "QS")[0]
            okl, loc, _ = location_ok(z, mz)
            near = [f"{min(m.proximal, m.distal):.0f}-{max(m.proximal, m.distal):.0f}" for m in mz if m.kind == z.kind]
            line += (f"\n        first touch {str(d.index[k])[:10]} | weekly trend {tr} ({tr_why}) | "
                     f"monthly location {'OK ' + loc if okl else 'NO'} | monthly {z.kind} zones: {near[-6:]}")
        print(line)


def summarise(df: pd.DataFrame) -> str:
    closed = df[df["result"] != "Open"]
    if closed.empty:
        return "No closed trades."
    wins = (closed["R"] > 0).sum()
    n = len(closed)
    gross_w = closed.loc[closed["R"] > 0, "R"].sum()
    gross_l = -closed.loc[closed["R"] <= 0, "R"].sum()
    eq = (1 + closed.sort_values("exit_date")["R"] * C.RISK_PCT / 100).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    lines = [
        f"Trades (closed): {n}   open now: {(df['result'] == 'Open').sum()}",
        f"Win-rate: {wins / n * 100:.1f}%   ({wins} win / {n - wins} loss)",
        f"Average R per trade (expectancy): {closed['R'].mean():+.2f} R",
        f"Total R: {closed['R'].sum():+.1f} R   = {closed['R'].sum() * C.RISK_PCT:+.1f}% on capital at {C.RISK_PCT}% risk",
        f"Profit factor: {gross_w / gross_l:.2f}" if gross_l > 0 else "Profit factor: inf",
        f"Max drawdown (1% risk, one trade at a time): {dd:.1f}%",
        f"Average holding: {closed['days'].mean():.0f} days",
        "By side: " + ", ".join(f"{s} {len(g)} trades, {(g['R'] > 0).mean() * 100:.0f}% win, {g['R'].mean():+.2f}R"
                                 for s, g in closed.groupby("side")),
        "By exit: " + ", ".join(f"{r} {len(g)}" for r, g in closed.groupby("result")),
    ]
    return "\n".join(lines)


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
    ap.add_argument("--buy-only", action="store_true", help="skip SELL setups")
    ap.add_argument("--tf", choices=list(TF_SETS), default="daily", help="time-frame set")
    ap.add_argument("--min-score", type=float, default=7, help="minimum Trade Score of the execution zone (6 or 7)")
    ap.add_argument("--box-proof", type=float, default=0,
                    help="monthly box counts only after price moved this %% away from it (e.g. 10)")
    ap.add_argument("--trend-mode", choices=["both", "structure"], default="both")
    ap.add_argument("--trend2", default=None, help="also need structure up on this bigger TF, e.g. MS")
    ap.add_argument("--latest-only", action="store_true", help="trade only the newest zone of the stock")
    ap.add_argument("--min-rr", type=float, default=0, help="need at least this R:R to the weekly target")
    ap.add_argument("--no-trail", action="store_true", help="with --min-rr: skip trades without a weekly target")
    ap.add_argument("--max-box-overlap", type=int, default=None, help="clean monthly box: max overlapping candles")
    ap.add_argument("--loc-q", action="store_true", help="location may also be a Quarterly box")
    ap.add_argument("--trend-tf", default=None, help="override trend time frame, e.g. MS (monthly)")
    ap.add_argument("--no-garbage", action="store_true",
                    help="do not reject daily zones for sideways candles (consolidation inside the monthly box)")
    ap.add_argument("--explain", nargs=3, metavar=("SYMBOL", "FROM", "TO"),
                    help="print every zone of one stock formed between two dates and why it was (not) traded")
    ap.add_argument("--entry", choices=["limit", "type2", "type3"], default="limit",
                    help="limit = PDF type 1 at proximal; type2 / type3 = PDF confirmation entries")
    a = ap.parse_args(argv)
    OPTS["buy_only"], OPTS["entry"] = a.buy_only, a.entry
    CFG["tf"], CFG["min_score"], CFG["garbage"] = a.tf, a.min_score, not a.no_garbage
    CFG["box_proof"] = a.box_proof
    CFG["loc_q"], CFG["trend_tf"], CFG["trend_mode"] = a.loc_q, a.trend_tf, a.trend_mode
    CFG["trend2"] = a.trend2
    CFG.update(latest_only=a.latest_only, min_rr=a.min_rr, no_trail=a.no_trail,
               max_box_overlap=a.max_box_overlap)
    print(f"latest_only={a.latest_only} min_rr={a.min_rr} no_trail={a.no_trail} max_box_overlap={a.max_box_overlap}")
    print(f"loc_q={a.loc_q} trend_tf={a.trend_tf or 'default'} trend_mode={a.trend_mode}")
    print(f"Options: buy_only={a.buy_only} entry={a.entry} tf={a.tf} min_score={a.min_score} garbage={CFG['garbage']} box_proof={a.box_proof} | {TF_SETS[a.tf]['label']}")
    if a.explain:
        y = D.yahoo_symbol(a.explain[0])
        explain(a.explain[0], D.get_candles(y, "1d"), a.explain[1], a.explain[2])
        return 0
    if a.watchlist or a.all_watchlists:
        from run_agent import pick_watchlists
        targets = [y for y, _ in pick_watchlists(a.watchlist, a.all_watchlists)]
    else:
        targets = [D.yahoo_symbol(s) for s in (a.symbols or [])]
    targets = [t for t in targets if not t.startswith("^")]
    D.prefetch_daily(targets)
    all_trades = []
    for y in targets:
        sym = y.replace(".NS", "").replace(".BO", " (BSE)")
        try:
            daily = D.get_candles(y, "1d")
            if daily is None or len(daily) < 300:
                continue
            t = backtest_symbol(sym, daily)
        except Exception as e:  # noqa: BLE001
            print(f"{sym}: error {str(e)[:100]}")
            continue
        print(f"{sym:12s} trades {len(t):3d}  R {sum(x['R'] for x in t):+.1f}")
        all_trades += t
    os.makedirs("reports", exist_ok=True)
    df = pd.DataFrame(all_trades)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = f"reports/setup_backtest_{stamp}.csv"
    if not df.empty:
        df.sort_values(["entry_date"]).to_csv(path, index=False)
        print("\nFilter funnel:", "  ".join(f"{k}: {v}" for k, v in sorted(FUNNEL.items())))
        print("\n===== SUMMARY =====")
        print(summarise(df))
        print(f"\nSaved {len(df)} trades -> {path}")
        print("\n===== TRADES =====")
        cols = ["symbol", "side", "pattern", "zone_low", "zone_high", "entry_date", "entry", "stop",
                "target", "planned_rr", "exit_date", "exit", "result", "R", "days", "monthly_location", "box_overlap", "curve_pos"]
        print(df.sort_values("entry_date")[cols].to_csv(index=False))
    else:
        print("No trades.")


if __name__ == "__main__":
    sys.exit(main())
