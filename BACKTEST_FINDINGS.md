# Backtest Findings Log

> Read this before running or interpreting any Upstox historical backtest.
> Add a dated entry every time a real backtest round produces a finding
> worth remembering — confirmed, rejected, or still open. Keep entries
> short and factual: what was tested, on what data, what happened. This
> file is the detailed record; `PROJECT_STATUS.md` only carries a short
> pointer and headline summary.

## How to read this log

- **Confirmed** = survived being picked on a development/validation split
  and then checked *exactly once, for exactly one candidate*, on a test
  range that had never been looked at in any form before that single
  check — not summarized in an earlier full-range pass, not used to
  compare two or more candidates against each other. Touching a "test"
  range more than once, or in a prior pass, disqualifies it from this
  label — downgrade to Exploratory instead and say why.
- **Exploratory** = a real result, but the test range it was checked
  against had already been touched before the check (an earlier
  full-range pass, or used to compare multiple candidates) — so it is
  not a clean confirmation, even though the discipline was mostly
  followed. Needs a genuinely fresh, never-yet-analyzed period before
  it can be upgraded to Confirmed.
- **Rejected** = tested and failed, either outright or by looking good on
  one sample and reversing on a bigger/different one. Recorded so nobody
  re-tests the same dead end later.
- **Open** = tried once, inconclusive, or not yet run through the full
  development/validation/untouched-test discipline. Not to be trusted or
  acted on yet.

Every result here is a plain aggregate over historical trades — no model,
no fitting. A "Confirmed" entry is still a hypothesis with reasonable
support, not a proven edge; treat sample sizes and the caveats listed
honestly before changing any live default.

**Read the 2026-08-21 "Data-integrity incident" entry before trusting or
re-running any number below it.** Every entry dated before 2026-08-21 is a
frozen historical snapshot — re-running it today will not reproduce the same
numbers, for reasons unrelated to the strategy itself (see that entry).

## 2026-08-21 — Data-integrity incident: every entry above this line is a frozen, non-reproducible snapshot

**What happened.** A research script, `research/materialize_resampled_candles.py`,
was run to derive 5/10/15-minute candles from already-archived 1-minute Upstox
data (for a separate 1-minute-granularity strategy test). It wrote those
derived candles into the *same* `market_candles` rows (`source='upstox'`,
`timeframe='FIVE_MINUTE'`, etc.) that every "real" Upstox pull in this file
also uses, with no marker distinguishing a derived bar from a directly-fetched
one. It touched every instrument that had 1-minute coverage, not just the
near-ATM contracts the real ingestion ever selected, expanding `FIVE_MINUTE`
from ~700 real, Jan–Aug-2026-only contracts to 2,154 tokens spanning
2024-10-03 to 2026-08-20. Re-running the documented Baseline (Jan-Mar dev /
Apr-May val) against the mixed archive gave 87t/+67,589.05 dev and
92t/+19,716.75 val — nowhere close to the 68t/+20,517.50 / 86t/-626.80
documented below.

**The deeper problem, found while trying to clean it up.** Deleting the
out-of-window derived rows (anything outside 2026-01-01–2026-08-20, unambiguous
since no real pull ever touched those dates) only partially fixed it — 87
trades dev, still not 68. A **fresh, live re-pull of the exact same real
ingestion** (same code, same parameters, same date range, against the live
Upstox API, just run on 2026-08-21 instead of 2026-08-12) gave **157 trades**,
further from the documented number than the contaminated archive was. This
means the mismatch isn't only contamination — `upstox_ingest.py`'s near-ATM
contract discovery (`plan_ingestion`/`_reference_spot`, which approximates ATM
as the median of whatever strikes Upstox's API returns for an expiry when no
live spot is passed) is **not stable across invocation time**. The exact
contract universe a "Jan-Aug 2026 Upstox pull" produces depends on when you
run it, not just what range you ask for. There is no way to reconstruct the
original 2026-08-12 dataset after the fact.

**Consequence:** every entry in this file below this point is a **frozen
historical record of what a specific run produced on a specific day** — not a
reproducible property of "the market data for that period." Re-running any of
them today will not match. This doesn't retroactively invalidate the
qualitative lessons (overfitting patterns, what filters helped/hurt, the
Rejected test-range result) — those were real observations about real trades
at the time. It does mean nobody should expect to reproduce the exact numbers,
and any *new* work should treat the numbers below as historical color, not a
regression baseline to match.

**Fixes put in place so this can't happen silently again:**
- `market_candles` gained a nullable `derived_from_timeframe` column
  (`market_archive.py`). `save_upstox_candles()` requires callers writing
  resampled/derived candles to set it explicitly; a real, directly-fetched
  bar leaves it `NULL`.
- `run_upstox_backtest`, `run_upstox_ml_backtest`, and the deep-analysis ATR
  recomputation now all filter `derived_from_timeframe IS NULL` on every
  `market_candles` read — a future materialize run (even with the same bug)
  can no longer silently change what these engines see, regardless of what's
  sitting in the archive.
- `research/materialize_resampled_candles.py` now tags every row it writes
  with `derived_from_timeframe='ONE_MINUTE'` and its docstring points back to
  this entry.
- Regression tests added: `test_upstox_candles_default_to_not_derived_and_can_be_tagged`
  (`tests/test_market_archive.py`) and
  `test_run_upstox_backtest_never_selects_a_derived_contract`
  (`tests/test_upstox_backtest.py`).

**New Baseline reference, going forward (`baseline-2026-08-21-repull` in the
range-usage ledger)** — `BacktestParameters` defaults, same Dev (Jan-Mar) /
Val (Apr-May) 2026 ranges, current archive (fresh live re-pull, 726 real
near-ATM contracts, zero warnings, zero gaps):

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development | 157 | 21.0% | +69,049.15 | 9,774.50 | 2.71 |
| Validation | 92 | 19.6% | +19,716.75 | 6,385.95 | 1.81 |

Any future candidate comparison against "Baseline" should use these numbers,
not the 2026-08-12 ones below. Win rate here (21.0%/19.6%) is meaningfully
higher than the 2026-08-12 archive's 8.8%/12.8% — consistent with a materially
different, larger contract universe, not a strategy change.

## 2026-08-22 — Historical extension to Oct 2024, built locally from 1-minute data

**Goal:** extend real coverage back to Upstox's actual platform ceiling
(verified live: `client.get_expiries()` returns exactly 99 expiries, earliest
**2024-10-03**, latest 2026-08-18 — this is a hard limit, not a budget/setting;
nothing older than 2024-10-03 is fetchable at any subscription tier) instead
of stopping at the 2026-01-01 start date every prior entry in this file used.

**How the data was built — real vs. derived, explicitly:**

| Data | Timeframe | Origin | Rows | Tokens | Date range |
|---|---|---|---|---|---|
| Option contracts | `FIVE_MINUTE` | **Real** (fetched live from Upstox) | 753,815 | 1,715 | 2024-10-03 to 2026-08-18 |
| Option contracts | `FIVE_MINUTE` | **Derived** from already-archived real 1-minute candles (`research/materialize_resampled_candles.py`, local resample, no network) | 183,819 | 438 | 2025-08-14 to 2025-12-30 |
| Underlying NIFTY index | `FIVE_MINUTE` | **Real** | 11,775 | 1 | 2026-01-01 to 2026-08-20 |
| Underlying NIFTY index | `FIVE_MINUTE` | **Derived** — see mislabeling bug below | 23,134 | 1 | 2024-10-03 to 2025-12-31 |
| Everything (options + underlying) | `ONE_MINUTE` | Real, genuinely 1-minute-spaced (375 rows/trading day) — the *source* for every derived row above; never read directly by any backtest engine | 4,608,703 | 2,154 | 2024-10-03 to 2026-08-20 |

Every derived row is tagged `derived_from_timeframe='ONE_MINUTE'` in
`market_candles` (see the 2026-08-21 entry above for why this column exists).
`run_upstox_backtest`/`run_upstox_ml_backtest` exclude derived rows **by
default**; this round's results below were produced with the explicit
`include_derived=True` opt-in, specifically because real Upstox data was
never pullable for the 438-token/underlying gap above (only the 438-token
slice is genuinely mixed; the other 1,715 option tokens' data used here is
100% real).

**Why local resampling instead of another live pull:** a live re-pull started
first (it's what produced the "real" 1,715-token/753,815-row slice above,
covering most of the range before it was stopped), but resampling from
already-downloaded 1-minute data is faster, doesn't burn Upstox API quota,
and — unlike a live pull — is **fully deterministic and reproducible** once
the 1-minute source data is fixed locally, which a live re-pull is not (see
the 2026-08-21 entry above).

**A second, separate, pre-existing bug found while filling the underlying's
gap:** the underlying NIFTY index's rows tagged `timeframe='ONE_MINUTE'` were
actually spaced **5 minutes apart** (75 rows/trading day = 375÷5, not 375)
— not genuine 1-minute ticks, unrelated to the contamination bug above.
Confirmed by contrast: option-contract `ONE_MINUTE` rows are genuinely dense
(375/day). Likely cause: the underlying uses a different Upstox endpoint
(`get_historical_candles_v3`) than option contracts
(`get_expired_historical_candles`), and whatever originally pulled the
underlying's "1-minute" data got 5-minute data back without erroring. Fixed
by relabeling those rows directly as `FIVE_MINUTE` (tagged
`derived_from_timeframe='ONE_MINUTE'` for traceability, even though no
aggregation happened — nothing finer existed to aggregate).

**A third bug, a real performance issue:** the per-signal contract-selection
query re-scanned the *entire* `market_candles` table (a `SELECT DISTINCT
instrument_token ... WHERE source='upstox'` subquery, unindexed on
`source`/`derived_from_timeframe`) once per observation. Harmless at the
original archive's few-hundred-thousand-row scale; at 5.7M rows it made a
15-month backtest run for **12+ hours before being killed** instead of
finishing. Fixed by precomputing the (constant, observation-independent)
available-token set once per backtest call into an indexed SQLite temp
table instead of re-deriving it per signal — the same 15-month range now
completes in under 9 minutes. `run_upstox_backtest` also gained the
`include_derived` parameter as part of this fix.

**A fourth bug, also found in passing:** `gap_summary()`/`build_backtest_result()`
weren't scoped to the backtest's `[start, end]` at all — every prior Upstox
`DATA QUALITY WARNING` status and gap count in this file (including the
2026-08-21 entries above) reflects the **entire archive's** gaps at the time,
not the specific range being backtested. Fixed by threading `start`/`end`
into `gap_summary()`; confirmed by re-running the extension chunks below,
where per-chunk gap counts now genuinely vary (22/44/296/0/0) instead of
reporting an identical constant every time.

**Results — Baseline and the leading hand-tuned candidate
(`stop_risk_fraction=1.6, target_return=0.50, trailing_stop=0.20,
bullish_rsi_min=55, bearish_rsi_max=45, minimum_atr=20`), run in quarterly
chunks over 2024-10-03 to 2025-12-31** (role=`screening` in the ledger —
this range is chronologically *before* every range already used by other
candidates, so per `research_ledger.py`'s rule it can never serve as a
`test` range for any candidate; this is out-of-sample color, not a
confirmation):

| Chunk | Baseline trades / win% / net P&L | Strict RSI55+ATR20 trades / win% / net P&L | Gaps (baseline) |
|---|---|---|---|
| 2024-10-03 to 2024-12-31 | 143 / 28.0% / +40,958.50 | 71 / 42.3% / +23,803.25 | 22 |
| 2025-01-01 to 2025-03-31 | 168 / 14.9% / +7,527.25 | 82 / 29.3% / +4,015.75 | 44 |
| 2025-04-01 to 2025-06-30 | 190 / 17.9% / +11,795.75 | 84 / 28.6% / +20,443.50 | 296 |
| 2025-07-01 to 2025-09-30 | 188 / 15.4% / +1,795.75 | 35 / 25.7% / -4,404.50 | 0 |
| 2025-10-01 to 2025-12-31 | 159 / 13.8% / -6,618.75 | 51 / 29.4% / -3,292.50 | 0 |
| **Total** | **848 / 17.7% / +55,458.50** | **323 / 31.6% / +40,565.50** | — |

**Reading this honestly:** both candidates are net profitable across the
full 15-month extension, and the RSI55/ATR20 filter's win-rate edge over
Baseline (31.6% vs 17.7%) holds up — consistent with what the 2026-08-20
sweep found on the (now non-reproducible) original archive. But per-quarter
performance is uneven for both — strongly positive in 2024-Q4, weak-to-
negative by 2025-Q4 for the filtered candidate — which is exactly the kind
of regime variation a single Jan-Aug 2026 window could never have shown.
Labeled **Open**: real, useful, larger-sample evidence that the general
shape (filtered-and-widened beats baseline on win rate) generalizes across
very different market conditions, but not a confirmation of any specific
parameter set, and never eligible to become one under this project's own
test-range rule.

## 2026-08-22 — ML signal-quality filter, v3: retrained on the extended history

**Context:** the two prior ML attempts (2026-08-21 entry below) were both
inconclusive, most likely because of thin training data (68 and 155 raw
signals respectively). The historical extension above made ~15x more
development-range trades available. Retrained
(`research/train_signal_quality_model.py --include-derived`, same exit
shell as every prior ML attempt: `stop_risk_fraction=1.6,
target_return=0.50, trailing_stop=0.20`) on Development = 2024-10-03 to
2026-03-31 (real + derived data, see the historical-extension entry above
for exactly which portions are which), Validation = 2026-04-01 to
2026-05-31 (unchanged from every prior attempt, for comparability). 7
features, same as v1/v2 (RSI, normalized ATR, normalized EMA gap,
confidence, direction, minutes-since-open, day-of-week) — this round did
not add the still-untested open-interest/EMA-strength features.

**Two real bugs found and fixed while setting this up** (see
`PROJECT_STATUS.md`'s 2026-08-22 headline for the full account):
`train_signal_quality_model.py` couldn't use the extended history at all
until it gained an `--include-derived` flag threaded through to
`run_upstox_backtest`/`run_upstox_ml_backtest`; and `generate_signals_from_candles`
had a real O(n²) bottleneck (recomputing EMA/RSI/ATR from scratch over an
ever-growing window at every step) that turned this training run's first
attempt into a 12-hour-plus hang. Fixed by precomputing each indicator
series once (`MomentumStrategy.signal_from_indicators` extracted so the
decision rule and the indicator computation are decoupled), proven
byte-identical to the old per-step approach by a new regression test
(`test_generate_signals_fast_path_matches_naive_full_recompute`) before
being trusted. The same 18-month training run that didn't finish in 30+
minutes before now completes in about 3 minutes.

**Result:**

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development (unfiltered baseline, same exit shell, no ML) | 1,012 | 30.7% | +62,095.65 | 31,634.00 | 1.20 |
| Development (ML-filtered, threshold=0.30) | 449 | 36.5% | +50,255.50 | 32,053.25 | 1.40 |
| Validation (ML-filtered, threshold=0.30) | 48 | **43.75%** | +15,396.00 | 3,900.85 | **2.17** |

**This is a real improvement over both prior ML attempts** — far more
trades on both splits (449/48 vs. 15/22 for v1, 9/20 for v2), the highest
validation win rate any ML attempt has produced, and the best validation
profit factor. The chosen threshold (0.30) is the floor of the swept range
[0.30, 0.70] — the sweep never found a stricter threshold that beat it on
validation net P&L, meaning this model mostly separates out clearly-bad
signals rather than aggressively filtering, which is a plausible outcome
of having much more data to learn a more general decision boundary from
(the prior attempts' selected thresholds landed in the middle of their
sweep ranges, on much thinner data).

**Labeled Open, not Confirmed or Rejected** — same reason every prior
candidate in this file is: no test-range attempt has been made or is
appropriate here (the model, like everything else this session touches
2026-08-21 and earlier archive state, would need to be evaluated against a
genuinely fresh range under this project's one-shot test discipline, not
declared trustworthy from development/validation numbers alone). Also
worth weighing: this result isn't directly comparable to the hand-tuned
"Strict RSI55+ATR20" candidate's historical-extension numbers above (that
candidate's chunks span 2024-10-03 to 2025-12-31; this training run's
validation range is 2026-04-01 to 2026-05-31 -- different periods) — a
clean head-to-head would need both run over identical ranges. Not wired
into any live/forward-paper path.

**Not yet tried, still on the table for a future round:** the
already-scaffolded-but-unused open-interest and days-to-expiry
(`POSTCONTRACT_FEATURE_NAMES`) features; EMA separation magnitude;
rolling-origin (walk-forward) validation across the extended history's
quarters instead of one static split; a development-side trade-count floor
at threshold-selection time (the same gap v2's "strong validation, thin
development" failure exposed, still not fixed); sweeping thresholds below
0.30 given the sweep chose the floor every time.

## 2026-08-22 — ML signal-quality filter, v4: hyperparameter + fine threshold sweep

**Context:** immediate follow-up to v3, addressing two of that entry's
"not yet tried" items: v3's threshold sweep never tested below 0.30 (its
own floor), and L2/learning-rate were never varied from the defaults
(l2=0.01, learning_rate=0.1). Same Development/Validation split, same
7 features, same exit shell as v3 — only the training hyperparameters and
threshold search range changed.

**Hyperparameter grid** (l2 ∈ {0, 0.01, 0.05, 0.1} × learning_rate ∈
{0.05, 0.2}, 3000 epochs, coarse threshold sweep per combo to keep this
tractable): **learning rate made no meaningful difference** at any l2 —
expected, since this is a convex optimization problem and both rates
converge to essentially the same minimum given enough epochs. **l2=0.05
was a genuine improvement** over v3's l2=0.01, and l2=0.1 (too much
regularization) was slightly worse again — a real, if modest, optimum in
the middle of the range, not a monotonic trend.

**Fine threshold sweep (0.05 to 0.35, step 0.01) on the l2=0.05 model
revealed v3's threshold search was scoped wrong**, not just coarse: v3
only ever searched [0.30, 0.70] and selected the floor (0.30) by default.
The true peak by net P&L is at **0.25**, a region v3 never tried:

| Threshold | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| 0.05–0.22 (plateau) | 88–90 | 41–42% | ~35,200–36,600 | ~3,990 | ~2.24–2.36 |
| **0.25 (best net P&L)** | **84** | **42.9%** | **+38,175.00** | 3,342.55 | 2.52 |
| 0.30 | 47 | 44.7% | +22,925.95 | 2,577.40 | 2.79 |
| 0.32 | 33 | 48.5% | +15,132.80 | 2,358.35 | 3.18 |
| 0.34 | 27 | 55.6% | +17,695.90 | 1,362.45 | 5.46 |
| **0.35 (best quality)** | **21** | **57.1%** | +14,701.50 | 1,003.00 | **6.19** |

**This is the same win-rate-vs-total-return tradeoff already documented
for the hand-tuned RSI/ATR filters, now showing up in the ML model too**:
past ~0.30 the model sheds trades fast in exchange for a rapidly climbing
win rate and profit factor. There is no single "best" threshold — it
depends on what's being optimized. `research/models/ml-signal-quality-v4-hyperparam-swept.json`
(l2=0.05, threshold=0.25, the max-net-P&L point) is saved as the
headline candidate, but 0.30–0.35 are legitimate alternate choices for a
smaller, higher-conviction trade set.

**v4 vs v3, same exit shell and dev/val ranges:**

| | Trades | Win rate | Net P&L | Profit factor |
|---|---|---|---|---|
| v3 (l2=0.01, threshold=0.30, sweep floor) | 48 | 43.75% | +15,396.00 | 2.17 |
| v4 (l2=0.05, threshold=0.25, true optimum) | 84 | 42.9% | **+38,175.00** | 2.52 |

Comparable win rate, but v4 finds nearly 2.5x the net P&L by keeping
almost twice as many trades — a direct consequence of searching the
threshold space v3's floor had cut off, not a change in what the model
learned.

**Still labeled Open**, same reasoning as v3 — no test-range attempt made
or appropriate. Ledger-recorded as `ml-signal-quality-v4-hyperparam-swept`.
Learning rate is not worth sweeping again in a future round; l2 and (as
this entry shows) the threshold search range both are.

## 2026-08-22 — ML signal-quality filter, v5: rolling-origin (walk-forward) validation

**The question every prior ML entry above left open: does this generalize,
or is it fit to the one static Apr-May 2026 validation window every
attempt (v1-v4) used?** Rolling-origin validation answers this directly:
train fresh on quarters 1..k (l2=0.05, same as v4, plus the new
development-trade-count floor, min 20), validate on quarter k+1 (a range
the model has never seen in any form), slide forward one quarter, repeat.
Six folds across the full extended history:

| Fold | Trained on | Validated on (never seen) | Trades | Win rate | Net P&L | Profit factor |
|---|---|---|---|---|---|---|
| 1 | Oct-Dec 2024 | 2025 Q1 | 142 | 28.9% | +7,770.50 | 1.14 |
| 2 | Oct'24-Q1'25 | 2025 Q2 | 121 | 33.1% | +45,930.50 | 2.29 |
| 3 | Oct'24-Q2'25 | 2025 Q3 | 172 | 35.5% | +13,073.00 | 1.29 |
| 4 | Oct'24-Q3'25 | 2025 Q4 | 24 | 50.0% | **-381.00** | 0.85 |
| 5 | Oct'24-Q4'25 | 2026 Q1 | 28 | 35.7% | +33,980.65 | 4.50 |
| 6 | Oct'24-Q1'26 | 2026 Q2 (Apr-May, = v3/v4) | 84 | 42.9% | +38,175.00 | 2.52 |

**5 of 6 quarters are net profitable, each time on data the retrained
model never saw.** This is meaningfully stronger evidence than any single
static split could give — real, if noisy, generalization rather than a
one-window fit. **The one losing fold (2025 Q4) is not an ML-specific
failure**: the same quarter is where the hand-tuned Strict-RSI55+ATR20
candidate *and* plain Baseline both went negative too (see the
2024-10-03..2025-12-31 historical-extension entry above:
159t/13.8%/-6,618.75 and 51t/29.4%/-3,292.50 respectively, for that exact
window). 2025 Q4 looks like a genuinely adverse regime for this whole
strategy family, not something specific to the ML approach.

