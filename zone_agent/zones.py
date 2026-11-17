"""
Demand & Supply zone engine, based on the "Trading in the Zone" PDF rules.

Candles:   exciting = body > 50% of range, base = body < 50% of range
           significant gap + base candle = works like an exciting candle
Zones:     legin -> base candle(s) -> legout
           DBR / RBR = demand (legout green),  RBD / DBD = supply (legout red)
Marking:   body-to-wick.  DZ proximal = highest body of base, distal = lowest wick
           SZ proximal = lowest body of base, distal = highest wick
           exceptional marking: distal moves to legin (reversal) / legout wick if it goes further
Score:     freshness (3 / 1.5 / 0) + strength (2 / 2 / 1) + time at base (2 / 1 / 0) = max 7
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from . import config as C


# --------------------------------------------------------------------------- candles
def classify_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Adds candle-type columns to an OHLC dataframe (columns Open High Low Close)."""
    d = df.copy()
    o, h, l, c = d["Open"], d["High"], d["Low"], d["Close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    d["body"] = body
    d["body_top"] = np.maximum(o, c)
    d["body_bot"] = np.minimum(o, c)
    d["ratio"] = (body / rng).fillna(0.0)
    d["green"] = c > o
    d["red"] = c < o
    d["exciting"] = d["ratio"] > C.EXCITING_BODY_RATIO
    avg_body = body.rolling(C.AVG_BODY_WINDOW, min_periods=5).mean()
    d["explosive"] = d["exciting"] & (body > C.EXPLOSIVE_BODY_MULT * avg_body)
    d["gap_up"] = o > h.shift(1)        # opens above previous high (significant gap)
    d["gap_down"] = o < l.shift(1)      # opens below previous low
    # leg candles: exciting candle, or base candle that opened with a significant gap
    d["leg_up"] = (d["exciting"] & d["green"]) | (~d["exciting"] & d["gap_up"])
    d["leg_down"] = (d["exciting"] & d["red"]) | (~d["exciting"] & d["gap_down"])
    d["is_base"] = ~d["exciting"] & ~d["leg_up"] & ~d["leg_down"]
    d["atr"] = atr(df, 14)
    rng_safe = rng.fillna(0.0)
    d["upper_wick_ratio"] = ((h - d["body_top"]) / rng).fillna(0.0)
    d["lower_wick_ratio"] = ((d["body_bot"] - l) / rng).fillna(0.0)
    d["wick_ratio"] = d["upper_wick_ratio"] + d["lower_wick_ratio"]
    return d


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()


# --------------------------------------------------------------------------- zone object
@dataclass
class Zone:
    kind: str                 # "demand" / "supply"
    pattern: str              # DBR, RBR, RBD, DBD
    legin_idx: int
    base_start: int
    base_end: int
    legout_idx: int
    proximal: float
    distal: float
    base_count: int
    gap_out: bool             # price left the base with a gap
    two_exciting: bool        # legout followed by another exciting candle
    explosive_legout: bool
    strong_close: bool        # legout closed beyond legin's full range (wick)
    formed_at: object = None
    tests: int = 0            # completed visits back into the zone
    in_zone_now: bool = False
    breached: bool = False
    breached_idx: int | None = None
    reaction_of_previous: bool = False
    notes: list = field(default_factory=list)

    # ---- PDF Trade Score -------------------------------------------------
    @property
    def freshness_score(self) -> float:
        return {0: 3.0, 1: 1.5}.get(self.tests, 0.0)

    @property
    def strength_score(self) -> float:
        # PDF: gap = 2, two exciting candles = 2, only one exciting candle and no gap = 1
        return 2.0 if (self.gap_out or self.two_exciting) else 1.0

    @property
    def base_time_score(self) -> float:
        if self.base_count <= 3:
            return 2.0
        if self.base_count <= 5:
            return 1.0
        return 0.0

    @property
    def base_score(self) -> float:
        return self.freshness_score + self.strength_score + self.base_time_score

    @property
    def authentic(self) -> bool:
        """PDF Credibility: a zone that is only a reaction of a previous zone is
        non-authentic, unless the legout closing is very strong (Case 4)."""
        return (not self.reaction_of_previous) or self.strong_close

    @property
    def valid(self) -> bool:
        return not self.breached and self.tests < 2

    def to_dict(self):
        d = asdict(self)
        d.update(base_score=self.base_score, authentic=self.authentic)
        return d


# --------------------------------------------------------------------------- detection
def find_zones(df: pd.DataFrame, max_base: int | None = None,
               require_high_break: bool = False,
               strict_shapes: bool = False) -> tuple[list[Zone], pd.DataFrame]:
    """Find every demand & supply zone in the data (oldest first).

    require_high_break (DBR demand only): the drop starts at the first red candle
    of the drop run. The legout candle must CLOSE above the High of that first
    red candle, in the same timeframe, or the zone is rejected.
    """
    d = classify_candles(df)
    n = len(d)
    O, H, L, Cl = (d[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    btop, bbot = d["body_top"].to_numpy(), d["body_bot"].to_numpy()
    leg_up, leg_dn = d["leg_up"].to_numpy(), d["leg_down"].to_numpy()
    is_base, expl = d["is_base"].to_numpy(), d["explosive"].to_numpy()
    green = d["green"].to_numpy()
    red = d["red"].to_numpy()
    if strict_shapes:
        # base candle: body <= 40% of range (wicks >= 60%)
        base_ok = is_base & (d["ratio"].to_numpy() <= C.STRICT_BASE_BODY_MAX)
        # legout (demand only): total wick <= 20% of range
        legout_wick_ok = d["wick_ratio"].to_numpy() <= C.STRICT_LEGOUT_WICK_MAX
        # drop candle (DBR): lower wick <= 10% of range
        drop_lower_ok = d["lower_wick_ratio"].to_numpy() <= C.STRICT_DROP_LOWER_WICK_MAX
    else:
        base_ok = is_base
        legout_wick_ok = np.ones(n, dtype=bool)
        drop_lower_ok = np.ones(n, dtype=bool)

    zones: list[Zone] = []
    for i in range(2, n):
        for kind in ("demand", "supply"):
            if kind == "demand" and not leg_up[i]:
                continue
            if kind == "demand" and not legout_wick_ok[i]:
                continue
            if kind == "supply" and not leg_dn[i]:
                continue
            # walk back over base candles
            j = i - 1
            bases = []
            mb = max_base or C.MAX_BASE_CANDLES
            while j >= 0 and base_ok[j] and len(bases) <= mb:
                bases.append(j)
                j -= 1
            if not bases or j < 0 or len(bases) > mb:
                continue
            legin = j
            if not (leg_up[legin] or leg_dn[legin]):
                continue
            b = np.array(sorted(bases))
            legin_up = bool(leg_up[legin] and (green[legin] or not leg_dn[legin]))

            if kind == "demand":
                pattern = "RBR" if legin_up else "DBR"
                if strict_shapes and pattern == "DBR" and not drop_lower_ok[legin]:
                    continue
                if strict_shapes and pattern == "RBR" and d["wick_ratio"].iloc[i] > C.STRICT_RBR_LEGOUT_WICK_MAX:
                    continue
                if strict_shapes and pattern == "RBR" and d["wick_ratio"].iloc[legin] > C.STRICT_RBR_LEGIN_WICK_MAX:
                    continue
                # closing concept: legout closes above legin
                if C.REQUIRE_CLOSING_CONCEPT and Cl[i] <= btop[legin]:
                    continue
                if require_high_break and pattern == "DBR":
                    # walk back to the first red candle of the drop run
                    s = legin
                    while s - 1 >= 0 and red[s - 1]:
                        s -= 1
                    if Cl[i] <= H[s]:
                        continue
                proximal = btop[b].max()
                distal = L[b].min()
                if C.EXCEPTIONAL_MARKING:
                    distal = min(distal, L[i])
                    if pattern == "DBR":
                        distal = min(distal, L[legin])
                gap_out = O[i] > H[b[-1]]
                two_exc = i + 1 < n and bool(leg_up[i + 1])
                strong_close = Cl[i] > H[legin]
            else:
                pattern = "DBD" if not legin_up else "RBD"
                if strict_shapes:
                    wr = d["wick_ratio"].to_numpy()
                    legin_max = C.STRICT_DBD_WICK_MAX if pattern == "DBD" else C.STRICT_RBD_RALLY_WICK_MAX
                    legout_max = C.STRICT_DBD_WICK_MAX if pattern == "DBD" else C.STRICT_RBD_DROP_WICK_MAX
                    if wr[legin] > legin_max or wr[i] > legout_max:
                        continue
                if C.REQUIRE_CLOSING_CONCEPT and Cl[i] >= bbot[legin]:
                    continue
                proximal = bbot[b].min()
                distal = H[b].max()
                if C.EXCEPTIONAL_MARKING:
                    distal = max(distal, H[i])
                    if pattern == "RBD":
                        distal = max(distal, H[legin])
                gap_out = O[i] < L[b[-1]]
                two_exc = i + 1 < n and bool(leg_dn[i + 1])
                strong_close = Cl[i] < L[legin]

            if proximal == distal:
                continue
            z = Zone(kind, pattern, legin, int(b[0]), int(b[-1]), i, float(proximal),
                     float(distal), len(b), bool(gap_out), bool(two_exc), bool(expl[i]),
                     bool(strong_close), formed_at=d.index[i])
            _track_after(z, H, L, Cl)
            zones.append(z)

    _mark_credibility(zones, L, H)
    return zones, d


def _track_after(z: Zone, H, L, Cl):
    """Count tests (freshness) and detect breach after the zone was formed."""
    n = len(Cl)
    inside = False
    for k in range(z.legout_idx + 1, n):
        if z.kind == "demand":
            touched = L[k] <= z.proximal
            broke = Cl[k] < z.distal
        else:
            touched = H[k] >= z.proximal
            broke = Cl[k] > z.distal
        if broke:
            z.breached, z.breached_idx = True, k
            return
        if touched and not inside:
            inside = True
            if k == n - 1:          # the visit happening right now is the trade, not a past test
                z.in_zone_now = True
            else:
                z.tests += 1
        elif touched and inside and k == n - 1:
            # still inside since an earlier candle: current visit -> undo that count
            z.tests -= 1
            z.in_zone_now = True
        elif not touched:
            inside = False


def _mark_credibility(zones: list[Zone], L, H):
    """PDF Credibility Case 1/4: new zone formed while price was reacting
    from an older, still-valid zone of the same type -> reaction (non-authentic)."""
    for idx, z in enumerate(zones):
        for old in zones[:idx]:
            if old.kind != z.kind or old.legout_idx >= z.base_start:
                continue
            if old.breached and old.breached_idx is not None and old.breached_idx <= z.base_start:
                continue  # Case 2: old zone already broken -> new zone is fresh & authentic
            if z.kind == "demand":
                base_low = L[z.base_start: z.base_end + 1].min()
                overlap = base_low <= old.proximal and z.proximal >= old.distal
            else:
                base_high = H[z.base_start: z.base_end + 1].max()
                overlap = base_high >= old.proximal and z.proximal <= old.distal
            if overlap:
                z.reaction_of_previous = True
                z.notes.append(f"reaction of older {old.pattern} zone")
                break


def breach_trend(zones: list[Zone]) -> str:
    """PDF Advance Trend Analysis: 2 supply zones breached -> up,
    2 demand zones breached -> down, only 1 -> sideways."""
    events = sorted((z.breached_idx, z.kind) for z in zones if z.breached)
    if len(events) < 2:
        return "sideways"
    last2 = [k for _, k in events[-2:]]
    if last2 == ["supply", "supply"]:
        return "up"
    if last2 == ["demand", "demand"]:
        return "down"
    return "sideways"
