"""
Agent settings. Change values here to match your own trading style.
(Badha settings ahiya che - tamari style pramane badli shako.)
"""

# ---------------- Capital & Risk (PDF: Risk Management) ----------------
CAPITAL = 1000000         # example capital (Rs) for position sizing
RISK_PCT = 1.0            # 1% beginner, 1.5% intermediate, 2% pro
REWARD_RISK = 2.0         # Target = 2 x (Entry - SL)   (2:1)

# ---------------- Candle rules (PDF: Types of candles) ----------------
EXCITING_BODY_RATIO = 0.5   # body > 50% of range = exciting, else base
EXPLOSIVE_BODY_MULT = 1.5   # exciting AND body > 1.5x avg body = explosive
AVG_BODY_WINDOW = 20

# ---------------- Zone rules ----------------
STRICT_BASE_BODY_MAX = 0.40   # base candle body <= 40% of range (base candles only)
STRICT_WICK_MAX = 0.20        # legout candles (all patterns): total wick <= 20% of range (18 Nov 2026, user)
STRICT_LEGIN_WICK_MAX = 0.30  # legin candles (all patterns): total wick <= 30% of range (18 Nov 2026, user)
MAX_BASE_CANDLES = 2        # user rule: a zone has at most 2 base candles
EXCEPTIONAL_MARKING = True  # use legin/legout wick for distal when it goes beyond base
REQUIRE_CLOSING_CONCEPT = True  # DZ legout must close above legin, SZ below legin
ENTRY_BUFFER_PCT = 0.05     # entry "just above/below" proximal (in %)
SL_BUFFER_ATR = 0.10        # SL "with some room" beyond distal (fraction of ATR)

# ---------------- Trade score (PDF: Trade Score) ----------------
MIN_SCORE = 5               # no trade below 5
SET_AND_FORGET_SCORE = 7    # 7+ -> Entry type 1, 5-6 -> Entry type 2/3
EMA_BONUS = 1               # 20/50 EMA passing through zone
STOCH_BONUS = 1             # stochastic oversold at DZ / overbought at SZ
SECTOR_BONUS = 2            # sector also at same type of zone

# ---------------- Trend (PDF: How to look at the trend) ----------------
TREND_SMA = 50
TREND_LOOKBACK = 7          # vertical line on the 7th candle back
TREND_FLAT_ATR = 0.5        # SMA move < 0.5 ATR over 7 candles = "close to 3" (sideways)

# ---------------- Scanner ----------------
MAX_DISTANCE_PCT = 5.0      # show zones only within 5% of current price
DEFAULT_PRESET = "monthly"  # see TIMEFRAME_PRESETS

# PDF: Trading on different time frames  ->  (HTF location, ITF trend, LTF execution)
TIMEFRAME_PRESETS = {
    "hourly":  ("75m", "15m", "5m"),
    "daily":   ("1d",  "75m", "15m"),
    "weekly":  ("1wk", "1d",  "75m"),
    "monthly": ("1mo", "1wk", "1d"),
}

# Sector index for some popular stocks (Yahoo symbols). Add more as you like.
SECTOR_INDEX = {
    "^CNXMETAL": ["TATASTEEL", "HINDALCO", "JSWSTEEL", "VEDL", "HINDCOPPER", "SAIL", "NMDC"],
    "^NSEBANK":  ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
                  "BANKBARODA", "FEDERALBNK", "PNB"],
    "^CNXAUTO":  ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO",
                  "TVSMOTOR", "ASHOKLEY"],
    "^CNXIT":    ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM"],
    "^CNXPHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN"],
    "^CNXFMCG":  ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR"],
    "^CNXREALTY": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE"],
    "NIFTY_FIN_SERVICE.NS": ["BAJFINANCE", "BAJAJFINSV", "SHRIRAMFIN", "HDFCAMC", "CHOLAFIN"],
}

DEFAULT_WATCHLIST = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "TATASTEEL",
    "HINDALCO", "MARUTI", "TATAMOTORS", "ASHOKLEY", "BANKBARODA", "TVSMOTOR",
    "SUNPHARMA", "ITC", "DLF", "BAJFINANCE", "SHRIRAMFIN", "FEDERALBNK", "HDFCAMC",
]

# ---------------- alert list settings ----------------
ALERT_MIN_SCORE = 7         # only setups with score 7 or more
ALERT_DISTANCE_PCT = 2.0    # price within 2% of the zone
ALERT_INCLUDE_IN_ZONE = False   # True = also send stocks already inside the zone

HTF_MAX_HEIGHT_PCT = 80      # ignore big zones taller than 80% of their middle price
# A zone = strong move INTO a base, price holds, strong move OUT (user's rule)
MIN_MOVE_PCT = {"Yearly": 10, "Half-yearly": 10, "Quarterly": 10, "Monthly": 10,
                "Weekly": 10, "Daily": 5}
MOVE_LOOKBACK = 5           # candles checked before / after the base for those moves

ALERT_MAX_PER_SECTION = 25  # (old lists)
TOP_N = 10                  # nightly message: only the best 10 setups of the whole market
FOLLOW_MAX_AGE_DAYS = 365   # follow-up zones must be from the last 1 year
H_NEAR_PCT = 3.0            # price within 3% of a bigger time frame zone = confluence (⭐)

# ---------------- Garbage filter (follow-up zones) ----------------
GARBAGE_LOOKBACK = 10       # candles checked before the legin
GARBAGE_MAX_OVERLAP = 5     # this many overlapping candles = sideways / garbage area
DEPARTURE_CANDLES = 3       # after legout, price must leave the zone within these candles...
DEPARTURE_MULT = 2.0        # ...by at least 2 x zone height (or an explosive legout)

# (alerts live in the private agent repo)
