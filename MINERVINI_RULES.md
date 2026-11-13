# Minervini SEPA – complete rulebook (for backtest)

Compiled 2026-11-13 from summaries of *Trade Like a Stock Market Wizard* (2013), *Think & Trade Like a Champion* (2017)
and Minervini interviews. Secondary sources; where they disagree both numbers are noted. [P] = testable with
price/volume data, [F] = needs fundamental data (StockEdge), [J] = judgement / portfolio level.

## 1. Stage 2 – Trend Template (all 8 must pass) [P]
1. Close > 150 SMA and > 200 SMA
2. 150 SMA > 200 SMA
3. 200 SMA rising at least 1 month (better 4-5 months)
4. 50 SMA > 150 SMA and > 200 SMA
5. Close > 50 SMA
6. Close >= 30% above 52-week low (leaders often 100-300%)
7. Close within 25% of 52-week high (best candidates 5-15% below the high)
8. RS rating >= 70, better 80-90+
Stage 1->2: rally of 25-30%+ off the 52-week low, higher highs/lows, more up-weeks on volume.
Stage 3 warning: largest 1-day drop since stage 2 began on heavy volume; 200 SMA flattens.

## 2. Base / VCP [P]
- Base length 3-65 weeks (typical VCP 4-12+ weeks; bases 5-26 weeks common)
- Depth 10-35% normal; avoid > 60% or > 2-3x the market's decline
- 2-6 contractions (usually 2-4), each smaller (about half) e.g. 25% -> 15% -> 8% -> 3%
- Volume dries up: last contraction volume < 50-day average, 1-2 days of very low volume just before pivot
- Down-day volume not heavier than up-day volume; pattern must tighten, not widen
- 1-3 shakeouts inside the base are positive
- Base count: bases 1-2 best, 3 still OK, 4-5 = late stage (avoid)
- Flat base: 4-7 weeks, 10-15% deep, buy above base high

## 3. Entry types [P]
- Pivot: high of the last (tightest) contraction; buy as price crosses it on expanding volume
  (breakout volume ~40-50%+ above average); buy-stop 1-2% above pivot, never chase > 5% above
- 3C / cheat: in a 15-40% deep cup, prior run 25-100% in 3-36 months, above rising 200 SMA;
  a 5-10% pause in the lower/middle part of the base; buy above the pause high (early, small stop)
- Low cheat: same, lower in the base
- Power Play (high tight flag): +100% in < 8 weeks on huge volume, then 3-6 weeks sideways,
  pullback <= 20-25% (tight <= 10% needs no VCP); can be traded on technicals alone
- IPO primary base: >= 3-5 weeks, correction <= 25-35%

## 4. Fundamentals [F]
- Latest quarter EPS up strongly y/y (>= 20-25%, superperformers 30-40%+); prior 2-3 quarters good too
- EPS acceleration (90%+ of big winners); deceleration = red flag
- Sales growth >= 25% q/q, accelerating; 3-year EPS & sales growth >= 15%
- Code 33: EPS, sales and margins all accelerating 3 quarters in a row
- Turnarounds: EPS +100% in last 1-2 quarters
- Estimate revisions up, earnings surprises; small float (95% of winners < 25 mn shares)
- P/E no limit; P/E 2-3x expansion during the move = topping area

## 5. Stops and risk [P/J]
- Stop below the low of the last contraction / cheat area
- Absolute max loss 10%; average loss target 5-7% (3-4% in good markets, 5-6% in hard ones)
- Loss should be about half the average gain (keep win/loss ratio >= 2:1)
- Risk 1-2.5% of equity per trade (pilot buys smaller)
- Squat (breakout falls back into range on low volume): keep original stop, allow 1-2 (up to 10) days

## 6. Moving the stop / selling into weakness [P]
- At 2-3x risk in profit: stop to break-even (a good gain never becomes a loss)
- After break-even trail with the 50 SMA (close below = sell rest or stop below that day's low)
- High-volume down day closing below 20 SMA: tighten stop to below that day's low
- Largest daily/weekly decline since the move began (heavy volume) = sell signal
- Down-day volume > up-day volume, gap downs = warning
- Time stop: stock goes nowhere -> sell (average hold ~20 trading days)

## 7. Selling into strength [P]
- Sell half at a profit (early bull market: half at 2R and stop to break-even; some sources: half at 1R)
- Climax: parabolic run (e.g. +25-50% in 1-3 weeks, +100% in < 2 weeks), mostly up days,
  momentum/exhaustion gaps, biggest up day then biggest down day on highest volume -> raise stop
  to below each day's low / sell into strength
- Don't sell into strength early in a fresh stage-1->2 breakout (could be a Power Play)
- Hard market: profits at 10-12% instead of 15-20%, stops 5-6% instead of 7-8%

## 8. Position sizing and exposure [J]
- 4-6 stocks (large accounts 10-12, never > 20); about 25% per position, max 25-50%
- Progressive exposure: start with pilot buys, add only after winners, cut size during losing streaks
- Ease in after bear markets

## 9. Market [P]
- 90% of superperformers start as the market comes out of a correction / bear market
- New 52-week highs expanding vs lows = healthy; leaders breaking down = reduce exposure
- 3-10 industry groups lead; focus on top 4-5 sectors; 60%+ of winners were part of a group move
- Leaders can top 6-12 months before the index

## FINAL backtest result (2026-11-13, sepa_backtest.py defaults)
Universe: all watchlists (~1100 NSE/BSE stocks), only stocks with >= Rs 50 lakh/day traded value and no data gaps.
Setups VCP + Flat + Power Play + Cheat/3C; stop max 10%; 50 SMA trailing from day 1; break-even at +3R;
20 SMA heavy-volume tighten; largest-decline exit; climax tight stop; 20-day time stop; 6 positions, 1.25% risk,
0.25% round-trip cost.
Portfolio 2010-2026: CAGR 19.4% (NIFTY 9.7%), max drawdown -22.5%; 2010-17 17.1%, 2018-26 21.6%.
Removed after testing (worse): half sells at 1R/2R, market filter, tough-market rules, progressive exposure,
base-count limit, strict cheat (50>200 SMA). Liquidity matters: with no liquidity limit CAGR was 24.8%
(mostly from illiquid small stocks), with Rs 1 crore limit 14.2%.
Known limits: today's stock list only (survivorship bias), Yahoo data, no fundamentals yet, 2024-26 weak (+11%, -1%, -7% YTD).
