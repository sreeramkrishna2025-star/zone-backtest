# Final zone rules (DBR, RBR, RBD, DBD)

Locked by the user. Do not change without an explicit new instruction.

## Base candle (all patterns)
- Body <= 40% of the candle range.
- Applies to base candles only.
- Base is 1 or 2 candles. More than 2 is rejected.

## Legin and legout candles (all patterns)
- Legin: total wick (upper + lower) <= 30% of the candle range. (User, 18 Nov 2026.)
- Legout: total wick (upper + lower) <= 20% of the candle range. (Changed from 10% by the user on 18 Nov 2026.)
- This applies to the legin and legout candles of DBR, RBR, RBD and DBD.

## Drop start (DBR)
- Drop start = the first red candle of the drop run.
- Legout close must be above the High of that first red candle (same timeframe).

## Demand zones
- DBR: drop candle (legin) and legout as above. Drop start rule applies.
- RBR: rally candle (legin) and legout as above.

## Supply zones
- RBD: rally candle (legin) and drop candle (legout) as above.
- RBD rally start = the first green candle of the rally run. Legout close must be below the Low of that first green candle (same timeframe).
- DBD: drop candle (legin) and drop candle (legout) as above.

## Exceptional zones
- An exceptional zone is a legin candle followed directly by a legout candle, with no base candle in between.
- Exceptional zones are only DBR (drop legin, rally legout) and RBD (rally legin, drop legout). There is no exceptional RBR or DBD. (User instruction, 18 Nov 2026.)
- Legin follows the 30% and legout the 20% total wick rule above. The base rule does not apply, because there is no base.
- Exceptional zones are marked only on monthly, quarterly, halfyear and yearly candles.
- Normal zones (with a base) are marked on daily, weekly, monthly, quarterly, halfyear and yearly candles.
- Drop start and closing rules (DBR, RBD) apply the same way as for normal zones.
- Demand exceptional zone lines: upper line = legout open. Lower line = the higher of the legin low and the legout low.
- Setup is valid only if the legout candle closes above the High of the legin candle (same timeframe).
- Supply exceptional zone lines (mirror of demand): lower line = legout open. Upper line = the higher of the legin high and the legout high.
- Supply setup is valid only if the legout candle closes below the Low of the legin candle (same timeframe).
- Exceptional zone distal: demand = lower line; supply = upper line. Price trading through the distal line (a break) makes the setup invalid, the same as for normal zones.

## Closing rule (all zones)
- A setup is final only after the legout candle closes. Zones are formed only from closed candles.

## Freshness and invalidation
- Fresh = price has not touched the zone since the legout period closed (any timeframe, daily data).
- Demand touch = candle low reaches the upper level (proximal).
- Supply touch = candle high reaches the lower level (proximal).
- A break of the setup low needs a touch first, so it adds no separate rule.

## Entry and exit (backtest only)
- Entry: first touch of the zone (proximal level).
- Fill price: if the open is at or below the proximal level (inside the zone or below it), fill at the open. If the open is above the proximal level, fill at the proximal level.
- If the open is below the distal level, the zone is broken. The setup is invalid and no trade is taken.
- Stop: zone distal. Target: 1:2 reward/risk.
- Setup break: if after the first touch, price trades below the distal level during the day (not via a gap), the setup is invalid. If a trade is open and the stop is hit, the setup is no longer used for any further trade.
- Same-bar stop and target: stop first.

## Implementation
- `zone_agent/zones.py` `find_zones(..., require_high_break=True, strict_shapes=True)`
- Constants in `zone_agent/config.py`: STRICT_BASE_BODY_MAX (0.40), STRICT_LEGIN_WICK_MAX (0.30), STRICT_WICK_MAX (0.20, legout).

## Alerts (display only, no trading effect)
- Instant alerts (Telegram + Zones tab) for monthly, quarterly, halfyear and yearly zones only. Daily and weekly zones appear in the Zones tab only.
- New-zone alerts are sent as one daily digest, not instantly.
- Touch alerts and invalid alerts are sent instantly.
- Touch alerts only for zones that are marked "watch" in the app.
- Distance filter: an instant alert is sent only if the price is within 5% of the zone's proximal level. Zones farther away appear in the Zones tab only.
- Alerts are sent only for stocks in the user's app watchlist. Stocks outside the watchlist never trigger alerts.
- Alerts never say buy or sell.

