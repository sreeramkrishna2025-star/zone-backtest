"""
The user's own system (top-down, follow-up zones):

1. BIG DIRECTION  - on Yearly / Half-yearly / Quarterly / Monthly charts find the
   last big zone that price tested.  Tested a DEMAND zone and moving up -> only BUY.
   Tested a SUPPLY zone and moving down -> only SELL.  Bigger time frame wins.
2. FOLLOW-UP ZONE - in that same move, the best fresh zone of the same type that
   formed on Monthly / Weekly / Daily (BUY -> DBR/RBR, SELL -> RBD/DBD).
3. GARBAGE FILTER - ignore zones sitting in sideways areas (many overlapping candles
   before the base) and zones that price did not leave strongly.
4. ALERTS         - (a) follow-up zone formed in the latest candle(s)  -> "naya zone"
                    (b) price within ALERT_DISTANCE_PCT of the zone       -> "zone paas"
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from . import config as C
from .data import resample_ohlc
from .zones import Zone, find_zones
from .analysis import ema

HTF_LIST = [("Yearly", "YS"), ("Half-yearly", "6MS"), ("Quarterly", "QS"), ("Monthly", "MS")]
FOLLOW_LIST = [("Monthly", "MS"), ("Weekly", "W-FRI"), ("Daily", None)]
TF_RANK = {"Monthly": 3, "Weekly": 2, "Daily": 1}


# ------------------------------------------------------------------ garbage filter
def zone_quality(z: Zone, d: pd.DataFrame) -> tuple[bool, str]:
    """True if the zone is clean. Returns (ok, reason_if_not)."""
    H, L = d["High"].to_numpy(), d["Low"].to_numpy()
    lo, hi = sorted((z.proximal, z.distal))
    height = max(hi - lo, 1e-9)

    # 1) sideways / garbage area: many candles before the legin overlapping the zone
    start = max(0, z.legin_idx - C.GARBAGE_LOOKBACK)
    before = range(start, z.legin_idx)
    overlap = sum(1 for k in before if L[k] <= hi and H[k] >= lo)
    if overlap >= C.GARBAGE_MAX_OVERLAP:
        return False, f"garbage area ({overlap} candles overlap before zone)"

    # 2) strong departure: price must leave the zone by >= DEPARTURE_MULT x zone height
    end = min(len(d), z.legout_idx + 1 + C.DEPARTURE_CANDLES)
    if z.kind == "demand":
        moved = H[z.legout_idx:end].max() - z.proximal
    else:
        moved = z.proximal - L[z.legout_idx:end].min()
    if moved < C.DEPARTURE_MULT * height and not z.explosive_legout:
        return False, "weak legout (price did not leave zone strongly)"
    return True, ""


# ------------------------------------------------------------------ move into / out of the base
def move_sizes(z: Zone, d: pd.DataFrame) -> tuple[float, float]:
    """% move INTO the base (legin side) and % move OUT of it (legout side).
    User's rule: a good move (10%+) comes, price HOLDS in a base, then a good
    move (10/15/20%+) leaves it.  That base is the zone."""
    H, L = d["High"].to_numpy(), d["Low"].to_numpy()
    b0, b1 = z.base_start, z.base_end
    base_hi, base_lo = H[b0:b1 + 1].max(), L[b0:b1 + 1].min()
    w0 = max(0, b0 - C.MOVE_LOOKBACK)
    if z.pattern in ("DBR", "DBD"):                 # price dropped into the base
        top = H[w0:b0].max() if b0 > w0 else base_hi
        move_in = (top - base_lo) / top * 100
    else:                                           # price rallied into the base
        bot = L[w0:b0].min() if b0 > w0 else base_lo
        move_in = (base_hi - bot) / bot * 100
    w1 = min(len(d), b1 + 1 + C.MOVE_LOOKBACK)
    if z.kind == "demand":
        move_out = (H[b1 + 1:w1].max() - z.proximal) / z.proximal * 100
    else:
        move_out = (z.proximal - L[b1 + 1:w1].min()) / z.proximal * 100
    return float(move_in), float(move_out)


def strong_moves(z: Zone, d: pd.DataFrame, tf: str) -> bool:
    need = C.MIN_MOVE_PCT.get(tf, 10)
    mi, mo = move_sizes(z, d)
    z.notes.append(f"move in {mi:.0f}% / out {mo:.0f}%")
    return mi >= need and mo >= need


# ------------------------------------------------------------------ step 1: big direction
@dataclass
class Direction:
    side: str            # BUY / SELL / NONE
    tf: str = ""
    zone: Zone | None = None
    touched_at: object = None
    text: str = ""


def big_direction(daily: pd.DataFrame) -> Direction:
    """Latest big-zone test across Yearly / Half-yearly / Quarterly / Monthly wins
    (e.g. ABREL: Yearly demand tested in 2023, then Half-yearly supply tested in
    2024-25 -> SELL).  Same time -> bigger time frame wins.  Big zones = 1-3 base
    candles is NOT required: the zone must have a strong move in and out (10%+)."""
    price = float(daily["Close"].iloc[-1])
    last_day = daily.index[-1]
    cands = []
    for rank, (name, rule) in enumerate(HTF_LIST):
        f = resample_ohlc(daily, rule)
        if len(f) < 6:
            continue
        zones, d = find_zones(f)
        H, L = d["High"].to_numpy(), d["Low"].to_numpy()
        for z in zones:
            if z.breached or not strong_moves(z, d, name):
                continue
            lo_, hi_ = sorted((z.proximal, z.distal))
            if (hi_ - lo_) / ((hi_ + lo_) / 2) * 100 > C.HTF_MAX_HEIGHT_PCT:
                continue                      # absurdly tall zone (e.g. 47-239), not a real zone
            if z.kind == "demand" and price < z.distal:
                continue
            if z.kind == "supply" and price > z.distal:
                continue
            for k in range(len(d) - 1, z.legout_idx, -1):
                touched = L[k] <= z.proximal if z.kind == "demand" else H[k] >= z.proximal
                if touched:
                    end = d.index[k + 1] if k + 1 < len(d) else last_day
                    cands.append((end, -rank, name, z, d.index[k]))
                    break
    if not cands:
        return Direction("NONE", text="koi bada zone test nahi hua")
    end, _, name, z, when = max(cands, key=lambda c: (c[0], c[1]))
    side = "BUY" if z.kind == "demand" else "SELL"
    lo, hi = sorted((z.proximal, z.distal))
    tag = {"Yearly": "Y", "Half-yearly": "H", "Quarterly": "Q", "Monthly": "M"}[name]
    label = f"{'RB' if side == 'BUY' else 'DB'}{z.base_count}-{tag}"
    return Direction(side, name, z, when,
                     f"{name} {'Demand' if side == 'BUY' else 'Supply'} {lo:.0f}-{hi:.0f} "
                     f"({label}) test hua {str(when)[:7]}")


# ------------------------------------------------------------------ step 2: follow-up zone
@dataclass
class Setup:
    symbol: str
    side: str
    tf: str
    zone: Zone
    score: float
    entry: float
    stop: float
    target: float
    qty: int
    price: float
    distance_pct: float
    is_new: bool
    direction: Direction
    notes: list
    points: float = 0.0
    why: str = ""


RULES = {"Daily": None, "Weekly": "W-FRI", "Monthly": "MS", "Quarterly": "QS",
         "Half-yearly": "6MS", "Yearly": "YS"}
ORDER = ["Daily", "Weekly", "Monthly", "Quarterly", "Half-yearly", "Yearly"]
# setup time frame -> trend is read on the next bigger time frame (trade WITH the trend)
PAIRS = [("Daily", "Weekly"), ("Weekly", "Monthly"), ("Monthly", "Quarterly"), ("Quarterly", "Half-yearly")]


def frame(daily: pd.DataFrame, tf: str) -> pd.DataFrame:
    r = RULES[tf]
    return daily if r is None else resample_ohlc(daily, r)


def sma_trend(f: pd.DataFrame) -> str:
    """PDF clock method: moving average (50, or shorter when few candles), 7 candles back.
    Average rising and price above it = up; falling and price below = down."""
    n = len(f)
    p = 50 if n >= 150 else max(5, n // 3)
    look = 7 if n >= 60 else 2
    if n < p + look + 1:
        return "unknown"
    sma = f["Close"].rolling(p).mean()
    c, now, back = f["Close"].iloc[-1], sma.iloc[-1], sma.iloc[-1 - look]
    if now > back and c > now:
        return "up"
    if now < back and c < now:
        return "down"
    return "sideways"


def swings(f: pd.DataFrame, k: int = 2):
    """Swing highs / lows (structure mapping, like the F1-F2-F3 zig-zag):
    a swing high is higher than k candles on both sides, a swing low lower."""
    H, L = f["High"].to_numpy(), f["Low"].to_numpy()
    hi, lo = [], []
    for i in range(k, len(f) - k):
        if H[i] == H[i - k:i + k + 1].max():
            hi.append((i, H[i]))
        if L[i] == L[i - k:i + k + 1].min():
            lo.append((i, L[i]))
    return hi, lo


def structure_trend(f: pd.DataFrame) -> str:
    """Higher High + Higher Low = up, Lower High + Lower Low = down.
    A close beyond the last swing (break of structure) also counts."""
    if len(f) < 12:
        return "unknown"
    hi, lo = swings(f)
    if len(hi) < 2 or len(lo) < 2:
        return "unknown"
    c = f["Close"].iloc[-1]
    (_, h1), (_, h2) = hi[-2], hi[-1]
    (_, l1), (_, l2) = lo[-2], lo[-1]
    if c < l2:
        return "down"                     # broke the last swing low
    if c > h2:
        return "up"                       # broke the last swing high
    if h2 > h1 and l2 > l1:
        return "up"
    if h2 < h1 and l2 < l1:
        return "down"
    return "sideways"


def trend_detail(f: pd.DataFrame) -> tuple[str, str]:
    """Final trend = SMA clock AND structure must agree; otherwise sideways."""
    a, b = sma_trend(f), structure_trend(f)
    if a == b and a in ("up", "down"):
        return a, f"SMA {a}, structure {b}"
    if a == "unknown" and b in ("up", "down"):
        return b, f"structure {b}"
    return "sideways", f"SMA {a}, structure {b}"


def trend_of(f: pd.DataFrame) -> str:
    return trend_detail(f)[0]


def htf_confluence(daily: pd.DataFrame, above_tf: str, kind: str) -> str:
    """Price inside / near a same-type zone on a time frame bigger than `above_tf`."""
    price = float(daily["Close"].iloc[-1])
    for tf in reversed(ORDER[ORDER.index(above_tf) + 1:]):
        f = frame(daily, tf)
        if len(f) < 6:
            continue
        zones, d = find_zones(f)
        for z in zones:
            if z.kind != kind or z.breached or not strong_moves(z, d, tf):
                continue
            lo, hi = sorted((z.proximal, z.distal))
            near = C.H_NEAR_PCT / 100
            if (kind == "demand" and lo <= price <= hi * (1 + near)) or \
               (kind == "supply" and lo * (1 - near) <= price <= hi):
                return f"{tf} {kind} {lo:.0f}-{hi:.0f}"
    return ""


def make_setup(symbol, side, tf, z, d, score, price, direc) -> Setup | None:
    atr = float(d["atr"].iloc[-1])
    if side == "BUY":
        entry = z.proximal * (1 + C.ENTRY_BUFFER_PCT / 100)
        stop = z.distal - C.SL_BUFFER_ATR * atr
        risk = entry - stop
        target = entry + C.REWARD_RISK * risk
        dist = (price - z.proximal) / price * 100
    else:
        entry = z.proximal * (1 - C.ENTRY_BUFFER_PCT / 100)
        stop = z.distal + C.SL_BUFFER_ATR * atr
        risk = stop - entry
        target = entry - C.REWARD_RISK * risk
        dist = (z.proximal - price) / price * 100
    if risk <= 0:
        return None
    qty = int(math.floor(C.CAPITAL * C.RISK_PCT / 100 / risk))
    new_window = 2 if tf == "Daily" else 1
    is_new = z.legout_idx >= len(d) - new_window
    return Setup(symbol, side, tf, z, score, entry, stop, target, qty, price, dist, is_new, direc, [])


def scored_zones(f: pd.DataFrame, tf: str, kind: str, patterns=None):
    """Fresh, clean zones of one type with score 7+ on one time frame."""
    zones, d = find_zones(f)
    e20, e50 = ema(f["Close"], 20).iloc[-1], ema(f["Close"], 50).iloc[-1]
    out = []
    for z in zones:
        if z.kind != kind or z.breached or z.tests > 0:
            continue
        if patterns and z.pattern not in patterns:
            continue
        ok, _ = zone_quality(z, d)
        if not ok or not strong_moves(z, d, tf):
            continue
        lo, hi = sorted((z.proximal, z.distal))
        score = z.base_score + (C.EMA_BONUS if (lo <= e20 <= hi or lo <= e50 <= hi) else 0)
        if score >= C.ALERT_MIN_SCORE:
            out.append((z, score))
    return out, d


def opposite_room(side, z, d, entry, risk) -> float:
    """Reward/risk room until the nearest fresh opposite zone on the same time frame."""
    zones, _ = find_zones(d[["Open", "High", "Low", "Close", "Volume"]])
    opp = [o for o in zones if o.kind != z.kind and not o.breached]
    if side == "BUY":
        tops = [o.proximal for o in opp if o.proximal > entry]
        return (min(tops) - entry) / risk if tops else 99.0
    bots = [o.proximal for o in opp if o.proximal < entry]
    return (entry - max(bots)) / risk if bots else 99.0


def plan_stock(symbol: str, daily: pd.DataFrame):
    """Best trade setups on every time frame, only WITH the trend of the next bigger
    time frame (trend up -> demand zones / BUY, trend down -> supply zones / SELL).
    Every setup gets QUALITY POINTS so the whole market can be ranked and only the
    best 5-10 shown."""
    price = float(daily["Close"].iloc[-1])
    tr_all = {tf: trend_detail(frame(daily, tf)) for tf in ("Weekly", "Monthly", "Quarterly",
                                                           "Half-yearly", "Yearly")}
    setups = []
    for tf, trend_tf in PAIRS:
        f = frame(daily, tf)
        if len(f) < 20:
            continue
        tr, tr_why = tr_all[trend_tf]
        side = {"up": "BUY", "down": "SELL"}.get(tr)
        if side is None:
            continue
        kind = "demand" if side == "BUY" else "supply"
        zs, d = scored_zones(f, tf, kind)
        conf = None
        for z, sc in zs:
            st = make_setup(symbol, side, tf, z, d, sc, price, None)
            if st is None or st.qty <= 0:
                continue
            if not st.is_new and not (0 < st.distance_pct <= C.ALERT_DISTANCE_PCT):
                continue
            if conf is None:
                conf = htf_confluence(daily, trend_tf, kind)
            # ---------------- quality points ----------------
            pts, why = sc, [f"score {sc:g}"]
            if conf:
                pts += 3; why.append("bada zone +3")
            bigger = ORDER[ORDER.index(trend_tf) + 1:]
            same = [t for t in bigger if t in tr_all and tr_all[t][0] == tr]
            if same:
                pts += len(same); why.append(f"{len(same)} bade TF same trend +{len(same)}")
            _, mo = move_sizes(z, d)
            if mo >= 30:
                pts += 2; why.append(f"legout {mo:.0f}% +2")
            elif mo >= 20:
                pts += 1; why.append(f"legout {mo:.0f}% +1")
            tfw = {"Daily": 0, "Weekly": 1, "Monthly": 2, "Quarterly": 3}[tf]
            pts += tfw
            if tfw:
                why.append(f"{tf} +{tfw}")
            risk = abs(st.entry - st.stop)
            room = opposite_room(side, z, d, st.entry, risk)
            if room < C.REWARD_RISK:
                pts -= 2; why.append(f"opposite zone {room:.1f}R par -2")
            else:
                pts += 1; why.append("2:1 room +1")
            if not st.is_new and st.distance_pct <= 1:
                pts += 1; why.append("1% paas +1")
            text = f"Trend: {trend_tf} {tr} ({tr_why})" + (f" | Bada zone: {conf} ✓" if conf else "")
            st.direction = Direction(side, trend_tf, None, None, text)
            st.notes = [conf] if conf else []
            st.points, st.why = pts, ", ".join(why)
            setups.append(st)
    setups.sort(key=lambda s: -s.points)
    return " ".join(f"{k}:{v[0]}" for k, v in tr_all.items()), setups


def pick_alerts(symbol: str, daily: pd.DataFrame):
    """Returns (trend_summary, best_new | None, best_near | None, all_setups)."""
    trends, setups = plan_stock(symbol, daily)
    new = next((s for s in setups if s.is_new), None)
    near = next((s for s in setups if not s.is_new), None)
    return trends, new, near, setups


def fmt(s: Setup, kind: str) -> str:
    icon = "🟢" if s.side == "BUY" else "🔴"
    lo, hi = sorted((s.zone.proximal, s.zone.distal))
    where = ("naya zone bana" if kind == "new" else
             ("zone ke andar" if s.distance_pct <= 0 else f"zone {s.distance_pct:.1f}% door"))
    star = "⭐ " if s.notes else ""
    head = f"{star}{icon} {s.symbol} {s.side} | {s.tf} {s.zone.pattern} | Score {s.score:g} | {where}"
    return (f"{head} | ⚡{s.points:g} pts\n"
            f"   {s.direction.text}\n"
            f"   CMP {s.price:.2f} | Zone {lo:.2f}-{hi:.2f} ({s.zone.base_count} base)\n"
            f"   Entry {s.entry:.2f}  SL {s.stop:.2f}  Tgt {s.target:.2f}  Qty {s.qty}")
