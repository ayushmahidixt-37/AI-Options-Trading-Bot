# RUN ME — instructions for an independent evaluation

You have been given a dataset and a frozen strategy. Your job is to run the
strategy against the data, report what happened, and change nothing.

**Do not optimise. Do not tune. Do not try variations.** If the result is poor,
that is the finding. A different parameter that scores better is not an
improvement — it is a different strategy, and testing it here would destroy the
only thing this folder is for.

---

## What's here

| File | What it is |
|---|---|
| `STRATEGY.md` | The complete, frozen strategy definition |
| `PROTOCOL.md` | Evaluation rules and pre-registered pass/fail criteria |
| `evaluate.py` | The runner. Has no tunable parameters by design |
| `data/*.sqlite3` | Self-contained market data — candles, open interest, IV, instruments |
| `RESULTS.md` | The run log. Append to it, never overwrite |

## Step 1 — check the data

```bash
python clean_room/inspect_dataset.py --dataset clean_room/data/<file>.sqlite3
```

Prints the date range, instrument count and how much of it carries open
interest. If the range is not what you expect, stop and say so — an evaluation
on the wrong window is worse than none, because it looks like evidence.

## Step 2 — run the evaluation

```bash
python clean_room/evaluate.py \
    --archive clean_room/data/<file>.sqlite3 \
    --start <YYYY-MM-DD> --end <YYYY-MM-DD> \
    --include-dhan --markdown
```

`--markdown` prints a block formatted for pasting straight back into a
conversation. Paste **all** of it, including any FAIL lines. A partial paste is
how a bad result quietly becomes a good one.

The script will refuse to run if fees or slippage are switched off, and refuses
data the strategy was derived from unless explicitly overridden. Both refusals
are deliberate — a previous result in this project was inflated sevenfold purely
by having costs disabled.

## Step 3 — record it

Append one row to `RESULTS.md` **before** interpreting the numbers:

```
| date run | data range | trades | net P&L | win% | PF | max DD% | verdict |
```

## What to report back

Paste the whole `--markdown` block. Then, in your own words:

1. Anything that looked **wrong with the data** — gaps, a suspicious trade count,
   a date range that doesn't match what was asked for.
2. Whether the trade frequency was roughly **one per week**. Far more suggests the
   RSI band isn't being applied; far fewer suggests missing data. Either matters
   more than the P&L.
3. Anything you noticed that the criteria don't capture.

**Do not** offer an opinion on whether the strategy is good, and do not suggest
parameter changes. Those questions are decided elsewhere, deliberately, by
someone who cannot see this result while choosing.

## The one thing that invalidates everything

Running the strategy, seeing a poor result, adjusting something, and running
again. That converts an independent test into a search, which is precisely the
error that produced two retracted "confirmed" strategies in this project. If you
believe a parameter is wrong, write that down as an observation and leave it
unchanged.
