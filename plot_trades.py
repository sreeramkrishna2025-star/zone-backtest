"""Draw daily charts of backtest trades so they can be checked by eye.

python plot_trades.py RRKABEL:2026-04-17:2026-09-07:1455:1338.6 ...
Each argument = SYMBOL:ENTRY_DATE:EXIT_DATE:ENTRY:SL  ->  charts/SYMBOL_ENTRYDATE.png
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from zone_agent import data as D  # noqa: E402


def plot(sym, ed, xd, entry, sl):
    df = D.get_candles(sym, "1d")
    df = df[df["Volume"] > 0]
    c = df["Close"]
    s50, s150, s200 = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    e, x = pd.Timestamp(ed), pd.Timestamp(xd)
    w = df[(df.index >= e - pd.Timedelta(days=200)) & (df.index <= x + pd.Timedelta(days=45))]
    fig, (ax, av) = plt.subplots(2, 1, figsize=(13, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [4, 1]})
    pos = range(len(w))
    for i, (_, r) in zip(pos, w.iterrows()):
        col = "#1a9850" if r.Close >= r.Open else "#d73027"
        ax.plot([i, i], [r.Low, r.High], color=col, lw=0.8)
        ax.add_patch(plt.Rectangle((i - 0.35, min(r.Open, r.Close)), 0.7,
                                   max(abs(r.Close - r.Open), 1e-6), color=col))
        av.bar(i, r.Volume, color=col, width=0.7)
    for s, name, col in ((s50, "50", "blue"), (s150, "150", "orange"), (s200, "200", "black")):
        ax.plot(list(pos), s.reindex(w.index).to_numpy(), color=col, lw=1, label=f"SMA {name}")
    ie = w.index.searchsorted(e)
    ix = min(w.index.searchsorted(x), len(w) - 1)
    ax.axvline(ie, color="green", ls="--", lw=1)
    ax.axvline(ix, color="red", ls="--", lw=1)
    ax.axhline(entry, color="green", lw=0.8)
    ax.axhline(sl, color="red", lw=0.8)
    ax.set_title(f"{sym}  entry {ed} @ {entry}  SL {sl}  exit {xd}")
    ax.legend(loc="upper left")
    ticks = list(range(0, len(w), max(1, len(w) // 10)))
    av.set_xticks(ticks)
    av.set_xticklabels([w.index[i].strftime("%d-%b-%y") for i in ticks], rotation=30)
    os.makedirs("charts", exist_ok=True)
    fig.tight_layout()
    fig.savefig(f"charts/{sym}_{ed}.png", dpi=80)
    plt.close(fig)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        s, ed, xd, en, sl = arg.split(":")
        try:
            plot(s, ed, xd, float(en), float(sl))
            print("saved", s, ed)
        except Exception as ex:  # noqa: BLE001
            print("error", s, ex)
