# Final zone rules (DBR, RBR, RBD, DBD)

Locked by the user. Do not change without an explicit new instruction.

## Base candle (all patterns)
- Body <= 40% of the candle range.
- Applies to base candles only.
- Base is 1 or 2 candles. More than 2 is rejected.

## Legin and legout candles (all patterns)
- Total wick (upper + lower) <= 10% of the candle range.
- This applies to both legin and legout candles of DBR, RBR, RBD and DBD.

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
- Legin and legout each follow the 10% total wick rule above. The base rule does not apply, because there is no base.
- Exceptional zones are marked only on monthly, quarterly, halfyear and yearly candles.
- Normal zones (with a base) are marked on daily, weekly, monthly, quarterly, halfyear and yearly candles.
- Drop start and closing rules (DBR, RBD) apply the same way as for normal zones.
- Demand exceptional zone lines: upper line = legout open. Lower line = the higher of the legin low and the legout low.
- Setup is valid only if the legout candle closes above the High of the legin candle (same timeframe).

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
- Constants in `zone_agent/config.py`: STRICT_BASE_BODY_MAX (0.40), STRICT_WICK_MAX (0.10).

## Alerts (display only, no trading effect)
- Instant alerts (Telegram + Zones tab) for monthly, quarterly, halfyear and yearly zones only. Daily and weekly zones appear in the Zones tab only.
- New-zone alerts are sent as one daily digest, not instantly.
- Touch alerts and invalid alerts are sent instantly.
- Touch alerts only for zones that are marked "watch" in the app.
- Distance filter: an instant alert is sent only if the price is within 5% of the zone's proximal level. Zones farther away appear in the Zones tab only.
- Alerts are sent only for stocks in the user's app watchlist. Stocks outside the watchlist never trigger alerts.
- Alerts never say buy or sell.
