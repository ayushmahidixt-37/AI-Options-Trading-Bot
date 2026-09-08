# Phase 4 — live paper-trading runner

New file: `src/options_bot/live_paper_runner.py`. Runs the 5 adopted strategies
(CONFIG.md) against **live** market data instead of historical data, on paper.

## What it reuses vs. what's new
- **Reused unmodified**: the exact strategy signal/entry/exit code from
  `Backtest Stratergies/strategies/*.py` and the cost/sizing math from `engine.py`
  -- imported directly, not re-implemented. This is deliberate: this repo already
  lost a strategy once ("Candidate B") to a live version quietly drifting from
  its backtested rules. There is one copy of each strategy's logic here, used
  for both backtest and live.
- **Reused from the existing live system**: `ConnectionManager` (live NIFTY
  price, live option instrument/quote resolution) -- already working,
  already exercised by the dormant Candidate B path. `Settings.validate()`
  hard-refuses to construct anything unless `trading_mode=="paper"` and
  `live_trading_enabled==False`; this file adds no order-placement
  capability beyond `PaperBroker`'s ("contains no broker order API").
- **New**: a live 1-minute bar buffer, incremental signal detection (re-runs
  the backtest's own `signals()` each cycle against however much of today
  has happened, distinguishing a real end-of-day exit from "no more data
  yet" by checking the exit bar's own clock time), live option
  instrument/price resolution, and an implied-volatility inversion
  (`engine.implied_vol`, added this phase) since live quotes carry no IV
  field the way the backtest's archive does.

`ACTIVE_STRATEGIES` (top of the file) is the one thing to edit to run 1
strategy or all 5.

## Two real bugs found and fixed before trusting this

1. **Every price/instrument lookup was reading from the static backtest
   archive, not live data.** Checked directly: that archive's `instruments`
   table has no expiry past 2026-09-03 and no priced row past 2026-08-28 --
   already stale relative to today. Fixed by routing all current-price and
   instrument-resolution calls through `ConnectionManager` (live) instead of
   `engine.connect()` (static); the static connection is now used only for
   pure historical warm-up (see #2) and never for anything "current."

2. **The live bar buffer only held today's bars, cold-starting Supertrend's
   ATR every morning.** Found by literally replaying a real historical day
   through the live code path and comparing to the known backtest result --
   the two didn't match, and reconstructed bars from a naive tick feed
   collapsed to zero range (O=H=L=C), which is what actually broke it. Fixed
   two ways: (a) the buffer now seeds itself with ~10 days of real historical
   bars at startup purely to warm up multi-day indicators (using stale data
   to warm up indicator *state* is normal practice, unlike using it for
   current prices, which is bug #1's mistake), and (b) the live poll loop
   samples price every 15 seconds (not once a minute), so real bars have
   real intra-minute range instead of degenerating to a single point.

## Validated -- how, and how far
Replayed a real historical day (2023-06-05) through the ACTUAL live code
path (`run_cycle`, the real bar buffer, the real signal detection), using
fakes that satisfy the same interface `ConnectionManager` exposes, and
compared the result to the already-known backtest trades for that day.

**Result: exact match** on both trades' entry/exit timestamps, direction,
strike, entry/exit premiums, and net P&L to the rupee (-Rs29.22 and
-Rs374.04). The only difference: one trade sized 4 lots live vs. 5 in the
backtest -- expected and disclosed, because live quotes carry no IV, so
position sizing falls back to a delta of 0.5 instead of the real ~0.43 the
backtest had from stored IV (`E.size_by_index_risk` already documents this
fallback; it's not new here).

This validates the **mechanism** shared by #3 and #4 (bar building,
multi-day indicator continuity, incremental signal detection, live-quote
option execution). #7's IV-inversion path was validated separately and in
isolation (round-trip tested against a known Black-Scholes price -- see
engine.py's `implied_vol`). #7/#10/#12's live adapters were built with the
same care (importing each strategy's own threshold constants rather than
retyping them) but were **not** independently replay-tested against a known
day the way #3/#4 were, for lack of remaining time in this pass -- treat
them as architecturally sound and logically reviewed, not proven the same
way.

## Known limitations, disclosed rather than hidden
- **No live IV feed.** Affects position sizing for all 5 (falls back to
  delta=0.5, a documented approximation) and #7's strike selection
  specifically (uses `engine.implied_vol`, a Black-Scholes inversion from
  the live quote -- validated in isolation, not yet validated end-to-end
  live).
- **Process-restart mid-day**: #10/#12's re-entry/breakeven state lives in
  memory for the running process (`run_two_leg_stop_strategy`'s `_state`).
  A restart between 9:20 and 15:06 loses that day's phase/leg state --
  the ledger file survives, but the day's position tracking would need to
  be reconstructed from it rather than resuming automatically. Not fixed
  this pass; don't restart the process mid-session without checking
  `live_paper_trading/ledger.jsonl` first if this happens.
- **Not tested against an actual Angel One connection** -- nothing in this
  environment can do that. `connect_angel()`/`quote_instrument()` are the
  same calls the dormant Candidate B path already uses live, but this
  specific file's use of them is unexercised against a real broker session.

## To run it
Requires the same credentials/env setup the rest of `src/options_bot`
already uses (`credentials.env`, `local-bot.env` -- unchanged by this
phase). From the repo root:
```
python -m options_bot.live_paper_runner
```
Runs during NSE market hours, sleeps outside them, writes every trade to
`live_paper_trading/ledger.jsonl` (gitignored -- runtime output, same
convention as `Backtest Stratergies/results/`) in the same row shape the
backtest's own JSON uses, so existing analysis scripts read it unmodified.
Reduce `ACTIVE_STRATEGIES` to `["s04_supertrend_vwap"]` alone for a
lower-risk first day, given the validation-depth difference above.
