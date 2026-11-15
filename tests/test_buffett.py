"""
Plain-assert tests for zone_agent/buffett.py (no pytest needed):   python tests/test_buffett.py
Uses made-up quarterly tables only.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zone_agent.buffett import score_symbol  # noqa: E402


def table(eps, sales, profit, start="2019-03-31"):
    ends = pd.date_range(start, periods=len(eps), freq="QE")
    return pd.DataFrame({"period_end": ends, "published": ends + pd.Timedelta(days=30),
                         "eps": eps, "sales": sales, "profit": profit})


def grower(n=16):          # EPS 10% up each quarter-year, steady margin
    eps = [10 * 1.025 ** i for i in range(n)]
    sales = [1000 * 1.02 ** i for i in range(n)]
    return table(eps, sales, [s * 0.12 for s in sales])


def test_steady_grower_is_green_without_price():
    r = score_symbol(grower())
    assert r["verdict"] == "GREEN", r
    assert r["up_years"] == "3/3" and r["loss_quarters"] == 0 and not r["price_used"]
    assert "price not checked" in r["reasons"]


def test_loss_making_is_red():
    g = grower()
    g.loc[g.index[-3:], ["eps", "profit"]] = [-1.0, -50.0]
    assert score_symbol(g)["verdict"] == "RED"


def test_short_history_is_na():
    r = score_symbol(grower(6))
    assert r["verdict"] == "NODATA" and "consecutive quarters" in r["reasons"]


def test_gap_in_quarters_cuts_history():
    g = grower(16).drop(index=[7]).reset_index(drop=True)      # a missing quarter in the middle
    r = score_symbol(g)
    assert r["verdict"] == "NODATA" or r["up_years"] != "3/3"      # old part must not be stitched across the gap


def test_expensive_price_lowers_score_and_flags_high():
    g = grower()
    cheap = score_symbol(g, price=150, high52=300)
    dear = score_symbol(g, price=3000, high52=3050)
    assert cheap["price_used"] and dear["price_used"]
    assert dear["score_pct"] < cheap["score_pct"]
    assert dear["near_high"] and not cheap["near_high"]
    assert dear["verdict"] != "GREEN"


def test_stale_data_is_na():
    g = grower()
    r = score_symbol(g, asof=g["published"].max() + pd.Timedelta(days=400))
    assert r["verdict"] == "NODATA" and "old data" in r["reasons"]


def test_nan_row_does_not_crash():
    g = grower()
    g.loc[g.index[-1], "eps"] = np.nan
    r = score_symbol(g)
    assert r["verdict"] in {"GREEN", "YELLOW", "RED", "NODATA"}


def test_standalone_is_flagged():
    g = grower()
    g["type"] = "standalone"
    r = score_symbol(g)
    assert r["result_type"] == "standalone" and "standalone results only" in r["reasons"]


def test_type_change_cuts_history():
    g = grower(16)
    g["type"] = ["standalone"] * 8 + ["consolidated"] * 8      # old half is a different basis -> not compared
    r = score_symbol(g)
    assert r["result_type"] == "consolidated"
    assert r["verdict"] == "NODATA" or r["up_years"] == "1/1"


if __name__ == "__main__":
    fns =[v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("OK ", f.__name__)
    print(f"{len(fns)} tests passed")
