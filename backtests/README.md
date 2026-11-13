# Backtest results (kept for building the final agent)

`redlist_tradingview_2026-11-05.csv` - TradingView Strategy Tester, script
`tradingview/zone_setup_backtest.pine`, Red list (28 stocks) + M&M, CANBK, CGPOWER, AIAENG.
Each chart time frame uses the next bigger one for trend (D->W, W->M, M->Q, Q->H).
Rules: 10% move in/out, max 2 base candles, score 7+, SMA clock + structure trend,
entry proximal, SL distal - 0.5%, target 2R, first touch only, 1% risk, 0.05% commission.

| Time frame | Trades | Win-rate | Sum of P&L % (32 stocks) |
|---|---|---|---|
| Daily     | 182 | 43.4% | +68.2 |
| Weekly    | 164 | 41.5% | +39.6 |
| Monthly   |  59 | 32.2% |  -5.0 |
| Quarterly |  23 | 43.5% |  +6.1 |
| All       | 428 | 41.1% | +108.8 |

## 2026-11-06 - user's exact setup (Daily 7/7 + Weekly trend + Monthly location + Weekly target >= 2R)
`backtest_setup.py`, all watchlists (~1,245 stocks), data 1999-2026, 1% risk, one trade per stock at a time.

| Version | Trades | Win-rate | Avg R | Total R | Profit factor | Max DD |
|---|---|---|---|---|---|---|
| BUY + SELL, limit entry (type 1) | 822 | 24.7% | +0.41 | +336 | 1.53 | -32% |
| BUY only, limit entry (type 1)   | 423 | 26.5% | +0.69 | +294 | 1.94 | -16% |
| BUY only, type 2 entry           |  73 | 15.1% | +0.46 |  +34 | 1.53 | -19% |
| BUY only, type 3 (confirmation)  |  60 | 33.3% | +1.36 |  +82 | 3.11 | -12% |
| BUY only, Daily set, score 6+, limit  | 730 | 26.3% | +0.70 | | 1.95 | -24% |
| **BUY only, Daily set, score 6+, type 3** | **91** | **39.6%** | **+1.31** | | **3.24** | **-8%** |
| BUY only, Weekly set, score 7, limit  | 115 | 17.4% | +0.52 | | 1.63 | -26% |
| BUY only, Weekly set, score 7, type 3 |  14 |  7.1% | -0.32 | | 0.64 | -10% |
| BUY only, Weekly set, score 6+, limit | 243 | 23.0% | +0.63 | | 1.81 | -30% |
| BUY only, Weekly set, score 6+, type 3|  35 | 20.0% | -0.12 | | 0.84 | -17% |

Weekly set = Weekly execution, Monthly trend, Quarterly location, Monthly target.

## Box first (Monthly box up to 5 base, no garbage filter), BUY only, Daily execution
| Version | Trades | Win | Avg R | PF | Max DD (sequential) |
|---|---|---|---|---|---|
| score 7, limit | 1166 | 29.1% | +0.66 | 1.91 | -37% |
| score 7, type 3 | 168 | 29.2% | +0.57 | 1.88 | -17% |
| score 6+, limit | 1960 | 29.6% | +0.81 | 2.14 | -38% |
| score 6+, type 3 | 293 | 32.8% | +0.55 | 1.87 | -12% |
| + box proof 10%: score 7, limit | 1040 | 28.4% | +0.71 | 1.98 | -34% |
| + box proof 10%: score 7, type 3 | 160 | 29.4% | +0.68 | 2.04 | -19% |
| + box proof 10%: score 6+, limit | 1736 | 28.8% | +0.85 | 2.17 | -37% |
| + box proof 10%: score 6+, type 3 | 285 | 32.3% | +0.61 | 1.95 | -12% |

## 2026-11-06 (afternoon) – user chart feedback tests (BUY, Type 3, score 6+, box proof 10%)
A = Monthly box, Weekly trend (SMA+structure) | B = +Quarterly boxes | C = Monthly trend | D = Monthly structure only | E = Weekly structure only | F = Weekly+Monthly structure
| Test | Trades | Win | Avg R | PF | 2024+ avg R |
|---|---|---|---|---|---|
| A | 287 | 32.8% | +0.61 | 1.96 | +0.05 |
| B | 335 | 30.4% | +0.45 | 1.67 | -0.07 |
| C | 633 | 26.1% | +0.16 | 1.23 | -0.18 |
| D | 848 | 25.5% | +0.15 | 1.21 | -0.17 |
| E | 606 | 26.2% | +0.31 | 1.42 | -0.29 |
| F | 288 | 27.8% | +0.34 | 1.47 | -0.03 |
| **A + clean monthly box (<=3 of 10 candles before legin overlap box)** | 147 | 40.8% | +1.03 | 2.82 | +0.29 |
Curve position (36-month range) did not separate good from bad trades.

