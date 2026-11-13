"""Universe loader used by the backtests (stock list in watchlists.json)."""
import json
import os

from zone_agent import data as D

WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlists.json")


def load_watchlists() -> dict:
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        return {k: [x for x in v.split(",") if x] for k, v in json.load(f).items()}


def pick_watchlists(names, all_lists) -> list[tuple[str, str]]:
    """Returns [(yahoo_symbol, list_name)] without duplicates."""
    wl = load_watchlists()
    chosen = list(wl) if all_lists else [k for k in wl for n in (names or []) if n.lower() in k.lower()]
    out, seen = [], set()
    for name in dict.fromkeys(chosen):
        for tv in wl[name]:
            y = D.tv_to_yahoo(tv)
            if y and y not in seen:
                seen.add(y)
                out.append((y, name))
    return out