## Setup rule: "Zone test -> new zone" (user, 18 Nov 2026) — DRAFT, code not written yet
Telegram alert (display only, never says buy or sell). No ranking of timeframes: whichever Monthly/Quarterly/Halfyear/Yearly zone was tested is reported.
- **Tested zone:** a Monthly, Quarterly, Halfyear or Yearly zone. "Tested" = the candle touched the zone's proximal line, OR a candle closed inside the zone (either one is enough). The zone must still be valid (distal not broken).
- **New zone:** after the test, a new FRESH zone forms on ANY timeframe (daily, weekly, monthly, quarterly, halfyear, yearly), by the normal zone rules (closed legout candle). For the demand case: DBR, RBR (or exceptional DBR). Example: TCS touched a Quarterly demand zone, price moved up, and a Weekly DBR formed.
- **Alert text (example):** "TCS: Quarterly demand zone test hua, upar gaya. Weekly DBR naya zone bana, fresh. Proximal 2170, Distal 2145." Shows: stock, tested zone timeframe, new zone timeframe + pattern, fresh status, proximal, distal.
- **Closing is compulsory:** the new zone counts only after its legout candle closes. So a Weekly zone alert comes after the week closes, a Daily zone alert after that day's close.
- **No separate price condition (user, 18 Nov 2026):** a new zone exists only after its legout closed above it (demand), so price can only be above the zone or back inside it. The one bad case, distal broken, is already covered by the general invalid rule: no alert. Alert goes out the same evening the zone's legout closes (daily: that day's close; weekly: Friday close; monthly and bigger: last trading day of the period), before the next open.
- **Once only:** one alert per (tested zone + new zone) pair, never repeated.
- **Which stocks (user, 18 Nov 2026):** ALL stocks of the Nifty 500 + Nifty 1000 lists (not only the user's watchlist). No stock outside these lists ever triggers this alert. (This alert type is an exception to the general 'watchlist only' alert rule above.)
- **Extra line in the alert (user, 18 Nov 2026):** "Upar ka supply": the nearest fresh supply zone above the current price, ONLY from Monthly, Quarterly, Halfyear or Yearly zones. Shown as the zone code + its proximal price. Codes: MSZ, QSZ, HSZ, YSZ (Monthly/Quarterly/Halfyear/Yearly Supply Zone); MDZ, QDZ, HDZ, YDZ for demand. Example: "Upar ka supply: QSZ, proximal 2600 (8% door)". The distance % = (proximal - current price) / current price x 100 (user, 18 Nov 2026).

### Supply side (mirror of the demand case, draft)
- **Tested zone:** a Monthly, Quarterly, Halfyear or Yearly SUPPLY zone. Tested = the candle's High reached the zone's proximal (lower) line, OR a candle closed inside the zone. Zone must still be valid (distal = upper line, not broken).
- **New zone:** after the test, price moved down and a new FRESH SUPPLY zone formed on ANY timeframe (RBD, DBD, or exceptional RBD), legout closed (closing rule).
- **No separate price condition:** same as the demand case. Distal (upper line) broken = invalid, no alert.
- **Alert text (example):** "TCS: Quarterly supply zone test hua, neeche gaya. Weekly RBD naya zone bana, fresh. Proximal 2480, Distal 2510." Plus extra line (mirror): "Neeche ka demand: MDZ, proximal 2290 (4% neeche)" = nearest fresh demand zone below the price, ONLY Monthly to Yearly, zone code + proximal price + distance %.
- Closing is compulsory, once only per (tested zone + new zone) pair, and only stocks from the user's Nifty 500 + 1000 watchlist: same as the demand case.
- Invalid rule applies everywhere: if price trades through a zone's distal line, that zone is invalid (tested zone and new zone alike).
- Normal zone rules above (base, legin 30%, legout 20%, closing rule, exceptional) are unchanged.