## 2026-11-13 – "latest best zone + big R:R" idea (BUY, score 6+, box proof 10%, clean monthly box <=3)
| Test | Trades | Win | Avg R | PF | 2015+ avg R | 2024+ avg R | DD |
|---|---|---|---|---|---|---|---|
| 1 Type3 (current agent) | 146 | 41% | +1.05 | 2.88 | +1.44 | +0.36 | -14R |
| 2 Type3 latest zone only | 88 | 41% | +0.71 | 2.24 | +0.90 | +0.54 | -10R |
| 3 Type3 R:R >= 3 | 121 | 37% | +1.04 | 2.76 | +1.52 | +0.14 | -16R |
| 4 Type3 latest + R:R >= 3 | 76 | 36% | +0.56 | 1.89 | +0.77 | +0.31 | -13R |
| 5 Limit latest | 649 | 29% | +0.71 | 2.01 | +0.40 | +0.37 | -24R |
| 6 Limit latest + R:R >= 3 | 590 | 28% | +0.72 | 2.01 | +0.36 | +0.32 | -23R |

## 2026-11-13 – Minervini SEPA backtest (Trend Template + RS>=70 + VCP breakout), all watchlists 1109 stocks
Entry next open after breakout close on 1.4x volume; SL tight low max 8%; break-even after +2R; trailing exit.
| Version | Trades | Win | Avg/trade | PF | 2015-2023 avg | 2024+ avg |
|---|---|---|---|---|---|---|
| TT + exit close<50 SMA | 3475 | 28.9% | +5.10% | 2.35 | +4.33% | -0.77% |
| TT + exit close<20 SMA | 3809 | 36.7% | +2.07% | 1.67 | +2.27% | -0.39% |
| TT + exit close<10 EMA | 3949 | 37.5% | +1.24% | 1.49 | +1.36% | -0.43% |
| NO trend template, 50 SMA exit | 18068 | 27.9% | +2.92% | 1.90 | +2.75% | -0.70% |
| TT 50 SMA + NIFTY>200SMA | 2713 | 28.1% | +3.90% | 2.02 | +4.29% | -1.89% |
| TT 20 SMA + NIFTY>200SMA | 2952 | 36.4% | +1.80% | 1.58 | +2.21% | -0.94% |
Trade lists in backtests/minervini/. Old Yahoo data has a few bad prices (e.g. KPIL 2005 +2400%), removed in the 2015-2023 column (pct>=300%).

### Minervini – what separates winners from losers (TT + 50 SMA exit, 2010+, 3138 trades)
- RS 90+ : +5.0%/trade vs RS <=80 : +2.5%
- Base depth 15-25% best; very shallow (<10%) or very tight (<5%) bases ~0%
- Breakout volume 1.8-4x best; >4x (climax) weaker
- Within 10% of 52-week high better than 10-25% below
- Breadth (% stocks > 200 SMA) did NOT clearly fix 2024-25
- "Clean" filter (depth 12-25, tight>=5, vol 1.8-4x, <=10% from high): 1014 trades, 31% win, +4.98%, PF 2.37; 2015-23 +5.10% PF 2.45; 2024+ -0.24% PF 0.94
Chart review (charts/): winners = first breakout from a long flat base with all averages rising (RRKABEL, GABRIEL, MAHASTEEL);
losers = late-stage breakout after a big run with 50 SMA flattening (STOVEKRAFT, EMAMIPAP), tangled flat averages (JAYNECOIND),
loose wide base (BLUESTARCO), and a winner that gave back all gains (JKTYRE).

## 2026-11-13 – SEPA + Minervini fundamentals (NSE point-in-time results), portfolio 2020-2026
| Filter | Trades (all yrs) | Avg/trade | PF | 2024+ avg | Portfolio CAGR 2020-26 | Max DD |
|---|---|---|---|---|---|---|
| none (all stocks) | 2264 | +1.68% | 1.53 | -0.14% | 25.0% | -22.5% |
| has fundamentals (NSE, same universe) | 996 | +1.68% | 1.52 | -0.23% | 23.6% | -19.7% |
| EPS >= 25% | 570 | +1.89% | 1.60 | -0.42% | 11.7% | -31.2% |
| EPS >= 25% and Sales >= 25% | 292 | +2.42% | 1.75 | +0.69% | 12.3% | -26.8% |
| EPS >= 25% accelerating | 325 | +1.85% | 1.55 | -1.13% | 6.7% | -33.2% |
| Code 33 | 12 | +0.64% | 1.15 | -0.15% | 0.2% | -4.6% |
| EPS >= 20% or turnaround | 609 | +1.91% | 1.59 | -0.61% | 13.7% | -37.8% |
| EPS > 0 and Sales >= 15% | 480 | +1.75% | 1.53 | -0.58% | 15.1% | -25.9% |
Fundamental filters raise per-trade quality a little (best: EPS+Sales 25%) but cut the number of trades so much that
the 6-stock portfolio sits in cash and CAGR falls. Code 33 almost never happens in this data.
