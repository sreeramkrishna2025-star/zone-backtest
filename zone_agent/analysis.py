"""
Trend, location (curve), indicators, trade plan & risk — PDF rules.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as C
from .zones import Zone, find_zones, breach_trend


# --------------------------------------------------------------------------- indicators
def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def stochastic(df, k=5, d=3, smooth=3):
    lo, hi = df["Low"].rolling(k).min(), df["High"].rolling(k).max()
    raw = 100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)
    pk = raw.rolling(smooth).mean()
    return pk, pk.rolling(d).mean()


def rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# --------------------------------------------------------------------------- trend
def clock_trend(df: pd.DataFrame, atr_series: pd.Series) -> str:
    """PDF: add 50 SMA, go 7 candles back, imagine a clock 12-3-6.
    SMA rising (12-3) = up, falling (3-6) = down, flat (close to 3) = sideways."""
    s = sma(df["Close"], C.TREND_SMA)
    if s.notna().sum() < C.TREND_LOOKBACK + 1:
        return "unknown"
    now, back = s.iloc[-1], s.iloc[-1 - C.TREND_LOOKBACK]
    move = (now - back) / max(atr_series.iloc[-1], 1e-9)
    if move > C.TREND_FLAT_ATR:
        return "up"
    if move < -C.TREND_FLAT_ATR:
        return "down"
    return "sideways"


def combined_trend(df, zones, atr_series) -> dict:
    ct = clock_trend(df, atr_series)
    bt = breach_trend(zones)
    final = ct
    if ct in ("up", "down") and bt in ("up", "down") and bt != ct:
        final = "sideways"          # the two methods disagree -> stay careful
    if ct == "unknown":
        final = bt
    return {"clock": ct, "zone_breach": bt, "final": final}


# --------------------------------------------------------------------------- location
def nearest_zones(zones: list[Zone], price: float):
    """Nearest valid (not breached) demand below/at price and supply above/at price."""
    dz = [z for z in zones if z.kind == "demand" and not z.breached and z.distal <= price]
    sz = [z for z in zones if z.kind == "supply" and not z.breached and z.distal >= price]
    dz = max(dz, key=lambda z: z.proximal, default=None)
    sz = min(sz, key=lambda z: z.proximal, default=None)
    return dz, sz


def curve_location(zones: list[Zone], price: float) -> dict:
    """PDF Curve Analysis: divide area between SZ proximal and DZ proximal into 3 parts."""
    fresh = [z for z in zones if z.tests == 0]
    dz, sz = nearest_zones(fresh, price)
    if dz is None or sz is None:
        return {"location": "equilibrium", "pct": None,
                "note": "only one side has a fresh zone -> treat as equilibrium, trade with trend"}
    lo, hi = dz.proximal, sz.proximal
    if price <= lo:
        loc, pct = "very low", 0.0
    elif price >= hi:
        loc, pct = "very high", 100.0
    else:
        pct = 100 * (price - lo) / (hi - lo)
        if pct < 100 / 6:
            loc = "very low"
        elif pct < 100 / 3:
            loc = "low"
        elif pct <= 200 / 3:
            loc = "equilibrium"
        elif pct <= 500 / 6:
            loc = "high"
        else:
            loc = "very high"
    return {"location": loc, "pct": round(pct, 1), "dz": dz.proximal, "sz": sz.proximal}


def decide_bias(location: str, itf_trend: str) -> tuple[str, str]:
    """PDF Rules: low on curve -> BUY, high -> SELL, equilibrium -> follow ITF trend."""
    if location in ("low", "very low"):
        bias, why = "BUY", f"price {location} on HTF curve"
    elif location in ("high", "very high"):
        bias, why = "SELL", f"price {location} on HTF curve"
    else:
        bias = {"up": "BUY", "down": "SELL"}.get(itf_trend, "NONE")
        why = f"HTF equilibrium -> ITF trend {itf_trend}"
    return bias, why


# --------------------------------------------------------------------------- candle patterns
def candle_patterns(d: pd.DataFrame) -> list[str]:
    """Last-candle confirmation patterns from the PDF (informational)."""
    if len(d) < 3:
        return []
    a, b, c3 = d.iloc[-3], d.iloc[-2], d.iloc[-1]
    out = []
    # engulfing
    if b["red"] and c3["green"] and c3["body_top"] >= b["body_top"] and c3["body_bot"] <= b["body_bot"]:
        out.append("Bullish Engulfing")
    if b["green"] and c3["red"] and c3["body_top"] >= b["body_top"] and c3["body_bot"] <= b["body_bot"]:
        out.append("Bearish Engulfing")
    # harami
    if b["red"] and c3["green"] and c3["body_top"] <= b["body_top"] and c3["body_bot"] >= b["body_bot"]:
        out.append("Bullish Harami")
    if b["green"] and c3["red"] and c3["body_top"] <= b["body_top"] and c3["body_bot"] >= b["body_bot"]:
        out.append("Bearish Harami")
    # morning / evening star
    if a["red"] and not b["exciting"] and c3["green"] and c3["Close"] > (a["Open"] + a["Close"]) / 2:
        out.append("Morning Star" + (" (strong)" if c3["Close"] > a["High"] else ""))
    if a["green"] and not b["exciting"] and c3["red"] and c3["Close"] < (a["Open"] + a["Close"]) / 2:
        out.append("Evening Star" + (" (strong)" if c3["Close"] < a["Low"] else ""))
    # hammer / inverted hammer
    rng = c3["High"] - c3["Low"]
    if rng > 0:
        lower = c3["body_bot"] - c3["Low"]
        upper = c3["High"] - c3["body_top"]
        if lower >= 2 * c3["body"] and upper <= 0.25 * rng:
            out.append(("Green" if c3["green"] else "Red") + " Hammer")
        if upper >= 2 * c3["body"] and lower <= 0.25 * rng:
            out.append(("Red" if c3["red"] else "Green") + " Inverted Hammer")
    return out


# --------------------------------------------------------------------------- trade plan
@dataclass
class TradePlan:
    symbol: str
    side: str
    zone: Zone
    entry: float
    stop: float
    target: float
    qty: int
    risk_rs: float
    base_score: float
    bonus: float
    total_score: float
    entry_type: str
    status: str
    distance_pct: float
    rr_ok: bool
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def row(self) -> dict:
        z = self.zone
        return {
            "Symbol": self.symbol, "Side": self.side, "Pattern": z.pattern,
            "Proximal": round(z.proximal, 2), "Distal": round(z.distal, 2),
            "Entry": round(self.entry, 2), "SL": round(self.stop, 2),
            "Target": round(self.target, 2), "Qty": self.qty,
            "Risk Rs": round(self.risk_rs), "Score": self.total_score,
            "Base score": self.base_score, "Entry type": self.entry_type,
            "Status": self.status, "Dist %": round(self.distance_pct, 2),
            "Zone date": str(z.formed_at)[:16],
            "Why": "; ".join(self.reasons), "Warnings": "; ".join(self.warnings),
        }


def build_plan(symbol, side, z: Zone, d: pd.DataFrame, opposite: Zone | None,
               bonus: float, reasons: list, warnings: list) -> TradePlan:
    price = float(d["Close"].iloc[-1])
    a = float(d["atr"].iloc[-1])
    buf = C.ENTRY_BUFFER_PCT / 100
    if side == "BUY":
        entry = z.proximal * (1 + buf)
        stop = z.distal - C.SL_BUFFER_ATR * a
        risk_ps = entry - stop
        target = entry + C.REWARD_RISK * risk_ps
        rr_ok = opposite is None or opposite.proximal >= target
        dist = (price - z.proximal) / price * 100
    else:
        entry = z.proximal * (1 - buf)
        stop = z.distal + C.SL_BUFFER_ATR * a
        risk_ps = stop - entry
        target = entry - C.REWARD_RISK * risk_ps
        rr_ok = opposite is None or opposite.proximal <= target
        dist = (z.proximal - price) / price * 100
    risk_amount = C.CAPITAL * C.RISK_PCT / 100
    qty = int(math.floor(risk_amount / risk_ps)) if risk_ps > 0 else 0
    if not rr_ok:
        warnings.append(f"opposite zone at {opposite.proximal:.2f} comes before 2:1 target")

    total = z.base_score + bonus
    if total >= C.SET_AND_FORGET_SCORE:
        etype = "Type 1 (set & forget limit order)"
    elif total >= C.MIN_SCORE:
        etype = "Type 2/3 (wait for confirmation)"
    else:
        etype = "NO TRADE (score < 5)"

    if any("against ITF trend" in w for w in warnings) and not etype.startswith("NO TRADE"):
        etype = "Type 3 (confirmation - against trend)"
    status = _entry_status(side, z, d, etype)
    return TradePlan(symbol, side, z, entry, stop, target, qty, qty * risk_ps,
                     z.base_score, bonus, total, etype, status, dist, rr_ok,
                     reasons, warnings)


def _entry_status(side, z, d, etype) -> str:
    """Entry type 2: candle closes inside zone, next opens inside zone.
       Entry type 3: candle closes inside zone, next leaves zone in our direction."""
    last, prev = d.iloc[-1], d.iloc[-2]
    lo, hi = sorted((z.proximal, z.distal))

    def inside(x):
        return lo <= x <= hi

    if not z.in_zone_now:
        return "WAITING - price not in zone yet (set alert at proximal)"
    if etype.startswith("Type 1"):
        return "IN ZONE - limit entry active"
    if inside(prev["Close"]):
        if side == "BUY" and last["Close"] > z.proximal:
            return "TRIGGERED - Type 3 confirmation (left zone upside)"
        if side == "SELL" and last["Close"] < z.proximal:
            return "TRIGGERED - Type 3 confirmation (left zone downside)"
        if inside(last["Open"]) and not etype.startswith("Type 3"):
            return "TRIGGERED - Type 2 (closed in zone, next opened in zone)"
        return "IN ZONE - closed inside, waiting for a candle to leave the zone (Type 3)"
    return "IN ZONE - wait for candle to close inside zone"


# --------------------------------------------------------------------------- full analysis
def analyse_symbol(symbol: str, htf: pd.DataFrame, itf: pd.DataFrame, ltf: pd.DataFrame,
                   sector_ltf: pd.DataFrame | None = None) -> dict:
    """Multi-timeframe analysis -> list of trade plans for one stock."""
    out = {"symbol": symbol, "plans": [], "error": None}
    if any(x is None or len(x) < 30 for x in (htf, itf, ltf)):
        out["error"] = "not enough data"
        return out

    htf_z, htf_d = find_zones(htf)
    itf_z, itf_d = find_zones(itf)
    ltf_z, ltf_d = find_zones(ltf)
    price = float(ltf["Close"].iloc[-1])

    loc = curve_location(htf_z, price)
    trend = combined_trend(itf, itf_z, itf_d["atr"])
    bias, why = decide_bias(loc["location"], trend["final"])
    out.update(price=price, location=loc, trend=trend, bias=bias, bias_reason=why,
               patterns=candle_patterns(ltf_d), zones=ltf_z, ltf=ltf_d)
    if bias == "NONE":
        return out

    kind = "demand" if bias == "BUY" else "supply"
    e20, e50 = ema(ltf["Close"], 20).iloc[-1], ema(ltf["Close"], 50).iloc[-1]
    k, _ = stochastic(ltf)
    kval = k.iloc[-1]
    sector_zone_kinds = set()
    if sector_ltf is not None and len(sector_ltf) > 30:
        sz_list, _ = find_zones(sector_ltf)
        sp = float(sector_ltf["Close"].iloc[-1])
        for z in sz_list:
            if z.valid and min(z.proximal, z.distal) * 0.99 <= sp <= max(z.proximal, z.distal) * 1.01:
                sector_zone_kinds.add(z.kind)

    candidates = [z for z in ltf_z if z.kind == kind and z.valid and z.authentic]
    for z in candidates:
        dist = (price - z.proximal) / price * 100 if kind == "demand" else (z.proximal - price) / price * 100
        if dist > C.MAX_DISTANCE_PCT or (dist < 0 and not z.in_zone_now):
            continue
        reasons = [why, f"ITF trend {trend['final']}"]
        warnings = []
        bonus = 0.0
        lo, hi = sorted((z.proximal, z.distal))
        if lo <= e20 <= hi or lo <= e50 <= hi:
            bonus += C.EMA_BONUS
            reasons.append("EMA 20/50 through zone +1")
        if (kind == "demand" and kval < 20) or (kind == "supply" and kval > 80):
            bonus += C.STOCH_BONUS
            reasons.append(f"Stochastic {'oversold' if kind == 'demand' else 'overbought'} +1")
        if kind in sector_zone_kinds:
            bonus += C.SECTOR_BONUS
            reasons.append("Sector also at same zone +2")
        if z.explosive_legout:
            reasons.append("explosive legout")
        if (bias == "BUY" and trend["final"] == "down") or (bias == "SELL" and trend["final"] == "up"):
            warnings.append("against ITF trend -> use only confirmation entry (Type 3)")
        if z.reaction_of_previous:
            warnings.append("reaction zone, tradeable only because of strong closing")
        # opposite zone for target check
        opp_kind = "supply" if kind == "demand" else "demand"
        opps = [o for o in ltf_z if o.kind == opp_kind and o.valid]
        if kind == "demand":
            opps = [o for o in opps if o.proximal > z.proximal]
            opposite = min(opps, key=lambda o: o.proximal, default=None)
        else:
            opps = [o for o in opps if o.proximal < z.proximal]
            opposite = max(opps, key=lambda o: o.proximal, default=None)
        plan = build_plan(symbol, bias, z, ltf_d, opposite, bonus, reasons, warnings)
        if plan.total_score < C.MIN_SCORE:
            continue                      # PDF: no trade below score 5
        out["plans"].append(plan)

    rank = {"TRIGGERED": 0, "IN ZONE": 1, "WAITING": 2}
    out["plans"].sort(key=lambda p: (rank.get(p.status.split(" -")[0], 3),
                                     -p.total_score, abs(p.distance_pct)))
    return out
