# LMS zone strategy - rules used in `lms_zone_backtest.py`

Source: GTF Learning Management System (LMS) book. The book describes the
strategy with charts and judgement ("fresh", "tested", "origin"). The table
below is how each rule is coded, so the backtest is repeatable. Where the book
gives no number, the value is an assumption and is marked as such.

| Book rule | Coded as | Value / note |
|---|---|---|
| Weekly trend in favour | Weekly close > weekly SMA(50), and that SMA is rising over 4 weeks | Assumption: the book gives no formula |
| Location = higher-TF demand zone | Fresh weekly demand zone that overlaps the daily zone | Weekly zone must not have been closed below its distal line |
| Execution = daily demand zone | Rally-base-rally: 1-2 small-body base candles, then an exciting bullish leg-out that closes above the base | Max 2 base candles (book: base candles rule) |
| Leg-out must prove itself | Price moves 5% (daily) / 10% (weekly) above the proximal line within 5 / 4 bars | Assumption on the size; zone becomes known only when the move happens (no look-ahead) |
| Enter on the zone, not at a random price | Limit at proximal line on first touch; if the bar gaps through the zone, skip | Book: "no entry far away" |
| "If there is no zone, no trade" | Entry only when a zone is present and the weekly location and trend are OK on the touch day | |
| Stop-loss below the zone | Distal line minus 0.5% | Book: "plan on zone 1 = exit on zone 1" |
| Stop too wide = capital blocked | Skip if stop is more than 8% below entry | Assumption |
| Target 1:2 | Entry + 2 x (entry - stop) | Book: focus on 1:2 first |
| Max 3 trades at a time | At most 3 open positions; one trade per stock | Book: "enter a maximum of only 3 trades" |
| Risk control | 1% of equity at risk per trade; size capped at equity / 3 per position | Config: `RISK_PCT`, `MAX_CONCURRENT` |
| Time stop | Exit at close after 40 sessions if neither stop nor target hit | Assumption: book gives the idea, not the number |
| Costs | 0.15% per side (brokerage, STT, charges, slippage) | Assumption |

## Not coded in v1 (open points)

- **Trailing stop with weekly demand zones** (book's long-term hold rule). v1 exits at the 1:2 target. Adding it means changing the exit logic.
- **Scalping mode and 40-40-20 investing allocation.** Different trade styles; not part of this test.
- **Sector and index confirmation** (book: more sectors and high-weightage stocks in zone = stronger reaction). Not coded.
- **Zone scoring** (repo's `backtest_setup.py` already has a trade score). Not used here, to keep this strategy separate.
- **Universe**: `watchlists.json` NSE entries, not an official Nifty 1000 constituent file. The sandbox cannot reach NSE or Yahoo, so the list was not checked against the official Nifty 1000 constituents.

## Outputs

`reports/lms/trades.csv` (one row per trade) and `reports/lms/summary.txt` (win rate, average and median R, profit factor, CAGR, max drawdown, exit reasons, NIFTY benchmark CAGR when available).
