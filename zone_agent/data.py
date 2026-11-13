"""
Market data: Yahoo Finance (free) for NSE stocks, with 75 min / 125 min candles
built from smaller candles aligned to the 9:15 NSE open. Also CSV + demo data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# yfinance interval + how much history it allows
_YF = {
    "5m": ("5m", "60d"), "15m": ("15m", "60d"), "1h": ("1h", "730d"),
    "1d": ("1d", "max"), "1wk": ("1wk", "15y"), "1mo": ("1mo", "max"),
}
# custom NSE intervals -> (source interval, minutes)
_CUSTOM = {"75m": ("15m", 75), "125m": ("5m", 125), "10m": ("5m", 10)}

_cache: dict = {}


# TradingView index codes -> Yahoo Finance codes
TV_INDEX = {
    "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "CNXIT": "^CNXIT", "CNXAUTO": "^CNXAUTO",
    "CNXPHARMA": "^CNXPHARMA", "CNXMETAL": "^CNXMETAL", "CNXFMCG": "^CNXFMCG",
    "CNXREALTY": "^CNXREALTY", "CNXENERGY": "^CNXENERGY", "CNXINFRA": "^CNXINFRA",
    "CNXPSUBANK": "^CNXPSUBANK", "CNX100": "^CNX100", "CNX500": "^CRSLDX",
    "CNXMEDIA": "^CNXMEDIA", "CNXPSE": "^CNXPSE", "CNXSERVICE": "^CNXSERVICE",
    "CNXCOMMODITIES": "^CNXCMDT", "NIFTYJR": "^NSMIDCP", "CNXFINANCE": "NIFTY_FIN_SERVICE.NS",
    "CNXMIDCAP": "^NSEMDCP50", "NIFTYMIDCAP50": "^NSEMDCP50",
}


def tv_to_yahoo(tv: str) -> str | None:
    """'NSE:BAJAJ_AUTO' -> 'BAJAJ-AUTO.NS', 'BSE:XYZ' -> 'XYZ.BO'.
    Returns None for symbols this agent does not scan (foreign, futures, CFD)."""
    if ":" not in tv:
        return None
    exch, code = tv.split(":", 1)
    if code.endswith("!"):          # futures continuous contract
        return None
    if exch == "NSE":
        if code in TV_INDEX:
            return TV_INDEX[code]
        if code.startswith(("CNX", "NIFTY")):
            return None             # other indices not on Yahoo
        return code.replace("_", "-") + ".NS"
    if exch == "BSE":
        return code.replace("_", "-") + ".BO"
    return None


def yahoo_symbol(sym: str) -> str:
    if sym.startswith("^") or sym.endswith(".NS") or sym.endswith(".BO") or "." in sym:
        return sym
    return sym.upper() + ".NS"


def _download(symbol: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    key = (symbol, interval)
    if key in _cache:
        return _cache[key]
    yf_int, period = _YF[interval]
    df = yf.download(symbol, period=period, interval=yf_int, progress=False,
                     auto_adjust=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    if interval in ("5m", "15m", "1h") and df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata")
    _cache[key] = df
    return df


def prefetch_daily(symbols: list[str], chunk: int = 80, log=print):
    """Download daily candles for many stocks together (much faster than one by one)."""
    import yfinance as yf
    todo = [yahoo_symbol(x) for x in symbols if (yahoo_symbol(x), "1d") not in _cache]
    yf_int, period = _YF["1d"]
    for k in range(0, len(todo), chunk):
        part = todo[k:k + chunk]
        log(f"  downloading {k + 1}-{k + len(part)} of {len(todo)} ...")
        try:
            df = yf.download(part, period=period, interval=yf_int, progress=False,
                             auto_adjust=False, group_by="ticker", threads=True)
        except Exception as e:  # noqa: BLE001
            log(f"  bulk download error: {e}")
            continue
        for sym in part:
            try:
                one = df[sym] if isinstance(df.columns, pd.MultiIndex) else df
                one = one[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(one):
                    _cache[(sym, "1d")] = one
            except KeyError:
                pass


def resample_nse(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Build N-minute candles that start at 9:15 every day (NSE session)."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    parts = []
    for day, g in df.groupby(df.index.normalize()):
        origin = day + pd.Timedelta(hours=9, minutes=15)
        parts.append(g.resample(f"{minutes}min", origin=origin).agg(agg))
    out = pd.concat(parts) if parts else df.iloc[0:0]
    return out.dropna(subset=["Open"])


def get_candles(sym: str, interval: str) -> pd.DataFrame:
    symbol = yahoo_symbol(sym)
    if interval in _CUSTOM:
        src, mins = _CUSTOM[interval]
        return resample_nse(_download(symbol, src), mins)
    return _download(symbol, interval)


def load_csv(path: str) -> pd.DataFrame:
    """CSV with columns Date/Datetime, Open, High, Low, Close (Volume optional)."""
    df = pd.read_csv(path)
    date_col = next(c for c in df.columns if c.lower() in ("date", "datetime", "time", "timestamp"))
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    df.columns = [c.capitalize() for c in df.columns]
    if "Volume" not in df:
        df["Volume"] = 0
    return df[["Open", "High", "Low", "Close", "Volume"]]


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    return df.resample(rule).agg(agg).dropna(subset=["Open"])


# ----------------------------------------------------------------- demo data (offline test)
def demo_daily(seed: int = 7, n: int = 1500, start_price: float = 500.0) -> pd.DataFrame:
    """Random-walk daily candles with trends, bases and sharp moves, for testing only."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n)
    price = start_price
    rows = []
    drift = 0.0
    for i in range(n):
        if i % 60 == 0:
            drift = rng.normal(0, 0.004)
        r = rng.random()
        if r < 0.10:                       # exciting / explosive move
            move = rng.choice([-1, 1]) * abs(rng.normal(0.03, 0.01))
            body_frac = 0.8
        elif r < 0.45:                     # base candle
            move = rng.normal(0, 0.003)
            body_frac = 0.25
        else:
            move = rng.normal(drift, 0.012)
            body_frac = 0.6
        o = price
        c = max(1.0, o * (1 + move))
        body = abs(c - o)
        wick = body * (1 / body_frac - 1) / 2 + o * 0.002
        h = max(o, c) + wick * rng.random() * 2
        lo = min(o, c) - wick * rng.random() * 2
        rows.append((o, h, lo, c, int(rng.integers(1e5, 1e6))))
        price = c * (1 + rng.normal(0, 0.002))   # small overnight gaps
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])
