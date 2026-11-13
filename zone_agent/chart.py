"""Candlestick chart with marked demand / supply zones and the trade plan."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

GREEN, RED, BASE = "#1f9d55", "#c62828", "#1f3a68"


def plot_zones(d, zones, title, path, plan=None, last_n=150, max_zones=8):
    d = d.iloc[-last_n:]
    start = d.index[0]
    pos = {ts: i for i, ts in enumerate(d.index)}
    fig, ax = plt.subplots(figsize=(14, 7), dpi=110)

    for i, (ts, r) in enumerate(d.iterrows()):
        col = BASE if r["is_base"] else (GREEN if r["Close"] >= r["Open"] else RED)
        ax.vlines(i, r["Low"], r["High"], color=col, lw=0.9)
        ax.add_patch(Rectangle((i - 0.35, r["body_bot"]), 0.7,
                               max(r["body_top"] - r["body_bot"], 1e-6), color=col))

    n = len(d)
    price = float(d["Close"].iloc[-1])
    show = [z for z in zones if z.valid and z.formed_at >= start]
    show = sorted(show, key=lambda z: abs(z.proximal - price))[:max_zones]
    if plan is not None and plan.zone not in show:
        show.append(plan.zone)
    for z in show:
        x0 = max(0, pos.get(z.formed_at, 0) - (z.legout_idx - z.base_start))
        lo, hi = sorted((z.proximal, z.distal))
        c = GREEN if z.kind == "demand" else RED
        ax.add_patch(Rectangle((x0, lo), n - x0 + 2, hi - lo, color=c,
                               alpha=0.18 if z.tests == 0 else 0.08, lw=0))
        ax.text(n + 2.5, (lo + hi) / 2,
                f"{'DZ' if z.kind == 'demand' else 'SZ'} {z.pattern} score {z.base_score:g}"
                + ("" if z.tests == 0 else f" (tested {z.tests})"),
                va="center", fontsize=7.5, color=c)

    if plan is not None:
        for y, lbl, c in ((plan.entry, "Entry", "#1565c0"), (plan.stop, "SL", RED),
                          (plan.target, "Target", GREEN)):
            ax.axhline(y, color=c, ls="--", lw=1)
            ax.text(1, y, f" {lbl} {y:.2f}", color=c, fontsize=8, va="bottom")

    step = max(1, n // 10)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([str(t)[:10] for t in d.index[::step]], rotation=30, fontsize=8)
    ax.set_xlim(-1, n + 22)
    ax.set_title(title, fontsize=12, loc="left")
    ax.grid(alpha=0.2)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
