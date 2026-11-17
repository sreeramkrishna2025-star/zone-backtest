# Final demand zone rules (DBR and RBR)

Locked by the user. Do not change without an explicit new instruction.

## Scope
- Demand zones only: DBR (drop-base-rally) and RBR (rally-base-rally).
- Timeframes: monthly, quarterly, half-year, yearly. Prices are raw (auto_adjust=false).
- Only closed periods are used. A zone exists only after its legout period closes.

## Base candle (DBR and RBR)
- Body <= 40% of the candle range, so wicks (upper + lower) >= 60%.
- Applies to base candles only.
- Base is 1 or 2 candles. More than 2 is rejected.

## Legout candle (demand)
- Total wick (upper + lower) as a % of range:
  - DBR legout: <= 20%
  - RBR legout: <= 10%
- RBR rally candle (legin): total wick (upper + lower) <= 10%

## Drop candle (DBR only)
- Lower wick <= 10% of range.
- Drop start = the first red candle of the drop run.
- Legout close must be above the High of that first red candle (same timeframe).

## Freshness and invalidation
- Fresh = price has not touched the zone since the legout period closed (any timeframe, daily data).
- Touch = any candle low that goes into the zone. The zone becomes Tested.
- A break of the setup low needs a touch first, so it adds no separate rule.

## Entry and exit (backtest only)
- Entry: first touch, fill at open if gapped below the zone, else at the zone upper level.
- Stop: zone distal. Target: 1:2 reward/risk.
- Same-bar stop and target: stop first.

## Implementation
- `zone_agent/zones.py` `find_zones(..., require_high_break=True, strict_shapes=True)`
- Constants in `zone_agent/config.py`: STRICT_BASE_BODY_MAX, STRICT_LEGOUT_WICK_MAX, STRICT_RBR_LEGOUT_WICK_MAX, STRICT_DROP_LOWER_WICK_MAX.

---

# Final supply zone rules (RBD and DBD)

Locked by the user. Do not change without an explicit new instruction.

## Base candle (RBD and DBD)
- Body <= 40% of the candle range.
- Applies to base candles only. Base is 1 or 2 candles.

## RBD (rally-base-drop)
- Rally candle (legin): total wick (upper + lower) <= 20% of range.
- Drop candle (legout): total wick (upper + lower) <= 20% of range.

## DBD (drop-base-drop)
- Drop candles (legin and legout): total wick (upper + lower) <= 10% of range.

## Freshness and invalidation
- Same as demand: fresh until the first touch after the legout period closes.
