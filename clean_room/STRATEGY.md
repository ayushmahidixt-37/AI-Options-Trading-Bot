# Strategy specification — FROZEN 2026-08-28

This file is the complete definition of the strategy under evaluation. It is
**frozen**: nothing in it may be changed, tuned, or "improved" as part of an
evaluation. If a parameter here turns out to be wrong, that is a result, not a
bug to fix.

Any change to this file invalidates every evaluation run before it. Change it
only by writing a new file (`STRATEGY-v2.md`) and starting the evaluation record
over.

---

## Signal

`TrendConfirmedMomentumStrategy` — EMA trend agreement plus an RSI band, with a
slow macro EMA confirming the direction.

| Parameter | Value |
|---|---|
| `fast_period` | 5 |
| `slow_period` | 10 |
| `macro_period` | 60 |
| `rsi_period` | 21 |
| timeframe | `FIVE_MINUTE` |

Periods are counted in **bars, not minutes**. On five-minute candles
`macro_period=60` is 300 minutes. Running this specification on a different
timeframe without rescaling the periods tests a different strategy.

## Entry filters

| Parameter | Value | Meaning |
|---|---|---|
| `bullish_rsi_min` | **60** | a bullish signal is only taken when RSI ≥ 60 |
| `bearish_rsi_max` | **40** | a bearish signal is only taken when RSI ≤ 40 |
| `minimum_option_premium` | 20 | skip contracts cheaper than ₹20 |
| `minimum_open_interest` | 100,000 | skip contracts with unknown or thin OI |

The RSI band is the whole strategy. Everything else is inherited from the
baseline it was built on. Note that `minimum_signal_confidence` is **redundant**
with the RSI band — the confidence metric is a direction-conditional rescaling
of RSI (`0.5 + (RSI-50)/100` bullish, mirrored bearish), so `confidence ≥ 0.60`
is algebraically identical to RSI 60/40. Do not set both.

## Exit

| Parameter | Value |
|---|---|
| `stop_risk_fraction` | 1.6 |
| `target_return` | 0.30 |
| `trailing_stop` | none |
| `maximum_hold_minutes` | none |
| force exit | session close (`FORCE_EXIT_IST`, default 15:20) |

Positions are strictly sequential — one at a time, never overlapping. A new
signal closes any open position before opening its own.

## Position sizing

| Parameter | Value |
|---|---|
| starting capital | ₹1,00,000 |
| sizing rule | risk-based |
| risk per trade | **2%** of the *current* balance |
| position cap | 50% of balance in premium |
| max lots | uncapped |

Lots = `(balance × 0.02) / risk_per_lot`, where `risk_per_lot` is that trade's
own distance to its stop × lot size, plus fees — bounded by what the balance can
afford in premium. A trade that cannot afford one lot is **skipped**, and skipped
trades must be reported.

Do **not** size by affordability (as many lots as the balance can buy). That rule
gives cheap options the largest risk allocation and is wrong.

## Costs — mandatory

Fees and slippage must both be applied. An evaluation run with costs disabled is
invalid; that error inflated a previous result by a factor of seven.

| Setting | Source |
|---|---|
| `PAPER_FEE_PER_ORDER` | environment / config, not zero |
| `PAPER_SLIPPAGE_BPS` | environment / config, not zero |

## Expected trade frequency

Roughly **one trade per week** (~47/year). A run producing far more has probably
lost the RSI band; far fewer suggests a data gap. Either is worth checking before
reading the P&L.
