# Drop-Base-Rally (DBR) demand zone - rules in `dbr_nifty50_backtest.py`

Source: the user's own chart rules (drop, base, rally with buyer confirmation), applied to NIFTY 50 with Rs 1,00,000 starting capital.

| Step | Rule in code | Value |
|---|---|---|
| Drop | Price fell at least this % over the 5 bars before the base, and that window has a strong bearish candle (body >= 50% of range) | 5% (assumption) |
| Base | 1 or 2 consecutive small-body candles (body < 50% of range) | max 2 |
| Rally start | Next candle is strong bullish and closes above the base high | - |
| Rally confirmation | Within the rally window, green body total > red body total, and at least one green candle with body >= 60% of range | 60% (assumption) |
| Move | Price moves this % above proximal within N bars | 5% within 5 bars (assumption) |
| Zone | proximal = top of base bodies, distal = lowest low of base | - |
| Entry | Limit at proximal on first return into the zone; skip if the bar gaps through | - |
| Stop | distal minus buffer | 0.5% (assumption) |
| Max stop | Skip wide stops | 8% (assumption) |
| Target | 1:2 | - |
| Time stop | Exit after this many sessions | 40 (assumption) |
| Portfolio | Max open trades, one trade per stock, risk per trade | 3, 1% (assumption) |
| Costs | per side | 0.15% (assumption) |

Notes:
- Items marked "assumption" are numbers I chose, not from the user's charts. Change them in the script and compare results.
- Universe: NIFTY 50 list typed from memory (2025). Membership changes over time, and this list has no point-in-time history (survivorship bias).
- Window is fixed (2016-01-01 to 2026-11-14) so repeat runs use the same period.
