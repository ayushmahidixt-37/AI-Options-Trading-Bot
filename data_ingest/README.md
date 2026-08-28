# data_ingest — fetch 1-minute NIFTY candles forward

**Folder to reference in the other chat: `data_ingest/`**

## The job

Populate 1-minute NIFTY candles — index *and* option contracts — from today
backwards, as far as the API allows, into a database this project's backtest
engine can actually read.

## Why this exists

Historical ingestion stopped on **2026-08-20**. The only data collected since is
from the live Angel One monitor, and the backtest engine cannot use it:

| Problem | Detail |
|---|---|
| Wrong source | engine filters `source IN ('upstox','dhan')`; there is no Angel branch |
| Wrong token | Angel writes `40999`, `41000`…; the engine queries `NSE_INDEX\|Nifty 50` |
| No open interest | all Angel rows have `open_interest = NULL`, and the strategy requires ≥ 100,000 |

So forward data has been accumulating in a shape nothing can evaluate. Until
that is fixed, waiting for "fresh data" produces nothing usable, however long
you wait.

## What success looks like

A database whose rows are indistinguishable in shape from the historical
archive. Concretely:

| Field | Required value |
|---|---|
| `source` | `dhan` (or `upstox`) — **never** `angel-one` |
| underlying `instrument_token` | `NSE_INDEX\|Nifty 50` exactly |
| option `instrument_token` | `DHAN\|NIFTY\|<expiry>\|<strike>\|<CE\|PE>` |
| `timeframe` | `ONE_MINUTE` (and `FIVE_MINUTE`, resampled — see FETCH.md) |
| `open_interest` | populated, not NULL |
| `instruments` table | one row per contract, with `strike`, `expiry`, `lot_size` |

**Verification is not optional.** `clean_room/inspect_dataset.py` will tell you
whether the result is usable. A dataset that looks fine and yields zero trades
has already happened once here.

## Files

| File | Contents |
|---|---|
| `CONNECTION.md` | which credentials are needed and where they live |
| `FETCH.md` | the exact commands, in order |

## Scope

Fetch and verify. Nothing else — no strategy work, no analysis, no evaluation.
Those belong in `clean_room/` and are deliberately kept separate.