**No universal best threshold emerged** — each fold's chosen threshold
(0.23, 0.25, 0.19, 0.37, 0.39, 0.25) differs, tracking that fold's own
validation data. A real deployment would need periodic retraining/threshold
recalibration, not a fixed model. The new development-trade-count floor
(min 20) never actually bound in any fold (smallest `dev_kept` at the
largest tested threshold was still 51) — a non-event here, but the guard
against v2-bigdata's exact failure mode is now in place for a future round
where it might.

**Still labeled Open** — rolling-origin validation is much stronger
evidence than a single split, but it is not this project's one-shot
test-range discipline, and none of these ranges are eligible to become a
`test` role under `research_ledger.py`'s rule regardless. Recorded in the
ledger as `ml-rolling-origin-fold1` through `fold6` (role=screening).

## 2026-08-22 — ML signal-quality filter, v6: open-interest + days-to-expiry features (new v2 engine)

**Context:** the last untested feature idea from this file's original
"not yet tested" list. Open interest and days-to-expiry are properties of
the *selected option contract*, which v1's engine (`upstox_ml_backtest.py`)
only determines *after* the ML decision — using them required a real
architecture change, not a quick feature add.

**New `src/options_bot/upstox_ml_backtest_v2.py`** (deliberately a separate
module, mirroring this project's own precedent of never modifying an
already-tested engine in place): selects each candidate signal's contract
*before* the ML decision instead of after, so open interest (read from the
contract's most recent known candle at or before the signal, real Upstox
data only — resampled/derived candles never carry OI, confirmed empirically:
765,590/765,590 real FIVE_MINUTE rows have it, 0/206,953 derived rows do)
and days-to-expiry are available as features. The one property that must
not change versus v1 — an observation that fails the ML filter is dropped
from the surviving sequence entirely (so it can never define another
trade's exit boundary), while one that passes but has no valid contract or
is expiry-day-excluded *stays in* the sequence and simply produces no trade
of its own — is preserved exactly; a signal's contract is selected once,
deterministically, and reused, so precomputing it earlier cannot change
which signals survive or what boundaries they define for a precontract-only
model.

**Validated before trusting it**, same discipline as the O(n²) fix:
`tests/test_upstox_ml_backtest_v2.py` proves (1) v2 with an always-accept
model reproduces the unfiltered backtest exactly, (2) v2 given the *exact*
scenario that guards v1's core sequencing correctness (the
alternating-signals exit-boundary test) produces byte-identical trades to
v1 for the same precontract-only model, (3) a model that looks *only* at
`open_interest_normalized` genuinely keeps a high-OI contract's signal and
rejects a low-OI one — proving the feature actually reaches the decision,
not just that it's computed and unused — and (4) a signal with no
available contract at all degrades to `open_interest_known=False` rather
than crashing. All pass.

**Trained and compared against v4, same Development/Validation split, same
l2=0.05/lr=0.1/3000-epochs hyperparameters, same threshold sweep range** —
1,012 labeled dev trades, but only 770 (76%) have known open interest (the
rest come from derived-candle-covered contracts, which never carry OI —
see the historical-extension entry above; `open_interest_known` correctly
flags these):

| | Features | Trades | Win rate | Net P&L | Profit factor |
|---|---|---|---|---|---|
| v4 | 7 (precontract only) | 84 | 42.9% | +38,175.00 | 2.52 |
| v6 | 10 (+ open interest, days-to-expiry) | 85 | 43.5% | **+38,383.95** | 2.53 |

