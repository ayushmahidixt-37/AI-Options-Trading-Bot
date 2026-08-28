# Fetch instructions

Run these in order. Read `CONNECTION.md` first for credentials and API limits.

Interpreter throughout: `C:\Users\DELL\pyembed312\python.exe`

## Before running anything: check the token is alive

`DHAN_ACCESS_TOKEN` lives about 24 hours and is the single most common reason a
run fails. A multi-hour job against an expired token just produces an empty
database and a `failed_requests.json` full of triples that aren't genuinely
missing — worth one cheap call first:

```python
from datetime import date, timedelta
from options_bot.credentials import load_credentials
from options_bot.dhan_data import DhanClient

client = DhanClient(load_credentials("credentials.env")["DHAN_ACCESS_TOKEN"].strip())
points = client.fetch_index_intraday(from_date=date.today() - timedelta(days=5), to_date=date.today())
print(f"OK -- {len(points)} points")
```

A `DH-901` error here means the token has expired. See `REFRESH_TOKEN.md` — it's
a manual step requiring your Dhan login, not something this repo can do for you.

## The command

Everything below is wrapped in one runner. `--walk-back` fetches backwards a
year at a time from `--end` and stops once **two consecutive years** return
nothing (one empty year alone is as likely to be a token/rate-limit blip as a
real historical limit, so it isn't trusted alone):

```bash
C:/Users/DELL/pyembed312/python.exe -u data_ingest/run_fetch.py --walk-back --end 2026-08-28 > data_ingest/data/fetch.log 2>&1
```

Then read `data_ingest/data/fetch.log`. Redirect rather than piping — piping to
`tail` buffers until exit and a running job looks hung.

The log will show which year the fetch stopped at; that's the API's historical
limit, not a failure.

**Failed requests are retried at the end, not immediately.** A request that fails
on a rate limit usually succeeds minutes later, and retrying at once just consumes
the same limit. Anything still failing after the retry rounds is written to
`data_ingest/data/failed_requests.json`, with exactly which (strike, option type,
cycle) triples are missing. Re-attempt just those, any time:

```bash
C:/Users/DELL/pyembed312/python.exe -u data_ingest/run_fetch.py --retry-only
```

Storage is idempotent — re-saving an existing candle is a no-op — so re-running is
always safe. The runner exits non-zero while anything is still missing.

If the Dhan token expires mid-run (~24h), refresh it and run the same command
again; already-saved rows are skipped.

The steps below describe what the runner does, for when something needs driving
by hand.

## Target database

Write to a **new file**, not the main archive:

```
data_ingest/data/nifty_forward.sqlite3
```

Keeping it separate means a partial or failed fetch cannot corrupt the 12 GB
historical archive, and the result can be inspected on its own before anyone
trusts it.

## Step 1 — index candles (do this first)

The underlying series must exist before option data is worth anything: no index
candles means no EMA or RSI, so the strategy produces nothing regardless of how
many option rows were fetched.

```python
from datetime import date
from options_bot.credentials import load_credentials
from options_bot.dhan_data import DhanClient
from options_bot.dhan_ingest import pull_index_range
from options_bot.market_archive import MarketArchive

archive = MarketArchive("data_ingest/data/nifty_forward.sqlite3")
archive.initialize()
client = DhanClient(load_credentials("credentials.env")["DHAN_ACCESS_TOKEN"].strip())

saved, warnings = pull_index_range(client, archive, date(2026, 1, 1), date.today())
print(saved, warnings)
```

Stores under `NSE_INDEX|Nifty 50` with `source='dhan'` — the exact token the
engine reads. Chunks to 90 days internally.

**Walk backwards in chunks** (e.g. 2026 → 2025 → 2024) rather than requesting a
huge span at once. When a chunk returns nothing, that is the API's historical
limit; record the date and stop. Warnings are returned, not raised, so check the
list rather than assuming success.

## Step 2 — option contracts with OI and IV

```python
from options_bot.dhan_ingest import pull_range

summaries = pull_range(client, archive, date(2026, 1, 1), date.today(),
                       on_cycle_done=lambda s: print(s))
```

Iterates weekly expiry cycles oldest-first and stores the strike ladder with
`open_interest`, `implied_volatility` **and `volume`**. This is the slow part —
expect minutes-to-hours depending on span. Print each cycle so progress is visible.

**Fixed 2026-08-28, immediately before this folder was written.** Dhan returns
volume and IV on every rolling-option call, but both were being parsed and then
discarded: `UpstoxCandle` had no field for them and `market_candles` had no
`volume` column. Volume was lost outright; IV had to be recovered by a second
UPDATE pass. Expired contracts cannot be re-fetched, so every earlier run lost
that data permanently. Both now persist on the first pass — verify `volume` is
populated in step 4 rather than assuming it.

### Strike coverage — decide before fetching, not after

`dhan_ingest.STRIKE_OFFSETS` is `range(-10, 11)`: ATM±10 strikes, CE and PE, 42
requests per weekly cycle. At 50-point strikes on a ~24,000 index that is roughly
**±2% around spot**.

Anything further out — 3-5% OTM strangles, wing hedges, ratio spreads — will have
**no data, ever**, because the contracts expire and cannot be fetched again later.
Widening the range costs proportionally more requests and time. Raise it now if
wider strikes might matter; it cannot be repaired retrospectively.

## Step 3 — resample to 5-minute (required)

**Do not skip this.** The strategy runs on `FIVE_MINUTE` bars, and the engine
does **not** derive one timeframe from another at query time — a 1-minute-only
database yields zero trades while every other health check passes. That exact
mistake shipped once already.

```python
from options_bot.dhan_ingest import (
    resample_dhan_underlying_to_five_minute,
    resample_dhan_options_to_five_minute,
    resample_dhan_iv_to_five_minute,
)
print(resample_dhan_underlying_to_five_minute(archive))
print(resample_dhan_options_to_five_minute(archive))
print(resample_dhan_iv_to_five_minute(archive))
```

## Step 4 — verify before reporting success

```bash
C:/Users/DELL/pyembed312/python.exe clean_room/inspect_dataset.py \
    --dataset data_ingest/data/nifty_forward.sqlite3
```

Must show, or the fetch is not done:

- both `FIVE_MINUTE` **and** `ONE_MINUTE` present, `source=dhan`
- `strategy timeframe FIVE_MINUTE: <n> rows present` (not the FATAL message)
- open interest on a high share of rows — near zero means options didn't land
- the NIFTY index series present, with the date range you expect
- no unexplained gaps

## What to report back

1. Date range actually obtained, and where the API stopped returning data.
2. Row counts by `timeframe` and `source`.
3. Percentage of rows carrying open interest.
4. The full `inspect_dataset.py` output.
5. Any warnings from steps 1–2 — they are returned in lists, not raised, so they
   are easy to miss.

## Things that will bite

**Long jobs, no visible progress.** Use `python -u` and redirect to a file. Never
pipe to `tail` while waiting — it buffers until the process exits and a running
job looks hung. That cost hours here.

**Slow queries.** Don't wrap a column in a function (`date(started_at) >= ?`) or
use `LIKE` on the token — both disable the indexes and turn a lookup into a scan
of millions of rows. ISO timestamps compare lexicographically, so
`started_at >= ?` works and stays indexed.

**Expired token.** 401/403 means `DHAN_ACCESS_TOKEN` has aged out (~24h). Refresh
it and resume — the fetch is resumable, already-saved rows are not re-fetched.

**Partial sessions.** Some days legitimately hold ~21 candles instead of a full
session. Note them; they are not necessarily corruption.
