# zone-backtest

Public backtest lab for Indian stock setups (NSE/BSE, daily data from Yahoo Finance, quarterly results from NSE filings).
Only setups that pass here are moved into the live alert agent (kept private).

- `sepa_backtest.py` – Mark Minervini SEPA (trend template, VCP, flat base, power play, cheat) with portfolio simulation
- `backtest_setup.py` – Demand & Supply zone setup (Daily 6/7 score, Weekly trend, Monthly box, Type 3 entry)
- `minervini_backtest.py` – simple trend template + VCP breakout study
- `zone_agent/fundamentals.py` + `data/fundamentals/` – point-in-time quarterly Sales / Profit / EPS
- Rules: `MINERVINI_RULES.md`; results: `backtests/README.md`

Run any backtest from the Actions tab (workflow_dispatch). Not investment advice.
