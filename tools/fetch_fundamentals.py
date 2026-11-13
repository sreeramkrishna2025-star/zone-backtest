"""
Download quarterly results (Sales, Net profit, EPS + the date the result was published) for NSE stocks
from NSE's corporate filings (XBRL, available from late 2018).

python tools/fetch_fundamentals.py --chunk 0 --chunks 4      -> data/fundamentals/part_0.csv
Already fetched symbols in that file are skipped (safe to re-run).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
     "Referer": "https://www.nseindia.com/"}
FIELDS = ["symbol", "period_end", "published", "type", "sales", "profit", "eps"]
REV_TAGS = ["RevenueFromOperations", "TotalRevenueFromOperations", "InterestEarned", "Income", "TotalIncome"]
PROFIT_TAGS = ["ProfitLossForPeriod", "ProfitLossForThePeriod", "NetProfitLossForThePeriod",
               "ProfitLossFromOrdinaryActivitiesAfterTax"]
EPS_TAGS = ["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsPerShareAfterExtraordinaryItems",
            "BasicEarningsPerShareBeforeExtraordinaryItems", "BasicEarningsLossPerShare"]


class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.refresh()

    def refresh(self):
        self.s = requests.Session()
        try:
            self.s.get("https://www.nseindia.com", headers=H, timeout=20)
        except Exception:  # noqa: BLE001
            pass

    def get(self, url, as_json=False):
        for k in range(4):
            try:
                r = self.s.get(url, headers=H, timeout=30)
                if r.status_code == 200:
                    return r.json() if as_json else r.text
                if r.status_code in (401, 403):
                    self.refresh()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2 + 3 * k)
        return None


def current_quarter_values(xml: str) -> dict:
    """Values of the current-quarter context (usually 'OneD')."""
    out = {}
    for tag, ctx, val in re.findall(r"<in-[a-z\-]+:([A-Za-z]+)[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]{1,40})<", xml):
        if ctx != "OneD":
            continue
        try:
            out.setdefault(tag, float(val))
        except ValueError:
            pass
    return out


def pick(vals, tags):
    for t in tags:
        if t in vals:
            return vals[t]
    return None


def listing(nse: NSE, sym: str) -> list[tuple]:
    """(period_end, published, is_consolidated, xbrl_url) from the old results API (to Dec 2024)
    and the Integrated Filing API (Mar 2025 onward)."""
    out = []
    old = nse.get(f"https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly",
                  as_json=True) or []
    for x in old:
        if not str(x.get("xbrl", "")).endswith(".xml") or x.get("cumulative") != "Non-cumulative":
            continue
        try:
            out.append((datetime.strptime(x["toDate"], "%d-%b-%Y"), datetime.strptime(x["broadCastDate"][:11], "%d-%b-%Y"),
                        x.get("consolidated") == "Consolidated", x["xbrl"]))
        except Exception:  # noqa: BLE001
            pass
    t = "Integrated%20Filing-%20Financials"
    new = nse.get(f"https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol={sym}&type={t}&page=1&size=100",
                  as_json=True) or {}
    for x in (new.get("data") or []) if isinstance(new, dict) else []:
        if not str(x.get("xbrl", "")).endswith(".xml") or not x.get("broadcast_Date"):
            continue
        try:
            out.append((datetime.strptime(x["qe_Date"].title(), "%d-%b-%Y"),
                        datetime.strptime(x["broadcast_Date"][:11], "%d-%b-%Y"),
                        x.get("consolidated") == "Consolidated", x["xbrl"]))
        except Exception:  # noqa: BLE001
            pass
    return out


def fetch_symbol(nse: NSE, sym: str) -> list[dict]:
    rows = listing(nse, sym)
    if not rows:
        return []
    # use one statement type consistently: the one with more quarters
    nc = len({r[0] for r in rows if r[2]})
    ns = len({r[0] for r in rows if not r[2]})
    want_cons = nc >= ns
    typ = "consolidated" if want_cons else "standalone"
    # first filing per quarter (later ones are revisions)
    first = {}
    for end, pub, cons, url in rows:
        if cons != want_cons:
            continue
        if end not in first or pub < first[end][0]:
            first[end] = (pub, {"xbrl": url})
    out = []
    for end, (pub, x) in sorted(first.items()):
        xml = nse.get(x["xbrl"])
        if not xml:
            continue
        v = current_quarter_values(xml)
        out.append({"symbol": sym, "period_end": end.date().isoformat(), "published": pub.date().isoformat(),
                    "type": typ, "sales": pick(v, REV_TAGS), "profit": pick(v, PROFIT_TAGS), "eps": pick(v, EPS_TAGS)})
        time.sleep(0.15)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=0)
    ap.add_argument("--chunks", type=int, default=1)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--max-minutes", type=float, default=330)
    a = ap.parse_args()
    if a.symbols:
        syms = a.symbols
    else:
        from run_agent import pick_watchlists
        ys = [y for y, _ in pick_watchlists(None, True)]
        syms = sorted({y[:-3] for y in ys if y.endswith(".NS") and not y.upper().startswith(("NIFTY", "CNX"))})
    syms = [s for i, s in enumerate(syms) if i % a.chunks == a.chunk]
    os.makedirs("data/fundamentals", exist_ok=True)
    path = f"data/fundamentals/part_{a.chunk}.csv"
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            done = {r["symbol"] for r in csv.DictReader(f)}
    print(f"chunk {a.chunk}: {len(syms)} symbols, {len(done)} already done")
    nse = NSE()
    t0 = time.time()
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for i, sym in enumerate(syms):
            if sym in done:
                continue
            if (time.time() - t0) / 60 > a.max_minutes:
                print("time limit reached, stopping (re-run to continue)")
                break
            rows = fetch_symbol(nse, sym)
            if not rows:                         # remember empty ones too
                rows = [{"symbol": sym, "period_end": "", "published": "", "type": "none",
                         "sales": None, "profit": None, "eps": None}]
            w.writerows(rows)
            f.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(syms)} {sym}: {len(rows)} quarters, {time.time() - t0:.0f}s")
    print("done")


if __name__ == "__main__":
    main()
