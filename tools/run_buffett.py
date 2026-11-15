"""
Run the Buffett agent (DRAFT v1) on the AMFI top-N and write buffett/scores_latest.csv.

python tools/run_buffett.py                       -> quality-only verdicts (no price needed)
python tools/run_buffett.py --prices prices.csv   -> adds P/E, PEG and "near 52W high"
                                                     prices.csv columns: symbol,close[,high52]
Reads local CSV files only. No network, no secrets, no orders.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zone_agent.buffett import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=1000, help="AMFI top-N stocks (default 1000)")
    ap.add_argument("--prices", default=None, help="optional CSV: symbol,close[,high52]")
    ap.add_argument("--out", default="buffett/scores_latest.csv")
    a = ap.parse_args()

    df = run(top=a.top, prices_path=a.prices)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    df.to_csv(a.out, index=False, encoding="utf-8")

    print(f"stocks scored: {len(df)}  (price used: {'yes' if a.prices else 'no - quality only'})")
    for v, icon in (("GREEN", "🟢"), ("YELLOW", "🟡"), ("RED", "🔴"), ("NODATA", "⚪")):
        print(f"  {icon} {v:<7}{int((df['verdict'] == v).sum()):>5}")
    print("written:", a.out)


if __name__ == "__main__":
    main()