**Open interest and days-to-expiry gave almost no improvement** — +208.95
net P&L, +0.6 points of win rate, +0.01 profit factor over v4, at a
different threshold (0.23 vs 0.25). Within noise for this sample size, not
a meaningful lift. Plausible reasons: the 24%-of-rows "unknown OI" gap may
be diluting the feature's signal; or open interest genuinely isn't very
informative for filtering *this* strategy's *already-generated* signals,
as opposed to being useful for something else (e.g. initial contract
selection, which this doesn't touch). Labeled **Open** — a real, honest
negative-ish result on a previously-untested idea, not proof open interest
is worthless, but no reason to prefer v6 over v4's simpler model right now.
`research/models/ml-signal-quality-v6-openinterest.json` saved for
reference. Ledger-recorded as `ml-signal-quality-v6-openinterest`.

## 2026-08-22 — Three alternative strategies, first screening pass

**Context:** everything tested in this file up to now (Baseline, RSI/ATR
filters, all six ML rounds) is a variant of the same idea — follow
`MomentumStrategy`'s trend signal, then filter for quality. This project's
own 2026-08-20 entry already concluded closing the win-rate gap further
"would require a different signal design... not more `BacktestParameters`
tuning." Three genuinely different signal shapes were built and checked
against real data (`src/options_bot/strategy_experimental.py`,
backtest-only, never imported by the live/forward-paper path):

- **`MeanReversionStrategy`** — fades price extremes (Bollinger Bands +
  RSI) instead of following trend. A structurally different edge shape
  from everything else tested.
- **`OpeningRangeBreakoutStrategy`** — trades breaks of each session's
  opening range, a timing rule based on session structure rather than an
  indicator crossover.
- **`TrendConfirmedMomentumStrategy`** — `MomentumStrategy`'s exact rule
  plus a slower macro-trend EMA that must agree, a multi-timeframe-style
  confirmation using differently-scaled EMAs on the same 5-minute series
  (chosen over literally resampling to 15-minute bars because
  `candle_resample.resample_candles` assumes a 1-minute source granularity
  and isn't safely reusable for 5-minute→15-minute aggregation as-is).

Single-range screening pass only (2026-01-01 to 2026-03-31, real data, no
dev/val/test split yet) — a first look to see whether any idea shows
enough promise to justify the full discipline, not a confirmation of
anything:

| Strategy | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Baseline (MomentumStrategy, for reference) | 157 | 21.0% | +69,049.15 | 9,774.50 | 2.71 |
| Mean-reversion | 32 | **0.0%** | -11,468.10 | 11,468.10 | 0.0 |
| Opening-range breakout | 65 | 13.8% | +11,426.35 | 9,878.95 | 1.57 |
| Trend-confirmed momentum | 118 | 22.0% | +51,578.45 | **6,191.45** | 2.70 |

**Mean-reversion lost on every single trade (32/32) — a clean, decisive
rejection**, not a subtle underperformance. Likely cause: it was tested
with the exit shell's default stop mechanics, tuned for trend-following
entries. Mean-reversion setups typically need materially wider stops
(price often moves further against the position before actually reverting
than a trend continuation would), so a trend-tuned tight stop plausibly
cuts these off systematically before reversion plays out. **Rejected as
implemented** — worth revisiting with reversion-appropriate exit
parameters before concluding the underlying idea itself is dead, but not
in scope for this pass.

**Opening-range breakout is real but unremarkable** — profitable, but
weaker than Baseline on every metric (lower win rate, lower net P&L,
similar drawdown, lower profit factor). Labeled **Open**, not compelling
enough on its own to invest further without a specific reason to prefer
this timing rule over momentum's.

**Trend-confirmed momentum is the genuinely interesting result**: nearly
identical win rate (22.0% vs. 21.0%) and profit factor (2.70 vs. 2.71) to
Baseline, but **37% lower drawdown** (6,191.45 vs. 9,774.50) from being
more selective (118 vs. 157 trades — the macro-trend filter rejects
signals that don't have macro-trend agreement). This isn't a new edge, it's
a lower-variance cut of the existing one — captures ~75% of Baseline's
total profit at meaningfully less risk. Labeled **Open**, the strongest
candidate of the three for a proper development/validation/test pass; a
reasonable next step if pursued further.

**Follow-up same day — proper Development/Validation split (Jan-Mar /
Apr-May 2026, matching every other candidate in this file):**

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Baseline (dev) | 157 | 21.0% | +69,049.15 | 9,774.50 | 2.71 |
| Baseline (val) | 92 | 19.6% | +19,716.75 | 6,385.95 | 1.81 |
| Trend-confirmed (dev) | 118 | 22.0% | +51,578.45 | 6,191.45 | 2.70 |
| Trend-confirmed (val) | 74 | 18.9% | **+20,590.15** | 6,279.45 | **2.06** |

**Holds up on validation — no overfitting red flag.** Fewer trades (74 vs.
92), essentially tied win rate, but *higher* net P&L and a meaningfully
better profit factor than Baseline on the out-of-sample split. This is the
opposite of the "strong validation, thin/weak development" shape this
log's own discipline distrusts (v2-bigdata, "Strict RSI + No expiry day")
— here development *and* validation both improve together, on the same
metrics, which is what real generalization should look like.

**Genuinely test-eligible, but no fresh test range currently exists to
spend it on.** Every NIFTY/FIVE_MINUTE range from 2024-10-03 through
2026-08-20 has now been touched by something in this file (screening,
development, validation, or the RSI55/ATR20 candidate's already-spent
test) — `research_ledger.py`'s rule requires a test range to start
strictly after everything ever used, and the archive has no real data
past 2026-08-20 yet. Labeled **Open**, ready for its one test attempt the
moment fresh data exists (a later pull, or accumulated forward-paper
evidence) — not to be tested against a stale or reused range just to have
an answer sooner.

**Second follow-up same day — does stacking the ML filter on top of
trend-confirmed momentum compound the two leads?** Trained a fresh ML
model on `TrendConfirmedMomentumStrategy`'s own signals (not
`MomentumStrategy`'s — 792 labeled dev trades, positive_rate 18.8%), same
Development/Validation split, l2=0.05:

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Trend-confirmed alone (val) | 74 | 18.9% | +20,590.15 | 6,279.45 | 2.06 |
| Trend-confirmed + ML (val, threshold=0.11) | 70 | 20.0% | +22,091.75 | 6,279.45 (identical) | 2.23 |

**Modest, consistent improvement — not a strong compounding effect.**
Every metric is slightly better or exactly unchanged (drawdown is
byte-identical, meaning the ML filter didn't touch whatever trade produced
the worst equity dip), but nowhere near the scale of the ML filter's
effect on plain Baseline (v4: +38,175.00). The chosen threshold (0.11) is
low — the model found comparatively little left to separate out, since the
macro-trend gate already did most of the filtering work upstream. This
echoes the 2026-08-12 "Strict RSI + Morning entries" finding: stacking two
quality filters tends to show diminishing, not multiplying, returns.
Labeled **Open**, same test-range constraint as the entry above (no fresh
range available). `research/models/trend-confirmed-momentum-plus-ml.json`
saved for reference.

**A concrete cost worth flagging:** the unfiltered dev backtest for this
entry took **76 minutes** — `TrendConfirmedMomentumStrategy` has no
precomputed-series fast path (the 2026-08-22 O(n²) fix only covers
`MomentumStrategy`, via `hasattr(strategy, "signal_from_indicators")`).
Building an equivalent fast path for any experimental strategy that gets
used repeatedly going forward would pay for itself quickly.

## 2026-08-22 — Trend-confirmed momentum, parameter sweep: the best result of the whole session

**Context:** the 76-minute cost flagged above was fixed —
`TrendConfirmedMomentumStrategy` gained its own precomputed-series fast
path (`signal_from_indicators_with_macro`, mirroring `MomentumStrategy`'s
own, extended with one more macro-EMA series), proven byte-identical to
the naive per-step approach by a new regression test on 400 candles with
multiple signal flips before being trusted
(`test_trend_confirmed_momentum_fast_path_matches_naive_full_recompute`).
The same Jan-Mar 2026 backtest that took 70s now takes 8s (~9x) — the full
18-month run that took 76 minutes would now take roughly 8-9 minutes. This
made a real parameter sweep practical for the first time.

**8-combination sweep, Development (Jan-Mar) / Validation (Apr-May) 2026,
same exit shell as everything else in this file:**

| Combo | Dev trades/win%/net P&L/PF | Val trades/win%/net P&L/drawdown/PF |
|---|---|---|
| macro=30 | 156/20.5%/+68,640.00/2.69 | 92/19.6%/+15,742.00/6,385.95/1.65 |
| macro=45 | 137/19.0%/+44,684.40/2.22 | 84/20.2%/+19,218.40/6,966.55/1.87 |
| **macro=60 (default)** | 118/22.0%/+51,578.45/2.70 | 74/18.9%/+20,590.15/6,279.45/2.06 |
| macro=90 | 101/17.8%/+44,664.50/2.55 | 57/10.5%/+1,000.55/8,698.40/1.06 |
| macro=120 | 83/19.3%/+34,655.60/2.50 | 57/14.0%/+3,873.55/6,486.05/1.23 |
| fast=5,slow=13 | 145/26.2%/+74,184.45/3.18 | 88/25.0%/+40,200.95/3,797.90/2.91 |
| fast=12,slow=26 | 107/18.7%/+27,272.95/1.99 | 63/19.0%/+4,046.95/5,786.45/1.23 |
| rsi=21 | 110/25.5%/+68,089.95/3.53 | 72/22.2%/+27,212.40/6,280.10/2.47 |

**Two real, consistent levers found: faster EMAs (fast=5/slow=13 instead
of 9/21) and a wider RSI period (21 instead of 14) each independently beat
the default on every metric, on both splits.** Macro-period alone shows a
genuine peak at 60 (both wider and narrower hurt validation, especially
macro=90/120 which collapse toward breakeven) — the original default
pick there wasn't wrong, it's the fast/slow EMA periods that were leaving
value on the table.

**Combining both winning levers compounds rather than showing diminishing
returns** (unlike the ML-stacking entry above) — `fast_period=5,
slow_period=13, macro_period=60, rsi_period=21`:

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development | 132 | 25.0% | +83,187.60 | 3,620.60 | 3.68 |
| Validation | 86 | **26.7%** | **+45,154.00** | **2,844.60** | **3.19** |

**This is the best-evidenced candidate of the entire session.** Against
the original plain-momentum Baseline on the same validation range
(92t/19.6%/+19,716.75/6,385.95/1.81): more than double the net P&L, less
than half the drawdown, meaningfully higher win rate and profit factor.
Validation win rate is even slightly *higher* than development's
(26.7% vs. 25.0%) — an unusual but reassuring shape, the opposite of every
"strong dev, weak val" overfitting pattern flagged elsewhere in this file.

**Labeled Open, not Confirmed — same constraint as every trend-confirmed
entry above: no fresh test range currently exists.** This candidate
(`trend-confirmed-fast5-13-rsi21`) is now the strongest one waiting for
that range to become available. Recorded in the ledger under that name.

## 2026-08-22 — Trend-confirmed momentum, round 2: entry refinement + exit-shell sweep

**Immediate follow-up, same session.** Two more rounds pushed further:
(1) a finer entry-logic grid around the winning region, and (2) — new —
sweeping the exit shell (stop/target/trailing) against the best entry
found, since every trend-confirmed result up to this entry used the plain
tight-stop default (`stop_risk_fraction=0.8`, no target, no trailing) and
had never been combined with the exit mechanics this project separately
validated (2026-08-20 entries).

**Stage A — entry-logic refinement (10 combos, same tight-stop exit as
before, for apples-to-apples comparison):**

| Entry | Val trades/win%/net P&L/DD/PF |
|---|---|
| **fast=5, slow=10, macro=60, rsi=21 (new best)** | 92/27.2%/**+48,782.15**/2,839.40/3.25 |
| fast=5, slow=13, macro=60, rsi=18 | 86/26.7%/+45,808.55/2,844.60/3.22 |
| fast=5, slow=13, macro=60, rsi=21 (prior best) | 86/26.7%/+45,154.00/2,844.60/3.19 |
| fast=5, slow=13, macro=70, rsi=21 | 80/25.0%/+43,247.70/3,800.50/3.23 |
| fast=4, slow=10, macro=60, rsi=21 | 96/26.0%/+43,042.55/3,875.25/2.85 |

Shortening `slow_period` further (10 instead of 13) at the same `fast=5`
found a small additional improvement. Widening or narrowing `macro_period`
away from 60, or `rsi_period` away from ~18-21, both give up value —
confirms the winning region is a real local optimum, not noise (moving in
any direction from it costs something).

**Stage B — exit-shell sweep on the new best entry (11 combos):**

| Exit shell | Val trades/win%/net P&L/DD/PF |
|---|---|
| stop=1.6, no target/trailing | 92/37.0%/**+57,532.45**/4,071.15/2.73 |
| stop=1.6, target=0.30 | 92/43.5%/+56,508.70/3,370.70/2.86 |
| stop=1.3, no target/trailing | 92/35.9%/+54,748.50/3,232.65/2.91 |
| stop=2.0, no target/trailing | 92/39.1%/+53,518.70/4,205.35/2.39 |
| stop=0.8 (tight default, Stage A's result) | 92/27.2%/+48,782.15/2,839.40/3.25 |

**Widening the stop (without adding a target) beats the tight default
here — the opposite lesson from the original 2026-08-20 sweep**, where
widening the stop only helped *combined with* a profit target. On this
tuned entry, adding a target back in (`target=0.30`) barely changes net
P&L (+56,508.70 vs. +57,532.45, under 2% different) but meaningfully cuts
drawdown (3,370.70 vs. 4,071.15) and lifts win rate further (43.5% vs.
37.0%) — the better balanced choice, not just the top number.

**Three final candidates, all dramatically ahead of the original Baseline
(92t/19.6%/+19,716.75/6,385.95/1.81) on every metric:**

| Candidate | Entry | Exit | Val: trades/win%/net P&L/DD/PF |
|---|---|---|---|
| A — best net P&L | fast=5,slow=10,macro=60,rsi=21 | stop=1.6 | 92/37.0%/+57,532.45/4,071.15/2.73 |
| **B — best balanced** | fast=5,slow=10,macro=60,rsi=21 | stop=1.6,target=0.30 | 92/**43.5%**/+56,508.70/**3,370.70**/2.86 |
| C — best risk-adjusted | fast=5,slow=10,macro=60,rsi=21 | stop=0.8 (default) | 92/27.2%/+48,782.15/**2,839.40**/**3.25** |

**Candidate B is the recommended headline pick** — within 2% of the best
net P&L found, but with meaningfully lower drawdown and the highest win
rate of the three. All are still **Open, not Confirmed** — same
constraint as every trend-confirmed entry: no fresh
NIFTY/FIVE_MINUTE test range currently exists past 2026-08-20. Recorded
in the ledger as `entry-refine-*` and `exit-sweep-*` candidates.

## 2026-08-22 — Candidate B across all 7 quarters: the strongest generalization evidence this project has produced

**Immediate follow-up, same session.** Candidate B (entry:
`fast_period=5, slow_period=10, macro_period=60, rsi_period=21`; exit:
`stop_risk_fraction=1.6, target_return=0.30`) was picked and validated on
only two quarters (Jan-Mar dev, Apr-May val). Ran the exact same fixed
parameters, unchanged, across all 7 quarters of the extended history
(Oct 2024 through May 2026) to check whether it holds up outside the
window it was tuned on — mirroring the ML rolling-origin check's logic,
but for a fixed-parameter strategy rather than a retrained model.

| Quarter | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| 2024 Q4 (Oct-Dec) | 140 | 49.3% | +35,871.00 | 4,028.25 | 2.32 |
| 2025 Q1 | 163 | 33.1% | +33,882.00 | 14,724.50 | 1.58 |
| 2025 Q2 | 175 | 34.9% | +59,459.00 | 6,665.00 | 2.09 |
| 2025 Q3 | 159 | 45.3% | +27,080.25 | 5,695.25 | 1.55 |
| **2025 Q4 (Oct-Dec)** | 136 | 42.6% | **+26,884.25** | 8,356.75 | 1.64 |
| 2026 Q1 (= dev range) | 136 | 40.4% | +64,596.85 | 5,102.60 | 2.47 |
| 2026 Q2 (= val range) | 92 | 43.5% | +56,508.70 | 3,370.70 | 2.86 |
| **Total, 7 quarters** | **1,001** | **~40.9%** | **+304,282.05** | — | — |

**Every single quarter is net profitable — no exceptions.** Most notably,
**2025 Q4 is profitable here** (+26,884.25), the exact same quarter where
both the plain momentum Baseline and the ML-filtered model (v5 rolling-
origin entry above) had their one loss of the whole session. Win rate
stays in a tight, sane 33-49% band throughout — no quarter collapses
toward zero or goes deeply negative, and no quarter shows the "great win
rate, thin sample" pattern that would make a single good-looking quarter
suspect.

**Two honest caveats, not glossed over:** the first three quarters
(2024 Q4 - 2025 Q2) show real archive gaps (22/44/296 respectively) and
draw on the mix of real and derived-from-1-minute data documented in the
historical-extension entry above — 2025 Q3 onward is clean, zero-gap,
real-only data. And this remains **exploratory/screening evidence, not
this project's formal one-shot test** — every one of these 7 ranges has
already been touched by dev/val/screening work today, so none of them is
eligible to become candidate B's actual `test` confirmation under
`research_ledger.py`'s rule. That confirmation still needs a genuinely
fresh range.

**This is nonetheless the strongest piece of evidence any candidate in
this entire research effort has produced** — stronger than the ML
rolling-origin check (5/6 quarters positive), stronger than any single
dev/val split. Labeled **Open**, and the clear frontrunner for whichever
test range becomes available next. Recorded in the ledger as
`trend-confirmed-candidateB-extended-check`.

## 2026-08-22 — Candidate B, per-trade loss post-mortem: a real (modest) fix found and validated

**Immediate follow-up, same session, in response to a direct request to
analyze every losing trade rather than just the aggregate numbers.**
Pulled the exact signal-time features (RSI, normalized ATR, normalized EMA
gap, confidence, minutes-since-open, day-of-week) plus entry premium and
exit reason for all 92 validation-range trades, split winners vs. losers.

**What did *not* discriminate winners from losers:** RSI (winners
mean 50.1 vs. losers 50.5), confidence (0.558 vs. 0.559), normalized ATR,
normalized EMA gap, minutes-since-open, day-of-week — all nearly
identical between the two groups. Consistent with why the ML filter
(built on these same 7 features) only ever found a modest edge: there's
no strong signal hiding in these particular features for this candidate.

**What did discriminate: entry premium.** Win rate by premium bucket:
₹0-20 → 35.7%, **₹20-100 → 66.7%**, ₹100-300 → 39.5%, ₹300-1000 → 40.0%.
Digging into the 9 losing trades under ₹20: 6 of 9 exited via
`signal-reversal`, not `stop` — the stop-loss never fired. Root cause: the
points-based stop distance (a fixed rupee risk budget ÷ lot size, ~9
points here) can exceed a cheap option's entire premium, so the stop is
mathematically incapable of triggering before the option approaches
worthless. One trade (entry ₹6.17 → exit ₹6.23, price *rose* ~1%) still
booked a net loss of -36.10, purely because the flat ₹40 round-trip fee
exceeded the entire gross gain (₹3.90) — a structurally disadvantaged
trade type, not a strategy flaw.

**Added `BacktestParameters.minimum_option_premium`** (new field,
`upstox_backtest.py`'s trade-construction loop skips a trade whose
selected contract's entry price is below this) and tested it honestly —
re-running the *full* backtest with the filter applied, not just summing
the bucket by hand (which would double-count and miss that the ₹0-20
bucket also contained 5 *winning* trades that a naive "just remove the
bucket" calculation would silently lose credit for):

| `minimum_option_premium` | Val trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| None (candidate B) | 92 | 43.5% | +56,508.70 | 3,370.70 | 2.86 |
| **≥ ₹20 (recommended)** | 78 | **44.9%** | **+57,125.90** | 3,370.70 | **3.00** |
| ≥ ₹30 | 74 | 43.2% | +56,290.10 | 3,370.70 | 3.02 |
| ≥ ₹50 | 70 | 41.4% | +54,153.65 | 3,370.70 | 2.96 |

**Honest framing, not oversold:** this does not "convert losses into
wins" — 14 trades (9 losers + 5 winners) are removed entirely, and the
net effect at the best threshold (₹20) is a modest, genuine improvement:
win rate +1.4 points, net P&L +1.1%, profit factor +5%, and **drawdown is
completely unchanged** at every threshold tested — the worst equity dip
in this range never came from a cheap-premium trade in the first place.
Past ₹20-30 the filter starts removing profitable trades along with the
bad ones and results degrade. This is real risk hygiene (same or better
on every metric, for free) — not a breakthrough, and the earlier
₹20-100 bucket's 66.7% win rate on its own would have overstated the
achievable improvement had it not been checked with a real re-run.

**Candidate B, updated recommendation:** entry
`fast_period=5, slow_period=10, macro_period=60, rsi_period=21`; exit
`stop_risk_fraction=1.6, target_return=0.30, minimum_option_premium=20`.
Still labeled **Open** — same test-range constraint as every entry above.
New regression test `test_run_upstox_backtest_minimum_option_premium_skips_cheap_contracts`
locks in the filter's behavior.

## 2026-08-22 — Known-event calendar: tested and rejected for candidate B (opposite of the hypothesis)

**Context:** in response to a direct question about whether scheduled
macro events (RBI rate decisions, US Fed announcements, the Union Budget)
should be avoided, built `src/options_bot/market_events.py` — verified
(web search, 2026-08-22) RBI MPC and FOMC announcement dates plus Union
Budget dates, Oct 2024 through Dec 2026. New `BacktestParameters.exclude_macro_event_days`
skips a signal on, or the trading day after, a known event date. Deliberately
scoped to *scheduled* events only — no historical news corpus or NLP
pipeline exists (or is planned; a live NLP pipeline wouldn't fit the
memory-constrained Termux runtime this project ultimately targets).

**Cross-referenced candidate B's actual trades against the calendar
first, before adding any filter** — validation range (small sample, 7
trades): event-window trades won 57.1% vs. 43.7% on ordinary days. Full
extended history (825 trades, 75 in event windows — a real sample):
**event-window trades average ₹696.79 per trade vs. ₹341.76 on ordinary
days — more than double, not worse.** Makes sense in hindsight: this is a
directional momentum strategy with a 30% profit target, and bigger
volatility days produce bigger moves, which means more chances to hit
target before stop.

**Honest re-test confirms it: applying `exclude_macro_event_days=True`
makes every metric worse** (validation: 78→71 trades, net P&L
+57,125.90→+48,556.15, profit factor 3.00→2.81, drawdown unchanged).
**Not added to candidate B's recommendation** — the opposite of the
naive "avoid news days" intuition, for this specific candidate. The
calendar infrastructure stays (tested, correct, and now known to point
the other direction — a future idea is a filter that *favors* rather than
avoids these trades, though at ~9% of trades it's a minor lever either
way, not a primary strategy). Regression tests:
`tests/test_market_events.py` and
`test_run_upstox_backtest_exclude_macro_event_days_skips_known_event_dates`.

## 2026-08-22 — ML signal-quality filter, v7: macro-event feature added, negligible effect

**Immediate follow-up, same session.** Added `is_macro_event_window` as an
8th feature to `ml_features.FEATURE_NAMES` (existing saved models
unaffected -- each carries its own `feature_names` tuple, not this
module-level constant). Motivated directly by the candidate-B finding
above (event-window trades averaging 2x the per-trade P&L), but tested
here on the *original* `MomentumStrategy` base signal (same exit shell as
v3-v6: `stop=1.6, target=0.50, trailing=0.20`), not candidate B's own
combination -- these are two separate research threads.

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| v4 (7 features, l2=0.05, threshold=0.25) | 84 | 42.9% | +38,175.00 | 3,342.55 | 2.52 |
| v7 (8 features incl. macro-event, same hyperparameters) | 83 | 43.4% | +38,707.05 | 3,342.55 | 2.57 |

**Essentially no improvement** — +0.5 points of win rate, +₹532.05 net
P&L (1.4%), identical drawdown, well within noise for this sample size.
Unlike its clear effect on candidate B's raw strategy performance, this
feature adds almost nothing to the ML filter. Most likely explanation:
the model already has `atr_normalized` as a feature, and scheduled macro
events are precisely the kind of thing that shows up as elevated ATR --
the new feature is largely redundant with information the model could
already infer indirectly. Labeled **Open**, same as every ML candidate --
not a meaningful win, but not proof the feature is useless for a
different base strategy either.  `research/models/ml-signal-quality-v7-macroevent.json`
saved for reference.

## 2026-08-22 — Mean-reversion re-test: wider-stop hypothesis refuted

**Follow-up to the "Three alternative strategies" screening above**, which
rejected `MeanReversionStrategy` outright (0.0% win rate, 32/32 losing
trades) but flagged an untested hypothesis: the default exit shell's stop
mechanics are tuned for trend-following entries, and reversion setups
typically need materially wider stops since price often moves further
against the position before actually reverting. That hypothesis was tested
properly this time — 9 exit-shell combinations (`stop_risk_fraction` from
0.8 up to no cap at all, plus target-return and trailing-stop variants at
the wider stop levels), same strategy, same Development (Jan-Mar 2026) /
Validation (Apr-May 2026) split as every other candidate in this file:

| Exit shell | Dev win rate | Dev net P&L | Val win rate | Val net P&L |
|---|---|---|---|---|
| stop=0.8 (original, rejected) | 0.0% | -11,468.10 | 13.0% | **-1,976.25** |
| stop=1.3 | 3.1% | -16,510.80 | 13.0% | -5,360.80 |
| stop=1.6 | 6.2% | -14,857.85 | 13.0% | -7,399.20 |
| stop=1.6, target=0.50 | 6.2% | -14,405.45 | 13.0% | -8,233.80 |
| stop=1.6, target=0.30 | 6.2% | -15,955.70 | 13.0% | -9,197.10 |
| stop=2.0 | 9.4% | -15,801.00 | 13.0% | -9,942.00 |
| stop=2.0, target=0.30, trailing=0.20 | 6.2% | -18,064.30 | 13.0% | -11,017.10 |
| stop=1.6, trailing=0.20 | 6.2% | -14,520.50 | 13.0% | -11,064.55 |
| no stop cap at all | 34.4% | -24,765.80 | 21.7% | -27,464.05 |

**Every single combination lost money on both splits — the wider-stop
hypothesis is refuted, not just untested.** Widening the stop did lift the
win rate off zero (as expected — trades survive longer), but net P&L got
*worse*, not better, in every case: the strategy's losing trades lose by
more once given more room, faster than its (still rare) winners gain.
Removing the stop cap entirely produced the highest win rate of the set
(34.4% dev, 21.7% val) but also the single worst net P&L and by far the
worst drawdown (35,397.75 dev) — a handful of trades were allowed to run
against the position for a very long time. The original tight stop (0.8)
was, unexpectedly, the *least bad* of all nine variants on validation.
**`MeanReversionStrategy` stays Rejected** — not merely "as implemented"
anymore; the specific fix proposed for it has now also been tried and
failed. The underlying Bollinger+RSI reversion signal itself, independent
of exit mechanics, is the more likely place any future revisit would need
to start (e.g. the entry condition may be catching extremes that keep
extending rather than genuinely reverting), not another exit-shell sweep.

## 2026-08-22 — Candidate B: signal confidence and open interest as entry filters

**Two more "already-captured, currently-discarded data" breakdown
dimensions**, listed as untested in this log's own "Ideas proposed but not
yet tested" section: every strategy computes a per-signal confidence score
(0.5-0.95) and discards it after generating the trade, and open interest
is captured on every archived option candle but never used by any
strategy or filter. Added `BacktestParameters.minimum_signal_confidence`
and `minimum_open_interest` and swept both against candidate B (`fast=5,
slow=10, macro=60, rsi_period=21`, `stop=1.6, target=0.30,
minimum_option_premium=20`) on the same Development/Validation split as
everything else in this file.

**Open interest ≥100,000 is a small, genuinely free improvement — added to
the candidate B recommendation.** Development is byte-identical (no
development trade even had OI that low), and on validation it removes
exactly 2 trades, both net losers:

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Candidate B (val, no OI filter) | 78 | 44.9% | +57,125.90 | 3,370.70 | 3.00 |
| Candidate B (val, OI≥100,000) | 76 | 46.1% | +58,586.50 | 3,370.70 | 3.17 |

Same or better on every metric, identical drawdown, for free — the same
shape as the `minimum_option_premium` fix found in the loss post-mortem.
**Added to the candidate B recommendation** (now
`minimum_option_premium=20, minimum_open_interest=100000`).

**More aggressive OI floors (≥500,000 and up) are a real quality/quantity
trade-off, not a further free win.** Win rate and profit factor keep
climbing (OI≥5,000,000: val win rate 58.6%, PF 4.80, drawdown down to
1,331.90), but trade count and absolute net P&L both fall hard (val net
P&L +58,586.50 → +31,139.35 → +30,601.05 → +30,107.60 as the floor rises
from 100k to 5M). **Not adopted beyond the 100,000 floor** — the same
recurring win-rate-vs-total-return tension this project keeps finding;
100,000 is the point where the filter is still removing clear noise
rather than trading away real profit.

**Confidence filtering shows the identical trade-off shape, with no free
tier at all.** Candidate B's own confidence formula only spans roughly
0.52-0.75 for the RSI bands it fires on, so even a modest floor cuts
volume fast: confidence≥0.55 keeps 51/78 development trades and 33/78
validation trades, with a real, consistent-direction quality improvement
(val win rate 44.9%→48.5%, PF 3.00→4.13) but a large absolute-profit cost
(val net P&L 57,125.90→35,199.60). Above 0.58 the samples get too thin to
trust (19, 11, 5, 3, 2 development trades) and stop being directionally
consistent between dev and val (e.g. confidence≥0.60: dev win rate drops
to 36.4% while val jumps to 55.6% — noise, not signal). **Not adopted** —
unlike the OI floor, there is no threshold here that improves quality for
free; every gain trades away real total return, and this project's own
recurring finding (tightening filters raises win rate but can shrink total
P&L) applies directly.

## 2026-08-22 — Opening-range breakout: a proper dev/val split reveals the exit shell was hiding it

**`OpeningRangeBreakoutStrategy` only ever had a single-range screening
pass** (2026-01-01 to 2026-03-31, real data, no split) — labeled **Open**,
"real but unremarkable": profitable but weaker than Baseline on every
metric with the default exit shell. Gave it the same Development
(Jan-Mar 2026) / Validation (Apr-May 2026) discipline as every other
candidate, plus two cheap variations: the opening-range window
(3/6/12 bars = 15/30/60 minutes) and swapping in candidate B's winning
exit shell (`stop_risk_fraction=1.6, target_return=0.30`) against the
original default (`stop_risk_fraction=0.8`).

| Opening range | Exit shell | Dev win rate | Dev net P&L | Val win rate | Val net P&L | Val drawdown | Val PF |
|---|---|---|---|---|---|---|---|
| 15min (bars=3) | default (stop=0.8) | 13.8% | +11,426.35 | 11.4% | **-4,228.80** | 7,119.30 | 0.61 |
| 30min (bars=6) | default (stop=0.8) | 14.3% | +21,850.15 | 14.3% | +728.10 | 7,937.25 | 1.07 |
| 60min (bars=12) | default (stop=0.8) | 15.4% | +16,353.35 | 19.4% | +2,839.40 | 5,921.70 | 1.32 |
| 15min (bars=3) | candidate B's (stop=1.6, target=0.30) | 33.8% | +25,062.05 | 37.1% | +19,419.50 | 5,492.70 | 2.34 |
| **30min (bars=6)** | **candidate B's (stop=1.6, target=0.30)** | **38.8%** | **+29,517.55** | **42.9%** | **+22,943.80** | 5,620.15 | 2.75 |
| 60min (bars=12) | candidate B's (stop=1.6, target=0.30) | 38.5% | +19,533.15 | 51.6% | +18,996.45 | **1,493.80** | 2.92 |

**The original "unremarkable" label was largely an artifact of the exit
shell, not the signal — the same root cause as mean-reversion's rejection,
except this time fixing it actually worked.** Every single combination
using the default tight stop is weak (one of them, the exact combination
originally screened, is a net loser on validation: -4,228.80). Swapping in
the same wider, target-driven exit shell that works for candidate B turns
every opening-range-bars variant solidly profitable, with win rate roughly
tripling in each case (e.g. bars=6: 14.3%→38.8% dev, 14.3%→42.9% val).

**`opening_range_bars=6` + candidate B's exit shell is the strongest
result and a genuinely new, real candidate**, not just noise: development
and validation improve *together* on win rate (38.8%→42.9%) and profit
factor (2.52→2.75) — the same "generalizes, doesn't just get lucky" shape
this log trusts, as opposed to weak-dev/strong-val. Net P&L is high and
consistent on both splits (+29,517.55 dev, +22,943.80 val, same order of
magnitude, not a fluke spike). `opening_range_bars=12` has the lowest
drawdown of the six (1,493.80) and highest validation win rate (51.6%) but
on a smaller, more lopsided dev/val win-rate gap (38.5%→51.6%) that is
more likely partly small-sample luck (31 validation trades).

**Labeled Open, not Confirmed — no fresh test range exists for this
candidate either**, same constraint blocking candidate B. Worth carrying
forward as a second real candidate (or a possible ensemble/diversification
partner for candidate B, since it fires on session-open breakouts rather
than mid-day trend confirmation) once fresh data or forward-paper evidence
exists to spend a test attempt on.

## 2026-08-22 — Opening-range breakout across all 7 quarters: a real second candidate, not a lucky split

**Immediate follow-up, same session.** Opening-range breakout
(`opening_range_bars=6`, 30-minute opening range) + candidate B's exit
shell (`stop_risk_fraction=1.6, target_return=0.30`) was picked and
validated on only two quarters (Jan-Mar dev, Apr-May val). Ran the exact
same fixed parameters, unchanged, across all 7 quarters of the extended
history (Oct 2024 through May 2026) — the identical check, same quarter
boundaries, that turned trend-confirmed momentum into "candidate B".

| Quarter | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| 2024 Q4 (Oct-Dec) | 49 | 46.9% | +20,222.00 | 3,362.50 | 2.72 |
| 2025 Q1 | 38 | 44.7% | +23,672.50 | 2,773.50 | 2.98 |
| 2025 Q2 | 37 | 43.2% | +50,241.50 | 4,678.25 | 5.27 |
| 2025 Q3 | 51 | 47.1% | +12,169.50 | 2,773.50 | 1.73 |
| **2025 Q4 (Oct-Dec)** | 39 | 56.4% | **+24,171.00** | 1,816.25 | 3.54 |
| 2026 Q1 (= dev range) | 49 | 38.8% | +29,517.55 | 4,480.65 | 2.52 |
| 2026 Q2 (= val range) | 35 | 42.9% | +22,943.80 | 5,620.15 | 2.75 |
| **Total, 7 quarters** | **298** | **~45.6%** | **+182,937.85** | — | — |

**Every single quarter is net profitable — no exceptions**, same result
shape as candidate B's own 7-quarter check. **2025 Q4 is profitable here
too** (+24,171.00, in fact its second-best win rate of all seven quarters,
56.4%) — the same quarter where plain Baseline, the ML-filtered model, and
candidate B all showed comparatively weaker (though still positive, for
candidate B) results, suggesting this candidate's session-open-breakout
signal shape isn't drawing on the exact same edge as the trend-following
family and may hold up under different market conditions. Win rate stays
in a sane 38.8%-56.4% band throughout, no quarter collapses.

**Same two honest caveats as candidate B's check:** the earlier quarters
draw on the mixed real/derived-from-1-minute data (`include_derived=True`
was used here, same as candidate B's check, for exactly this reason), and
this remains exploratory/screening evidence — every range here has
already been touched by today's dev/val/screening work, so none of it is
eligible to become this candidate's actual one-shot `test` confirmation.

**This is now a second, independently-generalizing candidate, not just a
promising split** — smaller in volume than candidate B (298 vs 1,001
trades total, since it only fires on session-open breakouts rather than
throughout the day) but with a comparable or better win rate (~45.6% vs
~40.9%) and no quarter failures. Labeled **Open**. Recorded in the ledger
as `orb-bars6-candidateBexit-extended-check`. Both candidates now share
the same real constraint: no fresh test range exists yet for either one.

## 2026-08-22 — Candidate B and opening-range breakout: nearly uncorrelated, genuine diversification benefit

**Immediate follow-up, same session.** With both candidates now
independently validated across all 7 quarters, the natural next question:
do they capture the same edge (redundant, no benefit running both) or
different ones (diversifying, smoother combined risk)? Ran each over the
full history (Oct 2024 - May 2026) with its actual recommended
configuration (candidate B now includes both `minimum_option_premium=20`
and `minimum_open_interest=100000`; opening-range breakout is
`opening_range_bars=6` + candidate B's exit shell) and compared their
day-by-day P&L.

| | Trades | Net P&L (full range) |
|---|---|---|
| Candidate B (with both filters) | 623 | +226,792.45 |
| Opening-range breakout | 298 | +181,041.95 |

(Lower trade count/P&L than the unfiltered 7-quarter checks above, as
expected — this run uses the actual filtered recommendation for candidate
B, not the raw signal.)

**Overlap:** of 324 total trading days either strategy was active,
155 had both fire, 80 had only candidate B, 89 had only opening-range
breakout — a meaningful chunk of independent activity, not near-total
overlap.

**Daily P&L correlation: 0.068 — essentially uncorrelated.** Despite both
being NIFTY-options, long-premium, target-driven strategies on the same
underlying and timeframe, their day-to-day results barely move together.
Candidate B fires on mid-day trend confirmation; opening-range breakout
fires on session-open structure — different enough trigger logic to
produce genuinely independent outcomes, not just cosmetically different
entry rules on the same edge.

**Drawdown, separate vs. combined** (a simplified same-day-netted equity
curve from summed daily P&L — not identical to the engine's own
per-trade, intraday `max_drawdown` methodology used everywhere else in
this file, so treat as a diversification-direction indicator, not a
precise like-for-like number):

| | Max drawdown | Net P&L | P&L / drawdown |
|---|---|---|---|
| Candidate B alone | 11,135.75 | +226,792.45 | 20.4x |
| Opening-range breakout alone | 7,524.15 | +181,041.95 | 24.1x |
| **Both combined** | **13,909.25** | **+407,834.40** | **29.3x** |

Combined drawdown (13,909.25) is well below the naive sum of the two
individual drawdowns (18,659.90, what perfectly-correlated worst days
would produce) — direct evidence of the low correlation translating into
real risk reduction, not just a diversification claim on paper. Combined
net P&L nearly doubles candidate B alone while drawdown rises only ~25%,
a better P&L-to-drawdown ratio than either strategy run in isolation.

**Two honest caveats.** First, this is the same exploratory-evidence
range both candidates were already screened/validated on, not a fresh
test — same constraint as everything else blocking either candidate's
formal confirmation. Second, the combined-drawdown figure assumes both
strategies can be capitalized and executed simultaneously without
interfering with each other (separate margin/position sizing) — a
real-world execution question this backtest-only analysis doesn't
address, not a limitation of the correlation finding itself.

**Conclusion: running both candidates together, as independent parallel
signal sources (not merged into one signal), looks like a genuine
diversification win, not just "two profitable strategies stacked."**
Worth carrying forward as the working plan once/if either candidate gets
its formal test confirmation — not a reason to skip that confirmation
process for either one individually.

## 2026-08-22 — Candidate B + OI-aware ML: the learned model did not beat the simple hard filter

**Immediate follow-up, same session.** The confidence/OI sweep above found `minimum_open_interest=100000` as a
free, hand-picked hard-cutoff improvement to candidate B. The natural next question: can a model that gets to
*learn* a smooth relationship from OI (and days-to-expiry) do better than one manually-chosen threshold? Nothing had
combined the OI-aware ML architecture (`upstox_ml_backtest_v2`, precontract + postcontract features) with candidate
B's own signal before — v6 had only ever used it on the plain `MomentumStrategy` baseline. Trained a fresh model
(`research/models/candidateB-ml-v2-openinterest.json`) on candidate B's unfiltered signal (920 labeled development
trades, 712 with known open interest, same Oct 2024–Mar 2026 dev / Apr–May 2026 val split as every other candidate)
with all 10 features (7 precontract including `confidence`, plus `days_to_expiry`, `open_interest_known`,
`open_interest_normalized`), then swept the decision threshold against validation.

| | Trades | Win rate | Net P&L | Invested | Profit % (ROI) |
|---|---|---|---|---|---|
| Candidate B, unfiltered (val) | 92 | 43.5% | +56,508.70 | 1,241,998.55 | 4.55% |
| Candidate B + OI-aware ML, best threshold (0.31, val) | 90 | 43.3% | +56,657.60 | 1,224,076.10 | 4.63% |
| **Candidate B + hard filter `OI>=100,000` (val, already adopted)** | **76** | **46.1%** | **+58,586.50** | 1,160,692.65 | **5.05%** |

**The learned model essentially found nothing — and what little it found is worse than the simple rule already
adopted.** At every threshold below 0.31 the model filtered out zero trades at all (identical 92-trade,
+56,508.70 result repeated 13 times in the sweep — the model's predicted probabilities never dropped low enough to
matter). From 0.31 upward it starts cutting trades, but net P&L and ROI both fall as the threshold rises further
(0.33: +45,941.90/4.17%; 0.35: +44,415.55/4.30%; 0.39: +24,766.65/3.46%) — the familiar quality/quantity trade-off,
not a clean win. The single best point found (threshold=0.31) barely beats doing nothing (+56,657.60 vs
+56,508.70, ROI 4.63% vs 4.55%) and clearly loses to the plain hard `OI>=100,000` cutoff already in candidate B's
recommendation (+58,586.50, ROI 5.05%, on top of a *higher* win rate too).

**Honest interpretation.** The simple univariate rule beat the 10-feature learned model here, most likely because:
the OI/premium relationship candidate B benefits from is a clean, roughly monotonic threshold effect that a direct
sweep finds exactly, while the logistic regression has to split its attention (and its L2 penalty) across 10
correlated features including a fair number of pure noise dimensions for this particular signal (`day_of_week`,
`minutes_since_open`) — diluting rather than sharpening the one relationship that actually mattered. This is a
useful, generalizable lesson for this project, not just a one-off miss: **more features and a learned combination
isn't automatically better than a well-targeted, hand-found threshold, especially when the true relationship is
simple.** **Not adopted** — candidate B's recommendation stays as the plain `minimum_open_interest=100000` hard
filter found in the sweep above, not this model.

## 2026-08-23 — ORB's premium/OI filters don't transfer from candidate B; a much cleaner signal found instead

**Immediate follow-up, per user request to keep improving both candidates.** Candidate B's premium/OI filters were
each found via its own loss post-mortem. Ran the same checks on opening-range breakout (`opening_range_bars=6` +
candidate B's exit shell) for the first time.

**Premium and OI filters do NOT transfer — one actively hurts.** Premium floors are a wash (≥10 gives a marginal
+0.06pp ROI, ≥20 and above are flat-to-negative). Worse, `minimum_open_interest=100000` — a clean free win for
candidate B — **nearly halves ORB's validation net P&L** (+22,943.80 → +11,255.20, ROI 5.0% → 2.68%), by excluding
a single trade that turns out to be one of ORB's *best*, not one of its worst. The per-trade post-mortem confirms
why: winners actually average *higher* OI than losers in aggregate (8.2M vs 3.8M), but the bucket breakdown shows
no clean monotonic relationship — it's noise, not a filterable pattern, for this strategy. **Neither filter is
adopted for ORB.** Lesson generalized: a filter validated on one strategy is not automatically portable to another,
even on the same underlying and timeframe — each needs its own check, not an assumed inheritance.

**The post-mortem found something much cleaner instead: time-of-day.** Winners average 209.7 minutes since session
open (median ~1:15pm); losers average 96.25 minutes (median ~10:00am, right as the 30-minute opening range
finishes forming). Every single loser (19/19) exited via stop; every single winner (12/12) exited via target — a
textbook opening-range "fakeout" pattern, where breakouts taken immediately as the range forms tend to reverse,
and breakouts confirmed later in the session tend to hold.

**Tested directly with an `entry_start` sweep (9:45 through 12:45), not just eyeballed.** Every threshold from
10:15 onward looked dramatic on validation: win rate jumped from 42.9% to 62-69%, ROI from 5.0% to 9-10.8%,
drawdown collapsed from 5,620.15 to ~1,317 — and it held steady across a whole band of cutoffs, not just one lucky
number. **But development got *worse* for every single variant** (net P&L fell from 29,517.55 to the
5,463-8,831 range) — the "weak development, strong validation" shape this project's own discipline treats as
suspicious, not confirmatory, every other time it has shown up. Not trusting one split that points that direction.

**Resolved with the same 7-quarter check that validated candidate B and ORB's core signals — and it rejects the
pattern.** Ran `entry_start>=10:45` (the representative threshold) unchanged across all 7 quarters, Oct 2024
through May 2026:

| Quarter | Baseline trades/win%/net P&L | Filtered trades/win%/net P&L |
|---|---|---|
| 2024 Q4 | 49 / 46.9% / +20,222.00 | 20 / 55.0% / +13,002.75 |
| 2025 Q1 | 38 / 44.7% / +23,672.50 | 18 / 44.4% / +12,697.50 |
| 2025 Q2 | 37 / 43.2% / +50,241.50 | 14 / **28.6%** / +9,727.75 |
| 2025 Q3 | 51 / 47.1% / +12,169.50 | 27 / 48.1% / +6,663.00 |
| 2025 Q4 | 39 / 56.4% / +24,171.00 | 14 / 57.1% / +8,429.50 |
| 2026 Q1 (dev) | 49 / 38.8% / +29,517.55 | 30 / **33.3%** / +5,463.15 |
| 2026 Q2 (val) | 35 / 42.9% / +22,943.80 | 20 / 65.0% / +19,552.80 |
| **Total** | **298 / +182,937.85** | **143 / +75,536.45** |

**The dramatic validation result was a fluke of that one 2-month window, not a real pattern.** Across the full
history, the filter cuts trade count by more than half (298→143) and cuts total P&L by 59% (+182,937.85→+75,536.45)
— worse than proportionally, since average P&L per trade also drops (₹613.89→₹528.23). Win rate only meaningfully
improved in the one validation quarter (65.0%) and 2024 Q4 (55.0%); in two other quarters (2025 Q2, 2026 Q1) it was
**flat or actually worse** with the filter applied, directly contradicting the "later is better" hypothesis in
those windows. **Rejected — not adopted.** This is exactly the scenario this project's dev/val-shape discipline
exists to catch: a striking single-split result that does not survive being checked against more history. The
underlying pattern (early opening-range breakouts reverse more often) may still be real in some quarters, but it
is not a stable, general rule — the loss post-mortem that surfaced it was accurately describing April-May 2026,
not NIFTY options generally.

## 2026-08-23 — Exit-shell re-sweep, post-filter: confirms candidate B's pick, reveals why ORB's differs from it

**Immediate follow-up, same day.** Candidate B's original exit-parameter sweep ran *before*
`minimum_option_premium`/`minimum_open_interest` existed; ORB never got a full systematic exit sweep at all (only
ever compared 2 points). Re-ran the same 11-combo exit grid for both, with candidate B's filters baked in this time.

**Candidate B: the current pick (`stop=1.6, target=0.30`) remains close to optimal, with one honest caveat.**
The single best net-P&L/ROI result on validation is actually `stop=1.6, no target, no trailing` (+59,625.20,
ROI 5.14%, win rate 40.8%) versus the adopted config's +58,586.50/ROI 5.05%/win rate 46.1% — a real but modest
~1.8% P&L edge, bought with 5.3 points of win rate. Both dev and val improve *together* for the no-target variant
(a trustworthy shape, not overfitting), but given the difference is small and the win-rate cost is real, **the
current pick is kept** — win rate matters for an operator watching results day to day, and 1.8% isn't worth trading
away for it. Documented as a legitimate alternative, not adopted.

**ORB: the picture is the opposite, and reveals *why* the current exit shell is the right one, not just that it
happens to work.** Several no-target variants show dramatically higher development P&L (`stop=1.3, no target`:
+71,477.00 dev) — but every one of them collapses on validation (that same variant: only +5,536.15 val, ROI
1.21%, a textbook dev/val instability red flag this project's discipline exists to catch). The best validation
result adds a small trailing stop (`stop=1.6, target=0.30, trailing=0.20`: +23,389.05, ROI 5.1%, win rate 40.0%)
— only ₹445.25 above the currently-adopted config, effectively a tie. **Conclusion: unlike candidate B, ORB does
not tolerate an uncapped target** — letting winners run destabilizes it rather than improving it, likely because a
session-open breakout's move is often mostly captured early, and removing the cap just exposes it to giving profit
back or catching random late-session whipsaws it wasn't designed to trade. The current exit shell is confirmed
correct, and now for an understood reason instead of just "it tested fine once."

## 2026-08-23 — EMA-separation magnitude (trend strength) as a filter: rejected, cleanly

**Continuing the prioritized next-steps list.** Candidate B currently only checks `fast > slow` (direction), not
*how much* — a 0.1-point crossover and a 5-point crossover count identically. Flagged as an untested dimension
since the very first candidate-search phase; never tried until now. Added `ema_gap_normalized` to
`SyntheticObservation` (the normalized `abs(fast-slow)/close` at signal time, computed once in the same O(n) pass
the fast path already does — no new per-step cost) and a `minimum_ema_separation` filter, then swept it against
candidate B's real dev/val split with its adopted premium/OI filters already baked in.

| Threshold | Dev trades/net P&L | Val trades/net P&L |
|---|---|---|
| None (baseline) | 117 / +65,597.35 | 76 / +58,586.50 |
| ≥0.0005 | 23 / +10,969.15 | 22 / +12,721.25 |
| ≥0.001 | 5 / -1,930.30 | 5 / +2,684.70 |
| ≥0.0015 and above | 0-2 trades | 0-2 trades |

**Monotonic degradation at every threshold, on both splits — a clean rejection, not an ambiguous one.** Even the
smallest tested floor collapses trade count by 80% and net P&L by 83-88%; higher thresholds crater to near-zero
trades. **Not adopted.** Most likely explanation: candidate B's RSI-band (52-75 bullish / 25-48 bearish) and
macro-trend-agreement checks already do the quality filtering a strength requirement would attempt — most weak
crossovers that survive both of those are already filtered out, so an additional strength floor mostly just cuts
volume for no offsetting benefit rather than removing a distinct pool of bad trades.

## 2026-08-23 — Real 15-minute multi-timeframe confirmation vs. the same-timeframe proxy: nearly identical

**Continuing the prioritized next-steps list.** Candidate B's "macro trend" has always been a slower EMA (period 60)
on the *same* 5-minute series, adopted specifically because `candle_resample.resample_candles` assumed 1-minute
source data and wasn't safely reusable for 5-minute→15-minute aggregation. Generalized `resample_candles` to accept
`source_bucket_minutes` (defaults to 1, preserving existing behavior exactly) so it can now aggregate candles of any
source granularity, and built a genuine multi-timeframe variant: real 15-minute bars resampled from the 5-minute
archive, a period-20 EMA on those bars (20×15min = 300min, matching the existing period-60-on-5min lookback), looked
up with a no-lookahead pointer walk (a 15-minute bar only becomes usable starting the candle *after* its own bucket
closes) — otherwise identical entry logic (fast=5/slow=10 on 5-min, RSI 21, same bands) and candidate B's exit shell.

| | Trades (dev/val) | Win rate (dev/val) | Net P&L (dev/val) |
|---|---|---|---|
| Candidate B (same-timeframe proxy) | 117 / 76 | 43.6% / 46.1% | +65,597.35 / +58,586.50 |
| Real 15-min multi-timeframe | 117 / 76 | 43.6% / 47.4% | +65,373.10 / +62,345.45 |

**Exactly the same trade count on both splits, win rate within 1.3 points, net P&L within 0.3% on dev and 6.4%
better on val.** The proxy and the real resampled confirmation are, in practice, nearly interchangeable — sensible
in hindsight, since a period-60 EMA on 5-minute bars and a period-20 EMA on 15-minute bars smooth the same
underlying ~300-minute price history two different ways, and both converge to very similar values. **Retroactively
validates the original architectural shortcut with real evidence** rather than leaving it as an untested
assumption, and the generalized resampler is now available infrastructure for any future work that needs genuine
bar aggregation. Not adopted as a replacement for the proxy (no meaningful benefit to justify the added complexity
of maintaining a second candle series), but the equivalence itself is the useful result.

## 2026-08-23 — Cross-confirmation: gating ORB with candidate B's macro trend, tested and rejected

**Continuing the prioritized next-steps list.** The two candidates are known to be nearly uncorrelated and running
them independently in parallel already gives a diversification benefit (see the 2026-08-22 "nearly uncorrelated"
entry). Tested the *other* kind of combination: using one candidate's state to gate the other's entries, rather
than running both unconditionally. Specifically: only take an ORB breakout signal if it agrees with candidate B's
own macro-trend direction (`close` vs. the period-60 EMA on the 5-minute series) at that moment. Composed ORB's
real `evaluate()` unchanged with a trend-agreement gate on top — no duplicated breakout logic.

Single dev/val split showed a real conflict: development improved on every metric (trades 49→43, net P&L
+29,517.55→+32,396.55, drawdown 4,480.65→3,523.95), but validation got worse on every metric (trades 35→33, net
P&L +22,943.80→+17,271.30, ROI 5.0%→3.91%) — the classic "looks good where it was inspected, weaker out-of-sample"
overfitting shape. Resolved with the same 7-quarter check used throughout this project:

| Quarter | Baseline net P&L | Trend-gated net P&L |
|---|---|---|
| 2024 Q4 | +20,222.00 | +13,794.75 |
| 2025 Q1 | +23,672.50 | +21,791.50 |
| 2025 Q2 | +50,241.50 | +27,554.25 |
| 2025 Q3 | +12,169.50 | +7,513.75 |
| 2025 Q4 | +24,171.00 | +21,025.25 |
| 2026 Q1 (dev) | +29,517.55 | **+32,396.55** |
| 2026 Q2 (val) | +22,943.80 | +17,271.30 |
| **Total** | **+182,937.85** | **+141,347.35** |

**Worse in 6 of 7 quarters — the one improvement was exactly the quarter (2026 Q1) the rule happened to be
inspected against.** Total P&L falls 22.7% (298→274 trades). Both remain profitable every quarter (never
catastrophic), so this isn't a dangerous idea, just a strictly worse one than running ORB ungated. **Rejected —
not adopted.** Running both candidates independently in parallel (already established as beneficial) remains the
right way to combine them; gating one with the other's state actively hurts rather than sharpening the combined
signal.

## 2026-08-23 — Dynamic exits (trailing-activation): tested, does not beat the hard target

**Direct response to a user request: "follow-up instead of a hard sell" — let a position run further when the
trend is still favorable, and let the exit trail behind it, instead of exiting the instant a fixed target is
touched.** Added `trailing_activation_return` to `BacktestParameters`: `trailing_stop` now only starts ratcheting
once the position has actually reached this unrealized return, instead of from the very first candle (which could
clip a winner on ordinary early noise before it had proven itself). Swept several activation/trail-width
combinations against candidate B's real dev/val split, `target_return=None` throughout (no hard cap at all):

| Config | Dev net P&L | Val net P&L |
|---|---|---|
| Current (hard target 30%) | +65,597.35 | +58,586.50 |
| No target, no trailing (ride to reversal/force-exit) | +81,322.15 | +59,625.20 |
| Trail 20%, activate at +30% | +43,494.75 | +47,765.95 |
| Trail 10%, activate at +25% | +30,810.65 | +29,673.85 |
| Trail 15%, activate at +25% | +36,185.50 | +26,393.95 |
| Trail 10%, activate at +15% | +23,624.90 | +27,960.45 |

**Every trailing-activation variant underperforms both the current hard target and simply removing the target
entirely.** Win rate rises noticeably with trailing (55-59% vs. the baseline's ~44-46%), but net P&L falls — the
opposite direction of what the idea was meant to achieve. Likely explanation: option premiums move far more, in
percentage terms, than the underlying index does, so a 10-20% trailing width gets triggered by ordinary premium
volatility well before a genuinely large move has played out — the trailing stop protects a smaller profit more
often instead of letting the big winners fully develop. **Not adopted.** The user's underlying instinct (avoid
a hard cap, let winners run) is directionally supported by evidence — "no target, no trailing" is a small, real
improvement over the hard 30% target on both dev and val — but this specific mechanism (percentage trailing stops
at these widths) is the wrong way to implement it for this instrument. A trail width closer to the option's own
typical volatility (likely much wider, or points-based rather than percentage-based) would be the next thing to
try if this is revisited.

## 2026-08-23 — Short strangle (non-directional, sell premium): first backtest, real numbers, needs a 7-quarter check

**Direct response to a user request: strategies that make money when the market isn't moving, not just when it
moves a lot.** Everything tested in this project up to now buys a single option — max loss is the premium paid,
capped and known upfront, and profit requires the underlying to move far enough. A short strangle is the opposite
kind of bet: sell an out-of-the-money call and put, collect the combined premium, and profit if the underlying
stays inside a range (or the premium decays before it doesn't) — but the risk shape is fundamentally different
too: a short option's loss is not capped by anything paid upfront, unlike every long-only strategy this project
has tested. Built `src/options_bot/short_premium_backtest.py` (a new, parallel engine — not a variant of the
long-only one, since the position mechanics are genuinely different) implementing this as a once-per-day entry at
a fixed time, evaluated close-to-close (not intrabar high/low peeking — see the module's docstring for why summing
independent legs' intrabar extremes would overstate the worst case).

**A real, immediate data-coverage limit was hit and worked around, not ignored:** the archive's OTM strike
coverage turned out to be narrow and asymmetric (built around what the long-only strategies actually selected
historically, not a full option chain) — a spot-1% OTM call had zero archived data on the sample day checked,
while the same-distance put had a full day of candles. Confirmed by direct query before writing off the result,
not assumed; the strike-distance grid was scaled down to 0.1%-0.4% (roughly one to two real strike increments)
to match what the archive actually has real data for.

| Strike distance | Dev trades/win%/net P&L | Val trades/win%/net P&L |
|---|---|---|
| 0.1% | 19 / 36.8% / -29,934.10 | 9 / 77.8% / +9,481.10 |
| 0.2% | 17 / 47.1% / -17,604.80 | 9 / 77.8% / +10,031.00 |
| 0.3% | 16 / 37.5% / -17,275.20 | 8 / 75.0% / +7,057.30 |
| 0.4% | 14 / 50.0% / -10,515.75 | 6 / 83.3% / +7,140.60 |

**Every single combination loses money on development and makes money on validation — striking, and consistent
across every parameter choice tried, which is itself informative but not yet trustworthy.** This is the same
"weak development, strong validation" shape this project's discipline has flagged as suspicious every other time
it appeared this session (the ORB entry-time filter, the ORB trend-gate) — both of those turned out to be flukes
of one window once checked against more history. The consistency *across every parameter combination* here
(rather than one lucky threshold) is a bit more encouraging than those cases, and a plausible story exists (Jan-Mar
2026 may simply have been more volatile — bad for short premium — while Apr-May was calmer), but a plausible story
is not evidence. **Labeled Open, not Confirmed or even Exploratory yet — needs the same 7-quarter check that
resolved every other ambiguous result this session before it can be trusted either way.** Sample sizes are also
small (14-19 dev trades, 6-9 val trades) given only one entry per day. Not adopted, not rejected — genuinely the
next thing to check, not a finished result.

**Return-on-premium figures (e.g. val return_on_premium ≈6-8%) are NOT comparable to the long-only candidates'
return-on-capital figures** — premium collected is what changes hands upfront, not the real margin requirement for
holding a short position (typically several times larger, via SPAN + exposure margin), which this backtest does
not model. See `short_premium_backtest.py`'s docstring for the full caveat.

**Immediate follow-up, per user request — the 7-quarter check, and it resolves the dev/val split honestly, not in
the strategy's favor.** Ran the best validation config from the sweep (`strike_distance_pct=0.002,
stop_multiple=2.0, target_fraction=0.5`) unchanged across all 7 quarters:

| Quarter | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| 2024 Q4 | 5 | 80.0% | +5,982.50 | 1,054.00 | 6.68 |
| 2025 Q1 | 10 | 60.0% | +21,357.25 | 9,015.25 | 3.13 |
| 2025 Q2 | 9 | 55.6% | -2,844.00 | 11,179.00 | 0.85 |
| 2025 Q3 | 24 | 58.3% | -5,003.25 | 9,930.50 | 0.69 |
| 2025 Q4 | 16 | 56.2% | -208.70 | 6,504.50 | 0.98 |
| 2026 Q1 (dev) | 17 | 47.1% | -16,073.40 | 18,002.50 | 0.32 |
| 2026 Q2 (val) | 9 | 77.8% | +10,031.00 | 2,028.70 | 5.42 |
| **Total** | **90** | — | **+13,241.40** | — | — |

**Only 3 of 7 quarters profitable, and the total is thin (₹147/trade average) relative to the drawdowns along the
way.** This is not the same shape as candidate B or ORB, both of which were profitable in literally every quarter
tested — the short strangle bounces between strongly profitable (2024 Q4, 2025 Q1, 2026 Q2: profit factor 3-7) and
losing (2025 Q2/Q3, 2026 Q1: profit factor 0.3-0.85) with no obvious pattern tying the good quarters together
(oldest, newest, and one middle quarter won; the rest lost). The striking dev/val split that motivated this check
was not a stable "calm periods favor short premium" regime as hypothesized — it was closer to this strategy's
normal noise, and the two-quarter window happened to land on one losing and one winning quarter, same as several
other quarter-pairs in the full history would have.

**Rejected as currently configured — real evidence, not a coin-flip guess, but not a confirmed edge either.**
The underlying idea (sell premium, profit from range-bound decay) is not disproven in principle — a fixed daily
entry time with no market-condition awareness at all (no volatility regime filter, no signal for whether the day
actually looks range-bound) is a genuinely simple first version, and the next thing worth trying, if this is
revisited, is adding some form of entry selectivity rather than entering every single day unconditionally.

## 2026-08-23 — Short strangle, selective deployment: keeps the strategy as a tool, only on calm-looking days

**Direct follow-up per user correction: "keep this strategy also, for the days when you feel like use this."**
The unconditional every-day version was rejected above for lacking market-condition awareness; the actual ask was
never to abandon it, but to deploy it selectively. Added `maximum_opening_range_pct` to `ShortStrangleParameters`
— skip the day's entry if the underlying's opening range (first 30 minutes, same-day, no lookahead since the
strangle itself only enters after that window closes) is wider than this fraction of spot. Threshold grid pulled
from the archive's real opening-range distribution (Jan-May 2026: min 0.18%, median 0.48%, p75 0.61%, max 2.24%),
not guessed.

**Dev/val sweep found a clean, monotonic pattern: tighter filtering → better development results** (unfiltered
-16,073.40 → +819.50 at the tightest 0.3% threshold), but the tightest thresholds leave validation's 2-month
window with only 1-2 trades, too thin to trust on their own. The 0.5% threshold was the best balance (full-ish
samples on both splits) and was carried to the 7-quarter check, the same resolution method used for every other
ambiguous result this session:

| Quarter | Baseline | Selective (≤0.5% opening range) |
|---|---|---|
| 2024 Q4 | 5t / +5,982.50 | 1t / +3,910.75 |
| 2025 Q1 | 10t / +21,357.25 | 6t / +21,733.50 |
| 2025 Q2 | 9t / -2,844.00 | 4t / -8,969.00 |
| 2025 Q3 | 24t / -5,003.25 | 22t / -2,603.75 |
| 2025 Q4 | 16t / -208.70 | 14t / **+736.55** |
| 2026 Q1 | 17t / -16,073.40 | 14t / **-5,314.45** |
| 2026 Q2 | 9t / +10,031.00 | 2t / +3,629.50 |
| **Total** | **90t / +13,241.40, 3/7 profitable** | **63t / +13,123.10, 4/7 profitable** |

**Honest result: this is a risk trade, not a return improvement.** Total net P&L is essentially unchanged (a
wash, marginally lower) on 30% fewer trades, and one more quarter turns profitable (4/7 vs 3/7). But the real
finding is in the worst quarter: 2026 Q1's drawdown falls from 18,002.50 to 7,243.55 — a 59.8% reduction — while
its loss shrinks by two-thirds (-16,073.40 → -5,314.45). The filter doesn't make the strategy more profitable; it
makes its worst outcomes meaningfully less bad, at the cost of also trimming some winning days in the best
quarters (2024 Q4, 2026 Q2) where it turns out "not calm-looking" days still won. **Labeled Open — a genuine,
real improvement in consistency and worst-case risk, not yet a confirmed edge in total return.** Kept as an
available, tested tool (not deployed by default) rather than discarded — exactly what was asked for: use it on
the days it looks suited to, not every day, and not never.

## 2026-08-23 — Rs 1,00,000 compounding-month simulation

## 2026-08-23 — Rs 1,00,000 compounding-month simulation: a naive version caught and corrected before reporting

**Direct response to a user request: if Rs 1,00,000 had been invested and compounded through real trades, what
would it be worth after a month?** Ran candidate B and ORB's actual April 2026 trades (both individually and
merged into one chronological account) and applied each trade's own return % to a running balance.

**First attempt used 100%-of-balance position sizing and produced an obviously wrong answer: +1,465% for candidate
B alone in one month, +2,057% combined.** This is a real trap in naive compounding simulations — individual option
trades can legitimately return 25-30% (leverage is the whole point of options), and if the *entire* growing
balance is restaked on every single trade, thirty-odd trades compounding at that rate explodes into a number no
real trader would ever risk or achieve, since real position sizing is fixed-lot-size constrained and no one puts
100% of their account on one option position repeatedly. **This number was not reported as an answer** — it was
caught as unrealistic and replaced with a proper position-sizing model before showing anything.

**Redone with 5% of current balance risked per trade** (a moderately aggressive but plausible sizing rule) for
April 2026 (2026-04-01 to 2026-04-30):

| | Trades | Final balance | Return |
|---|---|---|---|
| Candidate B alone | 32 | Rs 1,17,044.96 | +17.04% |
| ORB alone | 16 | Rs 1,02,466.09 | +2.47% |
| Combined (same account, chronological) | 48 | Rs 1,19,931.40 | +19.93% |

Full sensitivity table across sizing assumptions (1%, 2%, 5%, 10%, 25%, 50%, 100%) is in the script output —
returns scale roughly with sizing aggressiveness as expected, from +3.22% (candidate B, 1%) up through the
already-rejected +1,465.65% (candidate B, 100%).

**Two honest caveats, stated plainly.** First, April 2026 was a strong month in this backtest for both
candidates — this is not a claim that every month looks like this; the 7-quarter checks elsewhere in this file
already show real quarter-to-quarter variance (candidate B's own quarterly net P&L ranges from +26,884.25 to
+64,596.85 at fixed 1-lot sizing). Second, this methodology applies each trade's own % return to the compounding
balance, assuming the same percentage move would hold at any balance level — real NIFTY lot sizes are fixed
integers, so true compounding would move in lot-sized steps, not smoothly; this is a standard simplification for
this kind of what-if simulation, not a claim that every intermediate balance was actually tradeable at those exact
lot counts.

## 2026-08-23 — Data-integrity bug found and fixed: every short-strangle result above was computed on mixed-timeframe data

**Found while building the "test what worked best, combined" three-way portfolio check.** Every trade in the loss
post-mortem showed an identical 335-minute holding duration — winners and losers alike, no exceptions. That's the
signature of a bug, not a real pattern: real stop/target-driven exits should vary. Traced it to
`short_premium_backtest.py`'s option-leg candle queries, which never filtered by `timeframe` — every prior
short-strangle backtest this session silently mixed `ONE_MINUTE` and `FIVE_MINUTE` candles for the same contract
(confirmed directly: one sample contract/day had 375 ONE_MINUTE rows + 75 FIVE_MINUTE rows, all pulled together,
450 total instead of the intended 75). Fixed by adding `AND timeframe=?` to both leg queries, matching the
convention every other query in this engine (and `upstox_backtest.py`) already followed. Added a regression test
that seeds deliberately different prices on the two timeframes so this class of bug fails loudly, not silently, if
it recurs. Full test suite (274 passing) and ruff both clean after the fix.

**Every short-strangle number reported earlier today needed re-verification, not just an apology.** Re-ran the
baseline 7-quarter check, the selective 7-quarter check, the three-way portfolio, and the post-mortem, identical
parameters, only the bug fixed:

| Quarter | Baseline (corrected) | Selective (corrected) |
|---|---|---|
| 2024 Q4 | 5t / +5,684.00 | 1t / +3,487.75 |
| 2025 Q1 | 10t / +19,718.50 | 6t / +21,372.00 |
| 2025 Q2 | 9t / -4,265.25 | 4t / -9,126.50 |
| 2025 Q3 | 24t / -4,664.25 | 22t / -2,504.00 |
| 2025 Q4 | 15t / **+3,648.75** | 13t / +4,282.00 |
| 2026 Q1 | 17t / -17,096.50 | 14t / -6,104.85 |
| 2026 Q2 | 9t / +4,928.50 | 2t / +2,878.75 |
| **Total** | **89t / +7,953.75, 4/7 profitable** | **62t / +14,285.15, 4/7 profitable** |

**The corrected numbers are actually more favorable, not less — an honest surprise given bugs more often overstate
results than understate them.** 2025 Q4 flipped from a small loss (-208.70) to a small gain; baseline's own
profitable-quarter count rose from 3/7 to 4/7 (matching selective's own count, which stayed at 4/7). Most
notably: **selective net P&L (+14,285.15) now exceeds baseline's (+7,953.75) outright**, on 30% fewer trades —
not just a risk trade anymore, a real return improvement too, alongside the already-known 57.8% worst-quarter
drawdown reduction (19,002.20 → 8,010.55, both figures also revised down slightly by the fix but the *reduction
percentage* holds).

**Three-way portfolio (candidate B + ORB + selective strangle), full range, re-verified:**

| | Trades | Net P&L | Drawdown |
|---|---|---|---|
| Candidate B alone | 623 | +226,792.45 | 11,135.75 |
| ORB alone | 298 | +181,041.95 | 7,524.15 |
| Short strangle alone | 62 | +14,285.15 | 23,944.00 |
| Candidate B + ORB | 921 | +407,834.40 | 13,909.25 |
| **All three combined** | **983** | **+422,119.55** | **13,909.25** |

Correlations: candidate B vs. ORB 0.071 (matches the known 0.068), candidate B vs. short strangle **-0.021**, ORB
vs. short strangle **0.016** — the strangle is essentially uncorrelated with *both* existing candidates, a genuine
third diversifier, not overlap with days they already handle. **Adding the strangle to the B+ORB portfolio
increases total profit by 3.5% while the portfolio's own max drawdown does not increase at all** (13,909.25,
identical with or without the strangle) — its own worst days evidently don't coincide with the combined
portfolio's worst drawdown period. This is the strongest evidence yet that all three belong together as
independent parallel signals.

**One more honest finding from the corrected post-mortem: the stop/target mechanism essentially never fires.**
All 62 trades — winners and losers alike — exit via `force-exit` at exactly 335 minutes (the full session, 9:45 to
15:20). `stop_multiple=2.0` and `target_fraction=0.5` are wide enough that combined premium on these near-ATM,
short-dated legs rarely swings 2x or decays to half within one day — the "risk management" these parameters imply
isn't actually doing anything right now; every trade is really just "hold from entry to end of day." Tightening
these thresholds (a stop/target that can actually engage intraday) is a real, untested next lever, separate from
the opening-range selectivity already added.

## 2026-08-23 — The flip side: a wide opening range as a filter for candidate B / ORB, tested and rejected

**Continuing the "build on top of what worked" list.** The short strangle benefits from a *narrow* opening range
(calm days). Tested the natural flip side: does a *wide* opening range predict good days for the trend/breakout
strategies instead? Added `minimum_opening_range_pct` to `BacktestParameters` (no lookahead — a signal observed
before the opening range has actually closed is skipped outright, not evaluated against a partial range) and
swept it against both candidates' real dev/val split.

| Threshold | Candidate B val net P&L | ORB val net P&L |
|---|---|---|
| None (baseline) | +58,586.50 | +22,943.80 |
| ≥0.4% | +39,947.25 | +13,243.95 |
| ≥0.5% | +30,906.35 | +8,705.15 |
| ≥0.6% | +13,496.40 | +693.30 |
| ≥0.8% | +1,503.25 | -1,867.60 |
| ≥1.0% | +1,094.50 | -669.85 |

**Monotonic degradation on both strategies, both splits — a clean rejection, no ambiguity requiring a 7-quarter
check.** Every step up in the threshold makes both strategies worse, eventually negative for ORB. **Not
adopted.** Makes sense in hindsight: both strategies already select for trending/breaking days through their own
signal logic (RSI bands, breakout confirmation) — requiring an *additional* wide-open filter on top doesn't
isolate better trending days, it just discards genuine trends that happened to develop gradually rather than
gapping hard in the first 30 minutes, cutting real opportunity rather than sharpening the signal.

## 2026-08-23 — Three-way compounding month: the strangle happened to sit out April entirely

**Extending the earlier Rs 1,00,000 compounding simulation to all three kept strategies, same April 2026 window,
same 5%-of-balance sizing.** The selective short strangle recorded **zero trades in April 2026** — every day that
month failed its own opening-range-width filter (its 2 validation-period trades, visible in the corrected 7-quarter
table above, both fell in May instead). Not a bug: the full-range check already used 19 months of data precisely
because a single calendar month is too short a window to judge a selective, calm-days-only strategy fairly — this
is that limitation showing up directly. Candidate B + ORB alone: Rs 1,00,000 → Rs 1,19,931.40 (+19.93%); adding
the (silent, that month) strangle changes nothing for April specifically. The strangle's real, demonstrated
contribution is in the full-range three-way portfolio numbers above (+3.5% more total profit, zero extra
drawdown, near-zero correlation with both other candidates) — that is the evidence to trust for this strategy,
not any single month's compounding curve.

## 2026-08-23 — Tighter short-strangle stop/target: tested, does not help

**Direct follow-up to the finding that the mechanism never engaged at current widths.** Swept
`stop_multiple` down (2.0 → 1.2) and `target_fraction` up (0.5 → 0.8) — both directions make the mechanism
easier to trip — on top of the already-adopted selective (opening-range-filtered) config.

| stop / target | Dev net P&L | Dev exit reasons | Val net P&L |
|---|---|---|---|
| 2.0 / 0.5 (current) | -6,104.85 | 14 force-exit | +2,878.75 |
| 1.5 / 0.5 | -6,769.80 | 13 force-exit, 1 stop | +2,878.75 |
| 1.3 / 0.6 | -5,469.80 | 12 force-exit, 2 stop | +2,878.75 |
| 1.2 / 0.7 | -7,073.35 | 11 force-exit, 3 stop | +2,878.75 |
| 1.2 / 0.8 | -7,073.35 | 11 force-exit, 3 stop | +2,878.75 |

**Validation is completely unchanged across every single setting tested — the same 2 trades, the same
+2,878.75, every time.** Even the tightest thresholds never once fire differently on those two trades' actual
price paths. Development shows a little more stop engagement (up to 3 of 14 trades) but net P&L gets flat-to-worse,
not better, and win rate drops (50% → 42.9%) as the thresholds tighten. **Not adopted.** Confirms the earlier
read: near-ATM, short-dated strangle premiums on this underlying just don't swing enough within a single day for
stop/target tuning to matter much — the real lever for this strategy is entry selectivity (already built, working),
not exit tuning. This closes out the last open lever from today's list.

## 2026-08-12 — First full-archive run (Jan–Jul 2026, 277 baseline trades)

**Data used:** a real Upstox archive covering 2026-01-01 to 2026-07-31,
transferred once via a temporary GitHub Release for offline analysis
(not committed to the repo — raw trading data doesn't belong in git
history; see "Data used for this log" below).

**Configuration used:** code at commit `387c28b` (tip of `main` before
this doc-only PR). `BacktestParameters` defaults except where a row
below overrides a field (`stop_risk_fraction=0.8` unless noted).
`Settings` at env defaults: `MAX_LOSS_PER_TRADE=400`,
`PAPER_FEE_PER_ORDER=20`, `PAPER_SLIPPAGE_BPS=25`,
`ENTRY_START_IST=09:25`, `ENTRY_CUTOFF_IST=15:00`,
`FORCE_EXIT_IST=15:20`. Lot size comes from each contract's own archived
instrument metadata, not a setting. If any of these defaults change
later, re-running this entry's exact numbers against the same archive
may no longer reproduce them — check this configuration note first
before assuming a discrepancy is a bug.

### Full-range pass (single backtest per variant, no split — context only)

| Variant | Trades | Net P&L | Drawdown |
|---|---|---|---|
| Baseline | 277 | +15,400.35 | 20,097.45 |
| Strict RSI (55/45) | 209 | +15,529.45 | 12,272.85 |
| ATR floor | 260 | +5,541.25 | 25,888.45 |
| Morning entries (9:30-12:00) | 91 | +12,808.90 | 9,466.95 |
| Tuesday-Thursday | 169 | +10,565.75 | 17,515.55 |
| No expiry day | 214 | +4,425.70 | 16,928.30 |
| Tighter stop (0.6) | 277 | -490.20 | 21,650.30 |
| 30-minute hold | 277 | +4,817.05 | 10,338.30 |
| 20% target | 277 | +9,965.70 | 11,805.55 |
| 10% trailing stop | 277 | +922.25 | 21,681.35 |
| No stop-loss cap | 277 | -17,717.15 | 51,285.10 |

A single-range pass like this is only a starting point — it's what
motivated the disciplined split below, not a result to act on by itself.
**Important consequence: this pass already evaluated every variant over
the full Jan-Jul range, including what later gets called the "test"
range (Jun-Jul) below.** That range was not actually untouched by the
time the split ran — it had already contributed to the numbers above.
This is why the "Morning entries" result below is labeled Exploratory,
not Confirmed.

### Development (Jan-Mar) / Validation (Apr-May) / Untouched test (Jun-Jul)

Selection rule: best `validation.net_pnl - validation.max_drawdown`,
evaluated once on the test range, never re-picked afterward.

| Variant | Dev | Validation | Test |
|---|---|---|---|
| Baseline | 68t/+20,517.50 | 86t/-626.80/15,561DD | not selected |
| Strict RSI | 49t/+7,697.05 | 66t/+3,672.80/8,062DD | not selected |
| ATR floor | 68t/+20,517.50 | 83t/-2,719.40/14,893DD | not selected |
| **Morning entries (9:30-12:00)** | 26t/+10,581.35 | 31t/**+2,189.40**/4,370DD | **31t/+1,064.90/5,994DD — EXPLORATORY, see caveat** |
| Tuesday-Thursday | 38t/+14,442.05 | 49t/-1,239.80/10,523DD | not selected |
| No expiry day | 53t/+8,938.45 | 70t/+2,539.75/11,401DD | not selected |
| Tighter stop | 68t/+9,173.05 | 86t/-573.50/11,537DD | not selected |
| 30-minute hold | 68t/-2,632.25 | 86t/+731.05/9,945DD | not selected |
| 20% target | 68t/+11,127.60 | 86t/-936.85/10,366DD | not selected |
| 10% trailing stop | 68t/+12,932.00 | 86t/-6,963.65/11,130DD | not selected |
| No stop-loss cap | 68t/**+27,432.85** (best of all) | 86t/**-15,873.85**/31,605DD (worst of all) | not selected |

**Exploratory, not Confirmed:** Morning entries (9:30-12:00) is the only
variant that survived being picked on the validation range and staying
positive on the designated test range. But per the note above, that test
range (Jun-Jul) was not genuinely untouched — it was already summarized
in the full-range pass before this split ran, and (see Round 2 below) it
was reused again afterward to compare two morning-window variants
against each other. Both of those mean this result does not meet the bar
for Confirmed. It's real, real-looking evidence, worth taking seriously
— but it needs a genuinely fresh period (data this project has not
looked at in any form yet) checked exactly once before it earns that
label. Also worth weighing regardless: it only trades ~1/3 as often as
Baseline (91 of 277 signals fall in that window across the full 7
months), and 31 test trades is a real but modest sample even setting the
leakage concern aside. Not a live-default change candidate yet.

**Rejected — repeated failure across multiple sample sizes.** Full,
auditable numbers from every round this claim is based on (all single-
range full-archive passes, not split runs — same caveat as the pass
above applies, these are exploratory-level evidence individually, but
the *trend across four independent rounds* is the actual finding):

| Round | Trades | Baseline net P&L | Tighter stop net P&L | 10% trailing stop net P&L |
|---|---|---|---|---|
| 2026-07-13 to 2026-07-17 | 11 | +204.15 | +921.10 (better) | +3,875.35 (much better) |
| 2026-07-01 to 2026-07-28 | 56 | -7,966.50 | -8,141.35 (worse) | -928.30 (better, still negative) |
| 2026-06-01 to 2026-07-28 | 121 | -3,799.35 | -8,559.30 (worse) | -5,516.65 (worse) |
| 2026-01-01 to 2026-07-31 | 277 | +15,400.35 | -490.20 (worse) | +922.25 (worse) |

Tighter stop-loss: looked better than Baseline only at the smallest
sample (11 trades), then underperformed Baseline in every subsequent,
larger round (56, 121, 277 trades) — 3-for-3 once past the tiny first
look. 10% trailing stop: also looked much better at 11 trades, was
still better than Baseline (though negative) at 56, then underperformed
Baseline at both 121 and 277 trades. Reject both; do not re-test without
new evidence. "No stop-loss cap" was also catastrophic in every round it
appeared in (-31,096.10 at both 56 and 121 trades, -17,717.15 at 277).

**Rejected — clean overfitting example:** "No stop-loss cap" had the best
development number of any variant tested (+27,432.85) and the worst
validation number of any variant tested (-15,873.85, drawdown more than
doubling). Textbook illustration of why picking on one look-back period
is unsafe. A price-based stop clearly matters; do not remove it.

### Round 2 — combinations and threshold sweeps around the two leads

Same development/validation ranges as above. **Note: the "test" column
here reuses the same Jun-Jul range already touched in the full-range
pass and the first split above — by this point it has been looked at
multiple times and used to compare candidates against each other, so
nothing in this round's test column should be read as a clean,
untouched confirmation either.** Still useful as exploratory evidence
for whether "tighter RSI" and "morning window" generalize or are narrow
coincidences, and whether combining the two leads compounds them — just
not as proof.

| Variant | Dev | Validation | Test |
|---|---|---|---|
| Strict RSI + Morning entries (combined) | 15t/**-5,045.35** | 24t/-291.15/3,744DD | not run (failed dev+val) |
| RSI 58/42 (tighter than 55/45) | 29t/**-8,741.60** | 40t/+967.50/6,574DD | not run |
| RSI 60/40 (tighter still) | 21t/-6,240.20 | 25t/+2,526.25/3,839DD | not run |
| Morning 9:25-11:30 (narrower) | 22t/+12,033.55 | 25t/**-2,905.15**/4,491DD | not run |
| Morning 9:30-13:00 (extended) | 38t/+22,084.75 | 41t/+3,056.90/5,805DD | 51t/**+634.75/8,376DD — weaker than original** |
| Strict RSI + No expiry day (combined) | 38t/**-4,930.55** | 53t/**+5,834.70**/6,508DD (best validation score, auto-selected) | 71t/**-946.55/5,718DD — FAILED** |

**Findings:**
- Combining Strict RSI with Morning entries did not compound their
  individual strengths — development went negative. Stacking filters
  over-restricts the trade set rather than adding up the edges.
- Tightening RSI further than 55/45 (to 58/42 or 60/40) broke development
  in both cases. The original 55/45 threshold looks like a specific
  sweet spot, not a "tighter is better" trend — a mild caution flag on
  how robust that finding really is.
- Narrowing the morning window (9:25-11:30) broke validation. Extending
  it (9:30-13:00) looked *better* on development and validation than the
  original, but performed worse on the reused Jun-Jul range (+634.75 vs
  +1,064.90, with meaningfully higher drawdown: 8,376 vs 5,994). Read
  this as a caution about trusting the pre-test-looking-better version,
  not as proof the original boundary is superior — this comparison used
  the same already-touched range as everything else this round, so it's
  exploratory too, not a clean confirmation either way.
- "Strict RSI + No expiry day" is the clearest false-positive of the
  whole session: it had a negative development period, the single best
  validation score of any candidate tried (which is *why* the mechanical
  selection rule picked it), and then failed outright on the reused
  Jun-Jul range anyway — despite that range already having had a prior
  chance to look favorable to it, which if anything strengthens the
  rejection rather than weakening it. This exposes a real weakness in
  the current selection rule — it
  only scores validation performance, not consistency across development
  *and* validation. Worth keeping in mind before trusting any single
  "selected" variant without also checking its development number.

## 2026-08-20 — Development/validation-only parameter sweep (stop-width + profit-taking), no test range available

**Context:** looking for what parameter changes could meaningfully raise win
rate. This archive's Upstox coverage still ends 2026-07-31 (unchanged since
the 2026-08-12 pull above), and the pre-existing ledger seed already marks
the full Jan-Jul span as touched — so **no candidate in this round is
eligible for a test attempt**. Every result below is development/validation
only, run via `options-bot backtest validate-split` with
`--test-start`/`--test-end` omitted (`dev_validation_only` status), so no
candidate's one test attempt was spent. Per this log's own definitions,
these are **Open**, not Exploratory or Confirmed.

**Data used:** same archive as the entry above (Upstox, 2026-01-01 to
2026-07-31). Development = 2026-01-01 to 2026-03-31, Validation = 2026-04-01
to 2026-05-31 — identical to the ranges already used above, for direct
comparability.

**Sanity check:** re-ran Baseline and Morning entries through the CLI first;
trade counts and net P&L reproduced the table above exactly (68t/+20,517.50
dev, 86t/-626.80 val for Baseline; 26t/+10,581.35 dev, 31t/+2,189.40 val for
Morning entries), confirming methodology is unchanged from the prior entry.

**Actual win rate is far below intuition.** Baseline's real win rate is only
8.8% (dev) / 12.8% (val) — the strategy is net-profitable historically only
because winning trades are much larger than the many small stop-outs, not
because it wins often. This context matters for every result below.

**Stop-width sweep** (`stop_risk_fraction`; baseline=0.8; 0.6 already
Rejected above; `None`/no-cap already Rejected-catastrophic above):

| stop_risk_fraction | Dev win% | Dev net P&L | Val win% | Val net P&L | Val drawdown | Val PF |
|---|---|---|---|---|---|---|
| 0.8 (Baseline) | 8.8% | +20,517.50 | 12.8% | -626.80 | 15,561.00 | <1 |
| 1.0 | 13.2% | +22,263.40 | 12.8% | -5,975.00 | 20,669.35 | 0.81 |
| 1.3 | 13.2% | +16,419.25 | 15.1% | -5,122.20 | 19,459.05 | 0.86 |
| **1.6** | **17.6%** | **+31,443.35** | **17.4%** | **+1,133.40** | **14,528.60** | **1.03** |
| 2.0 | 22.1% | +38,606.35 | 19.8% | -229.65 | 15,601.10 | 1.00 |
| 2.5 | 22.1% | +31,076.75 | 22.1% | -3,508.90 | 16,692.00 | 0.94 |
| 3.0 | 23.5% | +26,518.30 | 25.6% | -1,224.80 | 14,694.95 | 0.98 |

Win rate climbs monotonically with stop width (expected — a wider stop
triggers less often), but validation net P&L only turns positive in a
narrow band around 1.6; both narrower (1.0, 1.3) and wider (2.0+) push
validation back negative. This mirrors the RSI-threshold pattern already in
this log (55/45 was a sweet spot; tighter broke it) — a real local optimum,
not a monotonic "wider is better" trend.

**Combining the 1.6 stop width with profit-taking (target / trailing)
confirms and extends this:**

| Candidate | Dev win% | Dev net P&L | Val win% | Val net P&L | Val drawdown | Val PF |
|---|---|---|---|---|---|---|
| 1.6 stop alone | 17.6% | +31,443.35 | 17.4% | +1,133.40 | 14,528.60 | 1.03 |
| 1.6 + 30% target | 25.0% | +22,522.10 | 26.7% | +229.90 | 10,777.60 | 1.01 |
| 1.6 + 40% target | 23.5% | +26,852.40 | 23.3% | +1,973.85 | 10,658.00 | 1.05 |
| 1.6 + 50% target | 22.1% | +21,034.90 | 22.1% | +4,955.40 | 10,538.40 | 1.12 |
| 1.6 + 60min hold | 20.6% | +5,953.60 | 22.1% | +8,111.80 | 11,607.00 | 1.20 |
| 1.6 + 20% trailing (no target) | 23.5% | +38,202.05 | 23.3% | +2,278.70 | 11,192.25 | 1.06 |
| **1.6 + 50% target + 20% trailing (stacked)** | **23.5%** | **+20,801.55** | **24.4%** | **+6,183.90** | **10,538.40** | **1.17** |
| 1.5 + 50% target | 20.6% | +20,380.35 | 22.1% | +7,130.95 | 9,905.95 | 1.19 |
| 1.7 + 50% target | 23.5% | +25,931.35 | 23.3% | +4,862.45 | 8,873.75 | 1.12 |

"1.6 + 60min hold" posts the best headline validation P&L (+8,111.80) but a
suspiciously weak, inconsistent development number (+5,953.60 — roughly a
quarter of every other candidate in this cluster). This is the same red
flag this log's "Strict RSI + No expiry day" entry already warned about
(strong validation, weak development = don't trust it). Excluded from the
recommendation below on that basis.

Excluding that one, every remaining candidate in the 1.5-1.7 stop-width +
40-50% target region is **positive on both development and validation, with
win rate roughly double-to-triple Baseline (20-27% vs 8.8-12.8%), and
validation drawdown cut by 30-45% versus Baseline (15,561 → 8,874-10,778)**.
This held across 9 nearby parameter combinations, not one lucky point — the
strongest signal available that it's real rather than overfit, within the
limits of development/validation-only evidence.

**Best candidate found:** `stop_risk_fraction=1.6, target_return=0.50,
trailing_stop=0.20` — 23.5%/24.4% win rate (dev/val, the tightest dev-val
gap of any candidate tested, i.e. most internally consistent), positive net
P&L on both splits, best profit factor among the trustworthy candidates
(1.17 val), drawdown cut nearly a third versus Baseline. Labeled **Open**
— not Exploratory, not Confirmed — no test attempt was made.

**Explicitly did not reach 50% win rate.** The highest win rate found
anywhere in this sweep was 29.0% (val), from "Morning + 15% target" (see
prior session's exploration) — but that candidate's total net P&L was
reduced to near-zero. Every combination tried shows the same shape: win
rate and total return trade off against each other, because this strategy's
current edge is "wins less often, but wins bigger" — a trend-following
payoff shape, not a coin-flip-with-edge shape. Reaching 50% win rate through
`BacktestParameters` tuning alone, without changing the signal logic in
`strategy.py`, was not achievable without collapsing net profitability in
every combination tried (see the stop-width table — win rate and validation
P&L visibly diverge past 1.6-3.0). Getting materially closer to 50% while
staying profitable would require a different kind of change — e.g. a signal
designed for hit rate (mean-reversion/range logic) rather than trend
capture — which is a strategy-design question, not a parameter-tuning one,
and is out of scope for this pass.

**Follow-up — layering signal-quality filters on the payoff-shape fix
(same day, same dev/validation ranges, still no test attempt spent).**
Two further things were checked:

1. **Degenerate-win-rate proof.** Shrinking the profit target on the 1.6
   stop to 5% pushes win rate to 47.1%/48.8% (dev/val) — close to 50% — but
   validation net P&L goes negative (-1,863.75); shrinking it further to 2%
   makes both splits clearly unprofitable (profit factor 0.42-0.44). This
   is the concrete demonstration that win rate alone is the wrong target:
   it is trivially reachable by taking tiny, noise-level profits, at which
   point fees and slippage dominate and the edge disappears entirely.
2. **Layering existing filters on the best 1.6-stop/50%-target/20%-trailing
   candidate.** Adding Strict RSI (55/45) on top is a large, consistent
   improvement over that candidate alone — not one lucky split:

   | Candidate (stop 1.6 + 50% target + 20% trailing, plus:) | Dev trades | Dev win% | Dev net P&L | Dev PF | Val trades | Val win% | Val net P&L | Val PF |
   |---|---|---|---|---|---|---|---|---|
   | (none — prior best) | 68 | 23.5% | +20,801.55 | 1.17 | 86 | 24.4% | +6,183.90 | 1.17 |
   | + Strict RSI 55/45 | 49 | 30.6% | +27,363.45 | 2.58 | 66 | 28.8% | +18,787.25 | 1.74 |
   | **+ Strict RSI 55/45 + ATR floor 20** | **47** | **31.9%** | **+28,622.55** | **2.78** | **58** | **29.3%** | **+19,133.25** | **1.87** |
   | + Strict RSI 55/45 + ATR floor 25 | 40 | 30.0% | +24,476.05 | 2.76 | 44 | 27.3% | +3,090.95 | 1.18 |
   | + RSI 58/42 (tighter) | 29 | 24.1% | +5,693.60 | 1.56 | 40 | 27.5% | +14,871.65 | 1.92 |
   | + RSI 60/40 (tighter still) | 21 | 28.6% | +8,945.75 | 2.28 | 25 | 32.0% | +13,296.75 | 2.37 |
   | + ATR floor 15 (alone, no RSI) | 68 | 23.5% | +20,801.55 | 1.75 | 83 | 24.1% | +4,728.95 | 1.13 |
   | + No expiry day (alone) | 53 | 24.5% | +8,069.40 | 1.34 | 70 | 27.1% | +11,309.55 | 1.37 |
   | + Tue-Thu (alone) | 38 | 26.3% | +13,488.50 | 1.87 | 49 | 24.5% | +4,255.95 | 1.20 |
   | + Morning window (alone) | 26 | 19.2% | +7,204.60 | 1.61 | 31 | 29.0% | -1,586.45 | 0.88 |

   ATR floor 20 stacked on top of Strict RSI is a further, genuine
   improvement over RSI alone (higher win rate, higher net P&L, better
   drawdown, better profit factor, on *both* splits). ATR floor 25
   overshoots — validation regresses sharply (net P&L drops from +19,133 to
   +3,091), the same "too-tight breaks it" shape already seen with RSI
   thresholds elsewhere in this log. Tightening RSI itself further (58/42,
   60/40) keeps win rate climbing (up to 32.0% val) and profit factor high,
   but development net P&L drops substantially (from +28,622 down to
   +5,694-8,946) as the trade count shrinks into thin-sample territory
   (21-40 trades) — a different kind of degradation than the ATR-25 case
   (smaller total return from a smaller, higher-quality sample, not a
   validation reversal), but reason enough not to trust it as much as the
   47/58-trade ATR-20 result. "No expiry day" and "Tue-Thu" alone (without
   RSI) underperform the RSI+ATR combination and are not worth stacking
   further. "Morning window" combined with this base actively hurts
   (validation goes negative) — the third time in this log a Morning-window
   combination has failed to compound with another lever, despite Morning
   entries working well on its own elsewhere in this log.

**Best candidate overall (this session):**
`stop_risk_fraction=1.6, target_return=0.50, trailing_stop=0.20,
bullish_rsi_min=55, bearish_rsi_max=45, minimum_atr=20` — win rate
31.9%/29.3% (dev/val), net P&L +28,622.55/+19,133.25, profit factor
2.78/1.87, drawdown 6,671/4,211 (vs. Baseline's roughly-comparable-period
drawdown of 15,561 on validation) — roughly **2.5x Baseline's win rate**,
**better absolute net P&L than Baseline on validation**, at **under a third
of Baseline's drawdown**, consistent across both splits. Still **Open** —
no test attempt was made or is currently possible.

**Ceiling found, not just a stopping point.** Across ~30 candidates this
session, every path toward materially higher win rate did one of two
things: (a) shrank the profit target toward noise level, which pushed win
rate to ~48% but destroyed the edge (see the degenerate-target result
above), or (b) tightened signal-quality filters further, which kept
lifting win rate (up to 32.0%) but shrank trade count and total P&L into
thin-sample territory. Roughly 30% win rate, at the profitability/drawdown
levels shown above, is the practical ceiling found by parameter tuning
alone in this sweep. Going meaningfully closer to 50% while staying
robustly profitable was not achieved and, based on this sweep's shape,
looks like it would require a different signal design (e.g. explicitly
optimizing for hit rate, such as a mean-reversion/range-bound approach)
rather than further `BacktestParameters` tuning.

**Not yet certifiable — no fresh test data.** The range-usage ledger's
pre-existing seed already marks the full Jan-Jul 2026 Upstox span as
touched (screening role), and this archive has no Upstox candles past
2026-07-31. No candidate here — including the leading combination above —
can be certified Confirmed or even a clean Rejected until either (a) fresh
Upstox data past 2026-07-31 is ingested (requires a currently-unavailable
valid `UPSTOX_ACCESS_TOKEN`), or (b) enough forward-paper Angel One
evidence accumulates to test against instead.

## 2026-08-21 — ML signal-quality filter (entry filter only), development/validation only

**Context:** following the parameter-tuning ceiling found in the 2026-08-20
entries above (~30% win rate, strongly profitable), built a trained
machine-learning entry filter as a generalized, multi-feature version of the
hand-tuned "Strict RSI + ATR floor" filter — a small hand-rolled logistic
regression (no numpy/scikit-learn, so it can eventually run on the Termux
runtime with zero new dependency), scored on 7 features (RSI, normalized ATR,
normalized EMA gap, signal confidence, direction, minutes-since-open,
day-of-week). The existing deterministic `MomentumStrategy` signal generation
is untouched; the model only decides whether to take a signal already
generated, layered on top of the same exit shell used throughout this
session (`stop_risk_fraction=1.6, target_return=0.50, trailing_stop=0.20`).

New modules: `src/options_bot/ml_features.py`, `src/options_bot/ml_model.py`,
`src/options_bot/upstox_ml_backtest.py` (a deliberately separate backtest
engine, mirroring `upstox_backtest.py`'s own precedent relative to
`backtest.py`, so the already-tested engine stays untouched), a new
`options-bot backtest ml-validate-split` CLI subcommand, and
`research/train_signal_quality_model.py` (Windows-dev-machine-only). Trained
on the same Development (2026-01-01 to 2026-03-31) / Validation (2026-04-01
to 2026-05-31) ranges used throughout this session, so results are directly
comparable. No test-range attempt was made or is currently possible (same
constraint as every candidate above — no Upstox data past 2026-07-31).

**A real correctness bug was caught and fixed before trusting any number
here.** The training script's first version selected its decision threshold
by filtering an already-built *unfiltered* trade list after the fact —
exactly the trap `upstox_ml_backtest.py`'s own docstring warns about (a
rejected signal sitting between two kept ones would otherwise still act as a
premature exit trigger for the trade before it, silently giving a kept trade
the wrong exit price). Caught by cross-checking the training script's
self-reported validation number against an independent run of the real
`options-bot backtest ml-validate-split` CLI path — the two disagreed
(+3,185.75 vs +3,003.75), which should never happen if both are computing
the same thing correctly. Fixed by having threshold selection call the real
`run_upstox_ml_backtest` engine once per candidate threshold instead of
post-hoc filtering; after the fix, both paths report the identical number.

**Result — model candidate `stop_risk_fraction=1.6, target_return=0.50,
trailing_stop=0.20` + learned filter, threshold=0.30 (chosen from a
0.30–0.70 sweep, step 0.02, by maximizing validation net P&L subject to a
20-trade floor):**

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development | 15 | 33.3% | +9,146.75 | 2,587.50 | 2.62 |
| Validation | 22 | 27.3% | +3,003.75 | 5,654.15 | 1.41 |

**Compared against the existing best hand-tuned candidate** (same exit
shell + Strict RSI 55/45 + ATR floor 20, from the 2026-08-20 entry above):

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development | 47 | 31.9% | +28,622.55 | 6,671.35 | 2.78 |
| Validation | 58 | 29.3% | +19,133.25 | 4,211.05 | 1.87 |

**The ML filter did not beat the hand-tuned candidate.** Win rate is
comparable (27–33% vs 29–32%), but the learned filter keeps far fewer trades
(15–22 vs 47–58), so total net P&L is much smaller, and validation profit
factor/drawdown are both worse, not better. The most likely explanation is
data volume, not the technique itself: only 68 labeled development trades is
a thin dataset for a 7-feature logistic regression to learn a genuinely
better decision boundary than the two hand-picked thresholds (RSI 55/45, ATR
20) already capture — with this little data, a simple linear model has
little room to find real structure beyond what a couple of well-chosen
manual thresholds already found. Labeled **Open** (not Confirmed, not
Exploratory, and not Rejected either — this is a negative result on a
useful idea, not proof the idea is wrong; more training data would be the
natural next test). The model file is committed at
`research/models/ml-signal-quality-v1.json` (small, diffable, weights and
metadata only, no raw market data) for reproducibility.

**Not yet certifiable — same reason as every candidate above.** No fresh
Upstox data exists past 2026-07-31 to test against.

**Follow-up same day — retrained on a bigger window, still not trustworthy,
for a different reason.** Development/validation ranges are always freely
reusable, so retrained on Jan–May (155 raw signals, vs. 68 before) and
validated on June–July instead of April–May, to test whether data volume
was really the limiting factor (candidate `ml-signal-quality-v2-bigdata`,
same exit shell and feature set as above):

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development | 9 | 33.3% | +1,445.05 | 2,479.55 | 1.46 |
| Validation | 20 | 35.0% | +6,458.55 | 3,641.25 | 2.10 |

More raw signals (155) did feed into training, but the chosen threshold
(0.36) filters development down to only **9 trades** while keeping 20 on
validation — development net P&L is barely positive on a sample too small
to mean anything, while validation looks considerably better. This is the
same "strong validation, thin/weak development" shape this log's own
"Strict RSI + No expiry day" entry already flagged as the clearest
false-positive of the whole project (there, development was negative;
here it's merely too small to trust) — not a confirmed improvement over
the first attempt, just a differently-unreliable result. **Also labeled
Open, not an improvement.** The threshold-selection sweep only enforces a
trade-count floor on validation, not development — a real methodological
gap this result exposes: a future revision should also floor development
trade count at selection time, not just after the fact.

**Where this leaves the ML approach:** two attempts, two ways of not being
trustworthy (first: real but smaller edge than the hand-tuned candidate;
second: a validation-only-looking-good pattern flagged by this project's
own discipline). Neither confirms nor cleanly rejects the idea. The
hand-tuned "Strict RSI 55/45 + ATR floor 20" candidate from 2026-08-20
remains the best evidenced result of this whole research effort.

## 2026-08-21 — First genuine test-range result of the whole project: Rejected

**Context:** a fresh Upstox pull (2026-08-01 to 2026-08-20, 24,766 candles,
66 contracts, zero warnings) extended the archive past the 2026-07-31 wall
this project has been stuck behind all session. The range-usage ledger
confirmed nothing had ever touched anything after 2026-07-31 for NIFTY —
making 2026-08-01 onward the **first genuinely untouched test range this
project has ever had access to**.

Spent that one-time test attempt on the strongest, most-evidenced candidate
from the 2026-08-20 entries above: `stop_risk_fraction=1.6,
target_return=0.50, trailing_stop=0.20, bullish_rsi_min=55,
bearish_rsi_max=45, minimum_atr=20` (candidate `r5-best-rsi55-atr20`), via
`options-bot backtest validate-split --test-start 2026-08-01 --test-end
2026-08-20`. Same development (Jan-Mar) / validation (Apr-May) ranges as
every prior entry, re-run identically:

| | Trades | Win rate | Net P&L | Drawdown | Profit factor |
|---|---|---|---|---|---|
| Development | 47 | 31.9% | +28,622.55 | 6,671.35 | 2.78 |
| Validation | 58 | 29.3% | +19,133.25 | 4,211.05 | 1.87 |
| **Test (2026-08-01 to 2026-08-20)** | **4** | **0.0%** | **-2,640.40** | **2,640.40** | **0.0** |

`classify_confirmation()` returned `"eligible_confirmed"` — the methodology
was clean (no forced override, a genuinely fresh range, this candidate's
only-ever test attempt). Per this log's own rule, `eligible_confirmed` +
a bad result means **Rejected**, not Confirmed: the strategy simply did
not perform when finally checked against data it was never picked from or
peeked at in any form. Labeled **Rejected.**

**Honest interpretation, not an excuse.** The test sample is tiny (4
trades over 14 trading days) — far smaller than the ~1.2-1.5 trades/day
rate seen in development and validation, meaning this specific 3-week
window produced unusually few qualifying signals for this heavily-filtered
candidate (RSI 55/45 + ATR floor 20 already cuts trade frequency hard).
A 4-trade sample is weak evidence on its own. But per this project's
entire discipline, that is exactly the point: the test attempt is spent
once, the result is recorded as-is, and a small or unlucky-looking sample
is not grounds to retry, explain away, or wait for a "fairer" window —
doing so is indistinguishable from picking whichever test result looks
better, which is the exact failure mode `research_ledger.py` exists to
prevent. This candidate's test is now permanently spent; it cannot be
tested again under this name.

**What this most likely means:** the RSI55/ATR20 filter combination was
selected by hand, by direct inspection of Jan-July data, across ~30
candidates tried in the 2026-08-20 session — even with a clean development/
validation split, that much manual searching over one underlying dataset
creates real risk that what was found was a pattern specific to Jan-July's
particular market conditions, not a generalizable edge. This is the
concrete, humbling demonstration of why this project's whole ledger/test-
discipline exists: it just caught exactly the kind of look-good-in-
backtest result it was built to catch, before any real capital was ever at
risk. Nothing here changes the paper-only safety boundary, which was never
contingent on this candidate anyway.

### Ideas proposed but not yet tested

- Open Interest as a new breakdown dimension (already captured on every
  option candle, currently unused anywhere in the strategy or analysis).
- Signal confidence (already computed per signal, 0.5-0.95, currently
  discarded after generating the trade) as its own breakdown dimension.
- EMA separation magnitude (trend strength, not just direction) as a
  filter or breakdown dimension.
- A genuinely fresh, never-yet-analyzed time period (not Jan-Jul in any
  form — that range has now been used multiple times across the
  full-range pass, the first split, and the Round 2 comparisons) to give
  "Morning entries" its first clean, untouched check before it can be
  called Confirmed rather than Exploratory.

### Data used for this log

The 7-month archive analyzed here was pulled to a local Termux device via
the dashboard's Upstox ingestion, then transferred once for this analysis
via a temporary GitHub Release asset (deleted after use) — never
committed to git history. Raw market-data SQLite dumps do not belong in
this repository (binary, grows without bound, would break the
fetch/merge cycle `scripts/termux_web.sh` runs on every launch); this log
is the durable record of what was learned from that data, not the data
itself.

## 2026-08-24 — Candidate B confirmed on fresh 2020-2024 data

> **RETRACTED 2026-08-26 — THE NUMBERS IN THIS ENTRY ARE INVALID. DO NOT CITE THEM.**
> Re-running this exact configuration produces **-149,566.00**, not +608,962.50. Twenty-seven
> exit-parameter configurations were tested; **none** reproduces this table, and the best result any
> configuration can achieve is roughly a third of what is claimed here. The table's win-rate column
> and its P&L/profit-factor columns come from two different runs and are mutually exclusive.
> The trade counts, entries and capital-deployed figures below are correct and do reproduce — only
> the P&L, profit factor and ROI columns are wrong. Full investigation, including the arithmetic
> proof and every hypothesis ruled out:
> `research/CANDIDATE_B_REPRODUCTION_INVESTIGATION.md`. See also the 2026-08-26 entry at the end of
> this log.

**What changed.** Candidate B (`TrendConfirmedMomentumStrategy`, see `research/INDEX.md`'s
"Current best candidate") had been stuck at **Open, not Confirmed** since 2026-08-22 for a
concrete, structural reason: every date range from 2024-10-03 through 2026-08-20 had already
been touched by its own dev/val/screening work, leaving no genuinely fresh test range to check
it against. A DhanHQ historical-data backfill added the same day (see `dhan_ingest.py`'s module
docstring for the reconstruction method and validation) supplied real, never-touched NIFTY
underlying and option data for 2020-08-03 through 2024-10-01 — fetched at 1-minute granularity,
then resampled to 5-minute bars (preserving open interest per bucket) to match the exact
granularity Candidate B was built and validated on.

**Configuration used — unchanged from the original validation:**

```python
strategy = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
params = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30, minimum_option_premium=20,
    minimum_open_interest=100_000,
)
```

Run via `run_upstox_backtest(..., include_dhan=True, include_derived=True, timeframe="FIVE_MINUTE")`
across all 17 calendar quarters the new range covers, one call per quarter — the same
quarter-by-quarter discipline used throughout this project, not a single full-range pass.

| Quarter | Trades | Win rate | Net P&L | Profit factor | ROI on capital deployed |
|---|---|---|---|---|---|
| 2020-Q3 (partial, from Aug 3) | 112 | 30.4% | +24,335.00 | 1.68 | +5.83% |
| 2020-Q4 | 135 | 37.0% | +53,757.50 | 2.29 | +9.98% |
| 2021-Q1 | 123 | 35.8% | +48,820.00 | 2.01 | +7.72% |
| 2021-Q2 | 137 | 32.1% | +15,897.50 | 1.32 | +2.66% |
| 2021-Q3 | 137 | 40.1% | +32,887.50 | 1.84 | +6.73% |
| 2021-Q4 | 130 | 42.3% | +60,922.50 | 2.34 | +10.53% |
| 2022-Q1 | 118 | 44.9% | +59,162.50 | 2.05 | +7.15% |
| 2022-Q2 | 147 | 38.1% | +77,685.00 | 2.19 | +9.66% |
| 2022-Q3 | 151 | 39.1% | +44,375.00 | 1.75 | +5.94% |
| 2022-Q4 | 142 | 35.2% | +21,525.00 | 1.42 | +3.85% |
| 2023-Q1 | 155 | 36.8% | +34,397.50 | 1.55 | +5.38% |
| 2023-Q2 | 127 | 37.8% | -140.00 | 1.00 | -0.04% |
| 2023-Q3 | 152 | 30.9% | +15,387.50 | 1.32 | +2.95% |
| 2023-Q4 | 113 | 40.7% | +22,077.50 | 1.58 | +5.17% |
| 2024-Q1 | 154 | 37.0% | +66,390.00 | 1.79 | +7.97% |
| 2024-Q2 | 150 | 36.0% | +18,790.00 | 1.40 | +3.71% |
| 2024-Q3 (partial, to Oct 1) | 173 | 30.1% | -2,726.25 | 0.94 | -0.63% |
| **Total** | **2,356** | — | **+593,543.75** | — | **+5.98%** (blended) |

**15 of 17 quarters profitable (88%)**, spanning the 2020 COVID-recovery volatility, the entire
2022 bear market, and the 2023-24 recovery — market regimes this candidate had never been tested
against before. Both losing quarters (2023-Q2, 2024-Q3-partial) were small and near-breakeven
(profit factor ~1.0), not blowups; there is no sign of the strategy falling apart in any single
regime. This is a stronger, longer, and more regime-diverse result than the original 7-quarter
check (Oct 2024 - May 2026) it was validated on.

**Verdict: CONFIRMED**, per this log's own definition — a genuinely fresh, never-before-touched
range, checked exactly once, unchanged parameters, no comparison shopping across variants.

**Sequencing note.** Candidate B was wired into live paper trading the same day, *before* this
confirmation run was performed — an informed, explicit user decision (see the `CANDIDATE_B_*`
constants and their provenance comment near the top of `connections.py`), not a process
violation discovered after the fact. This entry is the confirmation that decision anticipated.

**Recorded to the durable ROI ledger** under the group name
`"Candidate B fresh-data confirmation (2020-2024 Dhan)"` (`research/roi_all_runs.json`, one row
per quarter) — see `research/roi_ledger.html` for the regenerated table.

### Caveats

- The DhanHQ option data is *reconstructed*, not fetched per-contract the way Upstox data is —
  built from a wide ATM-relative band (`ATM-10`..`ATM+10`) re-grouped by each point's real
  absolute strike into a continuous per-contract series. Validated against real overlapping
  Upstox data before trusting it (see `dhan_ingest.py`'s docstring and the 2026-08-23/24 DhanHQ
  investigation), but it is a different data-collection method than every other confirmed result
  in this log, and that difference has not been separately stress-tested.
- The weekly-cycle boundary used while fetching Dhan data is plain Thursday arithmetic, not
  adjusted for exchange holidays that occasionally shift a real expiry to Wednesday — a rare
  boundary trading day could be attributed to the wrong cycle. Not expected to move results at
  this trade count, but noted for completeness.
- This is still one confirmation pass, not a claim of a permanent edge — the same honesty
  standard this log applies to every other "Confirmed" entry applies here too.

## 2026-08-25 — Fine-tuning pass: parameter re-sweep (no improvement) and an IV filter that looked good, then failed fresh confirmation

**Two follow-up questions after Candidate B's confirmation the day before:** (1) does more data reveal
better indicator periods than the ones already live, and (2) does DhanHQ's historical implied
volatility (never previously fetched) make a useful entry filter. Both were tested; both are now
resolved.

### Parameter re-sweep: current live config is already a local optimum

A quick single-year look (2023 only, 24 combinations of `fast_period`/`slow_period`/`macro_period`/
`rsi_period`) found the current live configuration (`fast=5 slow=10 macro=40 rsi=14`... — this run
used `macro_period=60`, the live value; the 1-year quick-look grid separately confirmed `macro=40`
edges out `macro=60`/`80` on this one year, a difference small enough not to act on without a
proper multi-year re-check) wins outright: +133,455 net P&L, 4.79% ROI, 39.4% win rate — better
than all 23 other combinations tested, several by a wide margin. A consistent pattern held across
the whole grid: `rsi=14` beat `rsi=21` in every single matchup, `slow=10` beat `slow=21` almost
everywhere. **Not adopted as a change** — there's nothing to adopt, the current live parameters
already sit at what looks like a genuine local optimum on this dimension. Exploratory only (single
year, no dev/val split); a proper multi-year re-sweep was not run to completion after this quick
look answered the practical question (is there obvious profit being left on the table here) with a
clear no.

### IV filter: promising on screening data, rejected on genuinely fresh confirmation

DhanHQ's historical rolling-option feed supports an `iv` field that was never requested during the
original 2020-2024 backfill. Added to `dhan_data.py`'s `requiredData`, verified real (not a
doc-only field) against a live sample, then backfilled onto all already-archived rows via a new
UPDATE-based path (`MarketArchive.backfill_implied_volatility`, `dhan_ingest.py`'s
`backfill_iv_for_weekly_cycle`) rather than re-inserting — **16,249,304 values backfilled across the
full 2020-2024 archive, zero warnings**, plus propagated onto the resampled 5-minute bars
(`resample_dhan_iv_to_five_minute`). Data-quality check first: 6.4% of values are exactly `0`
(Dhan's own placeholder for illiquid/edge-case moments it apparently couldn't price) and at least
one extreme outlier (1055% IV on a near-worthless, near-expiry contract) — both treated as "unknown"
(fails closed) rather than trusted, via `BacktestParameters.minimum_implied_volatility`/
`maximum_implied_volatility` added to `upstox_backtest.py`.

**Screening pass** (same dev/val split as the confirmation's own internal re-use, 2020-08..2022-12 /
2023-01..2024-10, Exploratory): `band 10-20` (only enter when IV is 10-20%) beat the no-filter
baseline on ROI in *both* splits — 9.53% vs 7.25% dev, 4.54% vs 4.08% val. `max_iv=20` showed a
similar, slightly smaller edge. Genuinely fewer trades (capital efficiency, not raw P&L) but a
consistent cross-split signal — looked like a real finding.

**Fresh confirmation, done properly before trusting it:** rather than accept the screening result,
a *new*, never-touched 18-month period (2025-03..2026-08, Dhan-reconstructed options with real IV,
backfilled the same way -- 5,711,537 one-minute candles, zero fetch failures across all 77 weekly
cycles) was fetched specifically to test this filter on data it had never seen. Required adding
`dhan_only=True` to `run_upstox_backtest` (a real bug caught and fixed along the way: without it, a
real Upstox contract and a Dhan-reconstructed one for the same strike/expiry are ambiguous
candidates with an identical `ABS(strike-spot)` distance — the query picks one arbitrarily; the fix
scopes `dhan_only` to option-leg queries only, not the shared-token underlying series).

| Variant | Trades | Win rate | Net P&L | ROI |
|---|---|---|---|---|
| baseline (no IV filter) | 852 | 37.1% | +266,773.75 | **4.02%** |
| band 10-20 | 530 | 36.4% | +137,040.00 | 3.27% |
| max_iv=20 | 743 | 37.3% | +218,027.50 | 3.95% |

**Both IV filter variants underperform the baseline on genuinely fresh data — the opposite of what
the screening pass showed.** This is exactly the failure mode this project's dev/val/fresh-test
discipline exists to catch, and it caught it. **Rejected.** The IV data itself remains archived and
usable (real, verified, no reason to distrust the underlying numbers) — it's this specific filtering
hypothesis that didn't generalize, not the data source.

### Infrastructure added, independent of the filter's rejection

- `MarketArchive.backfill_implied_volatility` (UPDATE-based, additive-only backfill onto existing rows)
- `dhan_ingest.py`: `backfill_iv_for_weekly_cycle`, `resample_dhan_iv_to_five_minute`
- `dhan_data.py`: `DhanRollingPoint.implied_volatility`, `iv` added to every `fetch_rolling_option` call permanently (harmless to always request)
- `upstox_backtest.py`: `dhan_only` parameter, `minimum_implied_volatility`/`maximum_implied_volatility` on `BacktestParameters`
- The archive now has real historical IV across both the original 2020-2024 range and this new 2025-2026 range -- available for a *different* IV-based hypothesis if one comes up later, without re-fetching anything.

## 2026-08-25 — ORB downgraded: does not hold up on fresh 2020-2024 data

**Direct follow-up to Candidate B's fresh-data confirmation, using the identical methodology.**
ORB (`OpeningRangeBreakoutStrategy(opening_range_bars=6)` + `stop_risk_fraction=1.6,
target_return=0.30`, unchanged from its original screening) was run across the same 17 quarters
of the 2020-2024 DhanHQ backfill Candidate B was confirmed on.

| Quarter | Trades | Win rate | Net P&L | PF | ROI |
|---|---|---|---|---|---|
| 2020-Q3 (partial) | 31 | 41.9% | +8,962.50 | 1.53 | +8.81% |
| 2020-Q4 | 53 | 39.6% | +11,290.00 | 1.28 | +5.51% |
| 2021-Q1 | 48 | 31.2% | +1,872.50 | 1.04 | +0.71% |
| 2021-Q2 | 57 | 35.1% | -4,660.00 | 0.89 | -2.12% |
| 2021-Q3 | 48 | 39.6% | -1,170.00 | 0.96 | -0.72% |
| 2021-Q4 | 41 | 41.5% | +680.00 | 1.02 | +0.34% |
| 2022-Q1 | 54 | 40.7% | +1,427.50 | 1.02 | +0.38% |
| 2022-Q2 | 49 | 40.8% | +1,142.50 | 1.02 | +0.42% |
| 2022-Q3 | 59 | 44.1% | +1,807.50 | 1.04 | +0.66% |
| 2022-Q4 | 49 | 53.1% | +20,477.50 | 1.79 | +9.47% |
| 2023-Q1 | 47 | 44.7% | -8,012.50 | 0.80 | -4.21% |
| 2023-Q2 | 41 | 46.3% | -2,792.50 | 0.87 | -2.27% |
| 2023-Q3 | 65 | 38.5% | -3,355.00 | 0.91 | -1.53% |
| 2023-Q4 | 46 | 32.6% | +9,270.00 | 1.26 | +5.66% |
| 2024-Q1 | 59 | 32.2% | -4,147.50 | 0.93 | -1.42% |
| 2024-Q2 | 48 | 37.5% | +3,231.25 | 1.09 | +1.93% |
| 2024-Q3 (partial) | 55 | 34.5% | -6,608.75 | 0.77 | -4.60% |
| **Total** | **850** | — | **+29,415.00** | — | **+0.82%** (blended) |

**Only 10 of 17 quarters profitable (59%)**, a real four-quarter losing streak through 2023
(Q1-Q3 plus 2024-Q1 all negative), and most "profitable" quarters are only marginally so (profit
factor barely above 1.0). Blended ROI of 0.82% across the whole span is a small fraction of
Candidate B's 5.98% on the identical range. This does not reproduce the "profitable in all 7
quarters" result the original Oct 2024 - May 2026 screening found.

**Downgraded from Open to Rejected as a standalone strategy on this evidence.** The original
screening range was real but short (under 2 years) and evidently not representative of how this
signal behaves across a full market cycle -- exactly the risk fresh-data confirmation exists to
catch. **Not added to any live/combined portfolio.** The 2020-2024 archive itself (already backfilled
for Candidate B) required no additional data work for this check.

## 2026-08-25 — Short strangle re-confirmed on the 2020-2024 fresh range; combined-portfolio check with Candidate B run for real

> **RETRACTED 2026-08-26 — the +67,980 figure in this entry was produced with fees and slippage
> DISABLED.** With real configured costs the same 526 trades give **+9,593.50** and 9/17 profitable
> quarters, not 12/17. Root cause confirmed by exact reproduction: passing `settings=None` (which
> zeroes fees and slippage) regenerates +67,980.00 to the rupee. Real costs consume 86% of the
> claimed profit. The strategy is downgraded from Confirmed to **Open** — still net positive after
> costs, but with roughly one seventh of the claimed edge. The combined-portfolio correlation numbers
> below inherit the same inflated P&L series and must be re-derived before use. See the 2026-08-26
> entries at the end of this log and
> `research/CANDIDATE_B_REPRODUCTION_INVESTIGATION.md`.

**Direct follow-up, same methodology as ORB above.** The selective short strangle (`strike_distance_pct=0.002,
stop_multiple=2.0, target_fraction=0.5`, opening-range filtered, `exclude_expiry_day=True`) was run across the
same 17 quarters of the 2020-2024 DhanHQ backfill. Unlike ORB, it held up: **12 of 17 quarters profitable, net
P&L +67,980** across the full span. (Aggregate result only -- the quarter-by-quarter table from this run was
reported in chat but not preserved as a file; it should be regenerated and pasted in here before this entry is
cited as the sole record. Do not treat the two summary numbers above as a substitute for the full table other
strategies in this log carry.)

**Combined-portfolio check, done for real rather than assumed:** the user asked explicitly whether the short
strangle had been tested together with Candidate B or only alone, and whether ORB's near-zero correlation
findings (see the "Extended to three" note in `research/INDEX.md`) still applied now that ORB itself is
rejected. Rather than reuse the old three-way numbers, a fresh two-way check was run directly from both
engines' `trade_details` over the full 2020-08-03..2024-10-01 range: per-day P&L was aggregated for each
strategy independently, a true combined daily P&L series was built by summing the two, and max drawdown was
computed on that combined series (not a naive sum of each strategy's own drawdown).

| | Candidate B alone | Short strangle alone | Combined |
|---|---|---|---|
| Trades | 2,388 | 526 | 2,914 |
| Net P&L | +608,962.50 | +67,980.00 | +676,942.50 |
| Max drawdown (daily-aggregated) | 13,455 | 26,395 | **14,540** (vs 39,850 naive sum) |
| P&L / drawdown ratio | 45.26 | 2.58 | **46.56** |

**Daily P&L correlation between the two strategies: -0.36** (negative -- they tend to do well on different
days, not the same days). 433 of the ~850+ trading days in the range had both strategies trading
simultaneously. Combining barely moves drawdown above Candidate B's alone (14,540 vs 13,455) despite adding
the strangle's full profit and its own much larger standalone drawdown (26,395) -- the negative correlation is
doing real diversification work here, not just failing to hurt.

**This is the first genuinely tested two-way combination in this project** (as opposed to assumed from each
strategy's standalone numbers) and is the basis for building live short-strangle execution. `MAX_OPEN_POSITIONS`
was raised from 2 to 5 in `local-bot.env` to make room for it (Candidate B up to ~2 positions, a strangle
needing 2 slots per trade, plus headroom).

**Live (paper-only) execution built and tested same day** -- `connections.py` gained
`create_short_strangle_proposal` (entry gate: 9:45 earliest, opening-range filter, nearest-expiry OTM
call/put selection mirroring `short_premium_backtest.py`'s exact query, expiry-day exclusion) and
`market_archive.py` gained `select_strangle_legs`. `paper_monitor.py` gained a daily, once-per-day auto-entry
(`_maybe_auto_short_strangle_entry`, with automatic rollback of the call leg if the put leg's open fails, so a
naked single leg can never be left open) and paired exit monitoring (`_check_strangle_exits`, which evaluates
both legs' combined buy-back cost together -- never either leg alone -- against `stop_multiple`/
`target_fraction`, matching how the backtest actually defines the exit). A separate "ENABLE/DISABLE AUTO
STRANGLE" toggle was added (dashboard + `/actions/auto-strangle`), independent of Candidate B's own toggle, so
the newer strategy can be switched on deliberately rather than inheriting Candidate B's already-running state.
Foundation layer (schema migration for SELL-side orders, direction-aware fill/P&L/risk math) was tested against
a copy of the real production database before any of this was built on top of it. 7 new tests in
`tests/test_short_strangle_live.py` plus 1 in `tests/test_web.py`; full suite 284 passed (same 4 pre-existing,
unrelated failures as before -- 2 `fcntl`-only-on-Linux in `test_service.py`, one in `test_market_archive.py`,
one in `test_readiness.py`). `LIVE_TRADING_ENABLED` remains `false`; nothing above changed that boundary --
this is all still paper-only.

## 2026-08-25 — Short strangle ML entry filter: rejected on fresh data, and the fresh range exposes a more urgent problem

**Direct response to a user request for an ML-driven entry decision for the short strangle**, going beyond the
existing hand-tuned `maximum_opening_range_pct` cutoff. Built `short_strangle_ml_features.py` (day-level features:
`opening_range_pct`, `day_of_week`, `days_to_expiry`, `is_macro_event_window`, `gap_from_prev_close_pct`,
`realized_vol_5d`, `realized_vol_20d` — `opening_range_pct` is itself one of the features, so the model is a
generalization of the existing cutoff, not an unrelated second filter), wired an optional `ml_model` parameter
into `run_short_strangle_backtest` that replaces `maximum_opening_range_pct` entirely when supplied, and reused
this project's existing hand-rolled logistic-regression infrastructure (`ml_model.py`, the same dependency-free
scorer Candidate B's own ML filter uses) via a new `research/train_short_strangle_ml_model.py`.

**A real, previously-undiscovered performance bug was found and fixed along the way, independent of the ML
result:** `market_candles` had no index on `source`, so every `source='dhan'`/`source='upstox'` filtered query in
this ~6GB archive (which is nearly all of them) required a full table scan — simple counts were taking 300-400+
seconds. Added `CREATE INDEX market_candles_source_idx ON market_candles(source, instrument_token)` to
`market_archive.py`'s schema (idempotent, applied automatically to every archive going forward); the same query
that returned an unindexed dev backtest in 12+ minutes without finishing completed in well under a minute after
the one-time ~5-minute index build. This benefits every backtest engine in the project, not just this one.

**Training used 542 labeled development days** (2020-08-03 to 2023-04-30, unconditional baseline for labels,
positive_rate 65.5%) — a genuinely large sample, unlike Candidate B's earlier ML attempt which was rejected as
"Open" specifically because only 68 development trades were available (2026-08-21 entry) before the DhanHQ
backfill existed. Threshold swept [0.05, 0.80] step 0.05 against validation (2023-05-01 to 2024-10-01):

| | Trades | Win rate | Net P&L | Profit factor |
|---|---|---|---|---|
| Development, unfiltered baseline | 542 | 65.5% | +24,103.00 | 1.08 |
| Development, ML at threshold=0.60 | 491 | 66.8% | +44,654.00 | 1.17 |
| Validation, ML at threshold=0.60 | 249 | 69.9% | +24,010.00 | 1.21 |

**Looked like a genuine, consistent win on both development and validation** — nearly double the baseline's net
P&L on development, held up on validation too. Exactly the shape that has fooled this project before (the IV
filter, 2026-08-25 earlier entry). Ran the real, disjoint, never-before-touched-by-this-strategy fresh range
(2025-03-01 to 2026-08-18, the same range already spent on Candidate B's IV filter hypothesis but never on the
short strangle) as the actual test:

| Fresh 2025-03..2026-08 | Trades | Win rate | Net P&L | Profit factor |
|---|---|---|---|---|
| Unconditional baseline (no filter at all) | 89 | 58.4% | -13,954.05 | 0.82 |
| **Existing manual `maximum_opening_range_pct=0.005` filter** | 65 | 60.0% | **-4,817.45** | 0.90 |
| ML filter (threshold=0.60) | 85 | 57.7% | -16,561.05 | 0.79 |

**The ML filter is Rejected — not just "didn't help," it did worse than doing nothing at all** on the one number
that counts, the opposite of what development/validation predicted. Textbook overfitting to that period's
specific regime, the same failure mode the IV filter hit hours earlier. Not adopted; not wired into
`create_short_strangle_proposal`, which still uses the plain hand-tuned filter exactly as before this entry.

**A more urgent finding buried in the same table: the already-confirmed manual filter itself lost money on this
fresh range** (-4,817.45 across 65 trades, profit factor 0.90 — below 1.0). This directly concerns the
short-strangle live-execution path built earlier today, since its "ENABLE AUTO STRANGLE" toggle governs exactly
this configuration. Two things temper how much weight to put on this before treating it as a reversal of the
strategy's 2020-2024 confirmation (12/17 quarters, +67,980, unaffected by anything in this entry):
- Only 65 of the ~380 trading days in this 17.5-month window produced an archived, tradeable pair of legs at the
  configured 0.2% OTM strike distance — the same narrow/asymmetric OTM strike-coverage gap already documented in
  the 2026-08-23 entry, not a new problem, but it makes this specific sample thinner than the 2020-2024
  confirmation's 526 trades.
- This one 17.5-month window has not itself been split and re-checked (e.g. quarter by quarter) the way every
  *confirmed* result in this log has been -- it is a single aggregate number, not yet given the same scrutiny.
- The IV filter's fresh test on this identical calendar range found Candidate B's *unrelated* directional
  strategy also underperformed its own baseline there (3.27-3.95% vs 4.02% ROI, still profitable, just less so)
  -- some evidence this window was simply a harder one across strategies, not strangle-specific.

**Immediate follow-up, same day -- the quarter-by-quarter breakdown, done properly rather than left as an open
question:**

| Period | Trades | Win rate | Net P&L | Profit factor |
|---|---|---|---|---|
| 2025-03 (partial) | 1 | 0.0% | -3,000.50 | 0.00 |
| 2025-Q2 | 4 | 50.0% | -9,126.50 | 0.14 |
| 2025-Q3 | 22 | 54.5% | -2,504.00 | 0.81 |
| 2025-Q4 | 13 | 61.5% | +4,282.00 | 1.64 |
| 2026-Q1 | 14 | 50.0% | -6,104.85 | 0.50 |
| 2026-Q2 | 3 | 100.0% | +5,893.40 | — |
| 2026-Q3 (partial) | 8 | 87.5% | +5,743.00 | 7.08 |
| **Total** | **65** | — | **-4,817.45** | — |

**Not a uniform decay -- a rough patch (2025-Q2 through 2026-Q1, all four negative) followed by a recovery in
the two most recent quarters (2026-Q2 and 2026-Q3, both strongly positive).** The worst single quarter
(2025-Q2, -9,126.50) is also the thinnest sample (4 trades) -- a couple of bad losses on a handful of trades,
not a broad pattern. Every quarter here has a small enough sample (3-22 trades vs. the 2020-2024 confirmation's
30-65 per quarter) that no single number should be trusted in isolation. Taken together: real evidence of a
weaker stretch through early 2026, not evidence the strategy has stopped working -- the two most recent
quarters look as strong as anything in the 2020-2024 confirmation. **Recommendation unchanged: do not enable
"AUTO STRANGLE" live from a standing start on this evidence, but this is a case for watching a few more weeks
of live paper data before deciding, not for abandoning the strategy.** The live paper-execution code built
earlier today is unaffected either way -- it is inert until a human explicitly flips that toggle, and this
finding is exactly why that toggle defaults off.

Infrastructure added, independent of the ML rejection: `short_strangle_ml_features.py` (7 tests),
`run_short_strangle_backtest`'s `ml_model` parameter (1 test), `market_candles_source_idx` (the whole test suite
re-ran clean against it, 288 passed). `research/models/short-strangle-ml-v1.json` committed for
reproducibility (small, diffable, weights and metadata only, no raw market data), matching Candidate B's
`ml-signal-quality-v1.json` precedent even though this one is a negative result.

## 2026-08-26 — Candidate B's confirmation does not reproduce; it is retracted, and the strategy as configured loses money

**This entry retracts the 2026-08-24 "Candidate B confirmed on fresh 2020-2024 data" result.** It was
found while building a capital-scaling simulation: replaying the confirmed trade sequence produced a
net loss where the log claimed a large profit. Rather than paper over it, the discrepancy was
investigated to root cause. Full working:
`research/CANDIDATE_B_REPRODUCTION_INVESTIGATION.md`.

**What reproduces exactly:** trade counts per quarter (112, 135, 123, 137, 137, 130, 118, 147, 151,
142, 155, 127, 152, 113, 154 — every quarter checked matches the documented table), entry prices,
and capital deployed (418,644 vs the 417,410 implied by the documented ROI, a 0.3% gap consistent
with a slippage setting). **The same signals, the same contracts, the same entries.** The divergence
is entirely in exit P&L.

**Hypotheses ruled out by direct test, not by reasoning:** settings drift (forced
`max_loss_per_trade=400`, no change); archive data changing (re-ran against the same-day
`backups/market-data-20260824.sqlite3` — identical result); quarter-chunked vs continuous ranges
(real but worth only a few hundred rupees per quarter); engine code changes (`git show faa3629`
touches only an additive, default-off `dhan_only` scope — with `dhan_only=False` the executed SQL is
byte-identical, and the exit path is untouched).

**The decisive evidence.** The documented profit factor pins gross win/loss exactly. For
2020-Q3-partial (112 trades, 30.4% = 34 wins, PF 1.68, +24,335): gross win 60,122, gross loss 35,787
-> average win 1,768, average loss 459, reward:risk **3.85**. A 12-cell trailing-stop grid found a
configuration matching those per-trade economics to within **one rupee** —
`stop_risk_fraction=1.6, target_return=None, trailing_stop=0.30, trailing_activation_return=0.20`
gives average win **1,769** and average loss **458** — but it produces **25 winners, not 34**.
Applying the documented row's own 34/78 split to those matched averages: 34 x 1,769 - 78 x 458 =
**+24,422**, i.e. the documented +24,335. **The documented P&L is one run's per-trade economics
carried by a different run's win count.** Notably, trailing stops are recorded as tested and *not
adopted* in this same log (2026-08-23), so the parameter line printed beside the table does not
describe whatever produced it.

**Confirmed unreachable.** A third grid swept `stop_risk_fraction` in {1.6, 2.0, 2.8, 4.0, 6.0} x
`target_return` in {None, 0.30, 0.60} — 27 configurations in total across the three grids. Every one
obeys the same trade-off with no exception:

| Regime | Win rate | Average win | Net P&L |
|---|---|---|---|
| Capped winners (`target_return` set) | 25.0% - 35.7% | 742 - 1,577 | **negative in all 10 cells** |
| Uncapped winners (`target_return=None`) | 21.4% - 25.0% | 2,044 - 2,193 | +4,996 to **+7,774 (best of 27)** |

Raising the win rate requires capping winners, which mechanically shrinks the average win. **No
configuration produces 34 wins at ~1,768 average — the documented columns are mutually exclusive**,
and the best achievable net is roughly a third of what was claimed.

**What the strategy actually does, and why it is structural.** The documented parameter line
(`stop_risk_fraction=1.6, target_return=0.30`) — which is also exactly what is wired into live paper
trading via the `CANDIDATE_B_*` constants in `connections.py` — gives **-13,551.00** on 2020-Q3
partial and **-149,566.00** across 2020-08-03..2024-10-01. Independent arithmetic confirms this is
forced, not bad luck: a 30% profit cap against a fixed ~Rs 640 stop is a reward:risk of ~1.49, which
needs a **40.2%** win rate to break even (`1/(1+R)`); the strategy delivers **30.4%**. Per-trade
expectancy = 0.304 x 22.5pts - 0.696 x 12pts = **-1.51 points per trade**.

**Immediate consequence: Candidate B is enabled for automatic paper entries in a configuration that
loses money on every historical period tested.** The live-trading boundary is untouched
(`LIVE_TRADING_ENABLED=false` — no real money was ever at risk), but the auto-entry toggle should be
reconsidered on this evidence. The best configuration found (+7,774) is **not** a recommendation: it
is the winner of a 27-cell search on one window, exactly the overfitting this project's dev/val/fresh
discipline exists to reject.

**Process failure this exposes.** Neither this confirmation nor the short strangle's was produced by
a committed, re-runnable script — both were run ad hoc with only their output pasted into this log.
That is the direct reason a wrong number survived two days and took a multi-grid investigation to
diagnose instead of a one-line diff. **Every future confirmation must be produced by a committed
script** (as `research/train_short_strangle_ml_model.py`,
`research/capital_compounding_simulation.py` and `research/one_month_sizing_analysis.py` now are), so
any number in this log can be regenerated on demand. The short strangle's own confirmation
(+67,980) was produced the same ad-hoc way and **has not been re-verified** — it should be treated as
suspect until it is.

### Position sizing: a separate real defect found the same day

`research/one_month_sizing_analysis.py` (2020-08-03..2020-08-31, 52 trades, Rs 1,00,000 start):

| Regime | Final | Return | Lots min/avg/max | Max drawdown |
|---|---|---|---|---|
| FIXED 1 lot (the live setting) | 92,273 | -7.73% | 1 / 1.0 / 1 | 10,062 |
| ROLLING (sized by affordable premium) | 40,804 | **-59.20%** | **3 / 10.4 / 28** | **91,372** |

Both took all 52 trades — Rs 1,00,000 is ample, nothing was skipped for capital. But sizing by
affordability (`lots = balance x cap% / premium`) is backwards: lot count scales *inversely* with
premium while the stop is a **fixed rupee distance per lot**, so cheap options received the largest
risk allocation. A premium-29 option took 28 lots and lost Rs 16,742 in one trade; a premium-100
option took 4 lots and lost Rs 2,390 — a 7x swing in rupees actually risked, driven purely by
option price rather than by any risk decision. **Correct rule: size by risk, not affordability** —
`lots = per_trade_risk_budget / (stop_distance x lot_size)`, giving a steady ~3 lots at Rs 1,00,000
risking 2%, instead of swinging 3-28. This must be fixed before further capital-scaling work, though
it does not rescue a negative-expectancy strategy: sizing governs how fast such a system loses, not
whether it does.

## 2026-08-26 (second entry) — Short strangle's confirmation also retracted: it was run with fees and slippage disabled

**Follow-up to the retraction above, done immediately because the same ad-hoc process produced both
numbers.** Re-verified with a committed script this time
(`research/verify_short_strangle_confirmation.py`), so this result can be regenerated on demand.

**Root cause found, and it reproduces exactly.** The engine treats `settings=None` as "no costs"
(`slippage = settings.paper_slippage_bps/10_000 if settings else 0.0`, likewise `fee`). Running the
documented configuration that way reproduces the 2026-08-25 claim **to the rupee**:

| Run | Trades | Quarters profitable | Net P&L |
|---|---|---|---|
| **Documented claim** | 526 | 12/17 | **+67,980.00** |
| Re-run, `settings=None` (no fees, no slippage) | 526 | **12/17** | **+67,980.00** — exact |
| Re-run, real configured costs | 526 | **9/17** | **+9,593.50** |

**The short strangle's confirmation was an idealised, cost-free run.** A strangle pays four orders
per trade (sell two legs, buy two back) — Rs 80 in fees at the configured Rs 20/order, before
slippage on four legs. Across 526 trades **real costs consume 86% of the claimed profit.**

| Quarter | Trades | Win rate | Net P&L (real costs) | PF |
|---|---|---|---|---|
| 2020-Q3 (partial) | 18 | 77.8% | -2,857.00 | 0.73 |
| 2020-Q4 | 17 | 70.6% | +4,137.50 | 1.64 |
| 2021-Q1 | 21 | 57.1% | -1,257.50 | 0.91 |
| 2021-Q2 | 26 | 61.5% | +2,024.50 | 1.18 |
| 2021-Q3 | 39 | 64.1% | +1,606.50 | 1.10 |
| 2021-Q4 | 24 | 58.3% | -7,210.50 | 0.68 |
| 2022-Q1 | 17 | 58.8% | +8,527.00 | 2.11 |
| 2022-Q2 | 20 | 60.0% | +1,855.50 | 1.19 |
| 2022-Q3 | 26 | 53.8% | -9,922.50 | 0.47 |
| 2022-Q4 | 35 | 60.0% | -2,434.50 | 0.85 |
| 2023-Q1 | 31 | 54.8% | -19.50 | 1.00 |
| 2023-Q2 | 43 | 65.1% | +373.50 | 1.03 |
| 2023-Q3 | 48 | 75.0% | +12,308.00 | 2.06 |
| 2023-Q4 | 42 | 57.1% | -18,141.50 | 0.50 |
| 2024-Q1 | 34 | 67.7% | -5,871.00 | 0.83 |
| 2024-Q2 | 39 | 79.5% | +15,419.75 | 2.70 |
| 2024-Q3 (partial) | 46 | 78.3% | +11,055.25 | 2.05 |
| **Total** | **526** | — | **+9,593.50** | — |

**Verdict: downgraded from Confirmed to Open, not Rejected.** Unlike Candidate B, this strategy is
still genuinely net positive after real costs (+9,593.50, profitable in 9 of 17 quarters). But its
true edge is roughly one seventh of what was claimed, and Rs 9,594 spread over four years is too
thin to carry a "confirmed, ready to deploy" label. The 2026-08-25 fresh-range result (-4,817 over
2025-03..2026-08) now reads consistently with this: a thin edge that real costs can flip negative.

**Margin remains unmodelled, and it is decisive at small account sizes.** A short strangle posts
SPAN + exposure margin rather than paying premium — flagged as unmodelled in
`short_premium_backtest.py`'s own docstring. Real margin for a short NIFTY strangle runs roughly
Rs 1.5-2 lakh per lot, so **an Rs 1,00,000 account cannot hold even one strangle position.** Any
capital-scaling work here must model margin first.

## 2026-08-26 (third entry) — What Rs 1,00,000 of real rolling capital actually produces

The question that started this whole investigation, now answered on verified numbers with correct
risk-based sizing (`research/capital_compounding_simulation.py --sizing risk --risk-pct 0.02`,
each trade risking 2% of the *current* balance rather than sizing by what the balance can afford —
see the sizing defect recorded in the first 2026-08-26 entry).

| Period | Start | End | Return | Max drawdown | Trades taken |
|---|---|---|---|---|---|
| 2020-08 .. 2024-10 (the retracted "confirmed" range) | 1,00,000 | **31,766** | **-68.2%** | 1,04,691 | 1,897 (450 skipped) |
| 2025-03 .. 2026-08 (most current data) | 1,00,000 | **63,525** | **-36.5%** | 63,930 | 637 (25 skipped) |

Both periods lose money, which is the expected consequence of the negative per-trade expectancy
established above — sizing governs how fast a negative-expectancy system loses, not whether it does.

Two observations worth keeping:

- **Risk-based sizing is materially less destructive than the naive rule** (-68.2% vs -98.9% on the
  same 2020-2024 sequence), confirming the sizing fix is real and worth keeping even though it does
  not rescue the strategy.
- **The recent period has a distinct shape**: a severe first six months (1,00,000 -> 37,925 by
  2025-08, -62%) followed by twelve months that were mildly *positive* overall (37,925 -> 63,525).
  Whether that reflects a genuine regime change or is noise on a thin sample is exactly the kind of
  question that needs a proper fresh-range confirmation — run from a committed script — before
  anyone acts on it.

**Rs 1,00,000 is ample capital for Candidate B** — only 25 of 662 trades were skipped for capital in
the recent period, versus the Rs 20,000 test where the account died entirely. The constraint at
Rs 20,000 was capital adequacy; at Rs 1,00,000 the constraint is simply that the strategy has no
edge.

## 2026-08-26 (fourth entry) — Short strangle RETIRED by user decision

Following the two retractions above, the short strangle is **retired from this project** — not merely
downgraded. The deciding factor is capital, not edge:

**A short strangle posts SPAN + exposure margin, not premium.** For NIFTY that is roughly
Rs 1.5-2 lakh *per lot*. The account this bot is being built for cannot hold even one position.
`short_premium_backtest.py` never modelled margin at all — flagged in its own module docstring —
so every P&L figure this strategy ever produced silently assumed capital that does not exist.
No amount of parameter tuning changes that.

The edge finding compounds it: with real fees and slippage the strategy returns +9,593.50 across
four years and 526 trades, roughly one seventh of the retracted +67,980 claim.

**What was done, and deliberately not done.** `connections.SHORT_STRANGLE_RETIRED = True` now makes
`create_short_strangle_proposal` refuse outright, which covers every entry point at once (the
dashboard's manual proposal and `paper_monitor`'s automatic daily entry both route through it). The
dashboard toggle carries the retirement notice. **The execution path itself was kept, not deleted,
and stays under test** (8 tests in `tests/test_short_strangle_live.py`, including one covering the
guard and three that patch it off to keep exercising the real logic): the implementation is correct
and tested, and the blocker is account size rather than any defect in it. Reviving it is one flag.

Ledger of what remains after this: **Candidate B is the only live-wired strategy, and it is
Rejected** — negative expectancy, structurally (30.4% win rate against a 40.2% break-even). The
project currently has no strategy with a demonstrated, cost-inclusive, reproducible edge.

## 2026-08-26 (fifth entry) — Exit-shell hypothesis REJECTED: the edge was never there to throw away

**Testing the one lead left after Candidate B was rejected.** The retraction investigation found a
consistent pattern across 27 exit configurations on a single window: every variant that *capped*
winners lost money, while every variant that let winners run was net positive (+4,996 to +7,774).
That raised a real question — is the entry signal finding something the 30% profit target throws
away? Tested properly this time via a committed script (`research/exit_shell_study.py`) with the
dev/val/held-out split fixed **before** any result was looked at. Entry signal, contract selection
and all entry filters held pinned throughout; only the exit shell varied.

**Phase 1 — Development (2021-01-01..2022-12-31, 1,096 trades).** The hypothesis looked strong:

| Exit shell | Win rate | Net P&L | PF |
|---|---|---|---|
| **uncapped (no target)** | 23.8% | **+58,970.00** | 1.12 |
| trail 0.40 act 0.30 | 25.5% | +38,149.00 | 1.08 |
| trail 0.40 act 0.20 | 25.2% | +34,686.50 | 1.07 |
| trail 0.40 | 25.0% | +33,755.00 | 1.07 |
| target=0.80 | 26.1% | +6,344.50 | 1.01 |
| trail 0.30 act 0.20 | 25.9% | +6,936.50 | 1.02 |
| trail 0.30 | 25.2% | +5,111.00 | 1.01 |
| target=0.50 | 28.8% | -800.50 | 1.00 |
| **baseline target=0.30** | 34.8% | **-40,352.00** | 0.91 |
| trail 0.20 | 24.9% | -115,519.50 | 0.69 |

Uncapped beat the baseline by nearly Rs 100,000 on this window, exactly as the hypothesis predicted.

**Phase 2 — Validation (2023-01-01..2024-10-01, 1,037 trades). The ranking inverted completely:**

| Exit shell | Win rate | Net P&L | PF | Dev rank -> Val rank |
|---|---|---|---|---|
| **baseline target=0.30** | 34.4% | **-89,972.00** | 0.76 | worst-but-one -> **best** |
| uncapped (no target) | 19.8% | -122,879.50 | 0.72 | **best** -> worst-but-two |
| trail 0.40 act 0.30 | 22.1% | -135,795.75 | 0.67 | 2nd -> 3rd |
| trail 0.40 act 0.20 | 22.0% | -135,911.00 | 0.67 | 3rd -> **worst** |

The development winner became the validation loser and the development loser became the validation
winner. That is the textbook signature of fitting noise, and it is why the shortlist was chosen on
development alone rather than on the whole dataset.

**Phase 3 — Held-out (2026-06-01..2026-08-20, 124 trades), one shot:**

| Exit shell | Win rate | Net P&L | PF |
|---|---|---|---|
| uncapped (the candidate) | 20.2% | **-24,027.75** | 0.60 |
| baseline (reference) | 25.8% | -3,086.70 | 0.95 |

**Verdict: REJECTED.** The candidate is worse than the baseline on validation *and* on held-out, and
negative on both. Not adopted.

**The more important conclusion, which goes beyond this hypothesis.** Every configuration tested —
all ten shells, on both validation and held-out — **loses money**. The baseline is merely the
least-bad. The development window's positive results (up to +58,970) do not survive contact with any
other period. This is not an exit-shell problem that better exits could fix: **Candidate B's entry
signal does not produce a tradeable edge, and there is nothing for a smarter exit to preserve.**
Even the best development result was marginal on its own terms (profit factor 1.12, ROI 1.11%) —
the kind of number that a single favourable window produces by chance.

Combined with the retraction entries above, the position is unambiguous: **Candidate B is Rejected
on entry logic, not on exit tuning, and no further parameter work on it is warranted.** Any future
effort should go to finding a genuinely different signal, tested from the start under the corrected
foundations now in place (committed scripts, real costs, risk-based sizing, honest range accounting).
