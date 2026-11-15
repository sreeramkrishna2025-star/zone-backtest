"""
Concall agent runner (DRAFT v1). Cheap by design: ONE announcements call, then it only reads transcripts of
shortlisted stocks (Buffett GREEN/YELLOW) that are new. Nothing new -> exits in seconds.

python tools/run_concall.py --probe                 first live run: shows what NSE really returns (field names)
python tools/run_concall.py --dry-run               lists the new transcripts it WOULD read, reads none
python tools/run_concall.py                         the real run (last 4 days, max 15 transcripts)
python tools/run_concall.py --file call.pdf --symbol TCS     score ONE local pdf/txt, no network
Options: --days N  --max N  --finbert (heavy, needs transformers+torch)  --shortlist buffett/scores_latest.csv
No secrets, no AI key, no orders.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zone_agent import concall as C  # noqa: E402

RESULTS = "concall/results.csv"


def shortlist(path: str) -> set[str]:
    if not os.path.exists(path):
        sys.exit(f"shortlist not found: {path}\nrun  python tools/run_buffett.py  first (or pass --shortlist).")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    return {r["symbol"] for r in rows if r.get("verdict") in ("GREEN", "YELLOW")}


def read_local(path: str) -> str:
    data = open(path, "rb").read()
    return C.extract_text(data) if path.lower().endswith(".pdf") else data.decode("utf-8", "ignore")


def show(res: dict, symbol: str):
    print(f"{symbol}: {res['icon']} {res['verdict']}  tone={res.get('tone')}  words={res.get('words')}  "
          f"pos={res.get('pos')} neg={res.get('neg')}  unc/1k={res.get('unc_per_1k')}  [{res.get('method', 'rules')}]")
    if res.get("tone_change") is not None:
        print(f"   vs previous call: {res['tone_prev']} -> {res['tone']} ({res['tone_change']:+})")
    if res.get("note"):
        print("   note:", res["note"])
    for g in res.get("guidance", []):
        print("   guidance:", g)
    for k in res.get("key_lines", []):
        print("   line:", k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--symbol", default="TEST")
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--max", type=int, default=15, help="max transcripts per run (keeps the run short)")
    ap.add_argument("--finbert", action="store_true")
    ap.add_argument("--shortlist", default="buffett/scores_latest.csv")
    ap.add_argument("--results", default=RESULTS)
    a = ap.parse_args()

    rows = C.load_results(a.results)

    if a.file:                                                       # offline: one local file
        res = C.analyse(read_local(a.file), C.previous_tone(rows, a.symbol), a.finbert)
        show(res, a.symbol)
        return

    syms = set() if a.probe else shortlist(a.shortlist)              # fail fast, before any network call
    today = datetime.now()
    nse = C.NSE()
    recs = C.fetch_announcements(nse, today - timedelta(days=a.days), today)
    print(f"announcements fetched ({a.days} days): {len(recs)}")

    if a.probe:
        tr = [r for r in recs if "transcript" in " ".join(map(str, r.values())).lower()]
        print(f"records mentioning 'transcript': {len(tr)}")
        for r in (tr or recs)[:3]:
            print("keys:", sorted(r.keys()))
            print({k: str(v)[:80] for k, v in r.items()})
        return

    done = {r["pdf_url"] for r in rows}
    todo = C.pick_transcripts(recs, syms, done)
    print(f"shortlisted stocks: {len(syms)} | new transcripts: {len(todo)}")
    if not todo:
        print("nothing new - done.")
        return
    todo = todo[: a.max]
    if a.dry_run:
        for t in todo:
            print("  would read:", t["symbol"], t["announced"], t["pdf_url"])
        return

    added = 0
    for t in todo:
        pdf = nse.get(t["pdf_url"], raw=True)
        text = C.extract_text(pdf) if pdf else ""
        if not text.strip():
            print(f"  {t['symbol']}: could not read pdf, skipped (will retry next run)")
            continue
        res = C.analyse(text, C.previous_tone(rows, t["symbol"]), a.finbert)
        rows.append(C.to_row(t["symbol"], t["announced"], t["pdf_url"], res))
        show(res, t["symbol"])
        added += 1
        time.sleep(1)
    if added:
        C.save_results(a.results, rows)
        print(f"saved {added} new row(s) -> {a.results}")


if __name__ == "__main__":
    main()
