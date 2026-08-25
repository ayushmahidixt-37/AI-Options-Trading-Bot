# Research Index — read this first, before grepping the long logs

> A "hub" note, not a detail note: this is the fast-orientation summary.
> Full detail always lives in `BACKTEST_FINDINGS.md` / `PROJECT_STATUS.md`
> — this file points at exact section headings (grep for them) rather than
> line numbers, which drift as those files grow. Keep this file short;
> if it starts accumulating detail instead of pointers, that detail
> belongs in the dated log instead.

## Current best candidate

**"Candidate B"** — `TrendConfirmedMomentumStrategy` (new file:
`src/options_bot/strategy_experimental.py`) with:

```python
strategy = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
params = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30, minimum_option_premium=20,
    minimum_open_interest=100_000,
)
```

- Validation (Apr-May 2026): 76 trades, 46.1% win rate, +58,586.50 net P&L,
  3,370.70 drawdown, profit factor 3.17 (with the OI≥100,000 filter --
  same or better than without it on every metric, for free).
- Signal-confidence filtering was also tested and **should NOT be added**
  — unlike the OI floor, it's a real quality/quantity trade-off with no
  free tier: any threshold that raises win rate also shrinks total profit.
- An OI-aware ML model trained directly on candidate B's own signal (10
  features, precontract+postcontract) was also tried, to see if a learned
  relationship could beat the hand-found `minimum_open_interest=100000`
  cutoff. **It didn't** — best threshold found barely beats doing nothing
  and clearly underperforms the plain hard filter already adopted. Grep
  `## 2026-08-22 — Candidate B + OI-aware ML` for the full analysis.
- **Profitable in all 7 quarters** of the extended history (Oct 2024 – May
  2026), including the one quarter (2025 Q4) where plain Baseline and the
  ML-filtered model both lost. Strongest generalization evidence this
  project has produced.
- `exclude_macro_event_days=True` was tested and **should NOT be added** —
  macro-event-window trades average 2x the P&L of ordinary days for this
  candidate (75-trade sample), the opposite of the naive hypothesis.
- **CONFIRMED 2026-08-24.** The 2024-10-03..2026-08-20 exhaustion this entry
  used to cite is resolved: a DhanHQ backfill added genuinely fresh,
  never-touched NIFTY underlying + option data for 2020-08-03..2024-10-01
  (reconstructed from Dhan's ATM-relative rolling feed, then resampled
  1-minute -> 5-minute to match how this candidate was built and validated
  -- see `dhan_ingest.py`'s module docstring for the reconstruction/validation
  detail). Run through the identical quarter-by-quarter discipline as the
  original 7-quarter check, unchanged strategy/parameters, across all 17
  quarters this new range covers: **15/17 profitable (88%)**, 2,356 trades,
  net P&L +593,543.75, overall +5.98% ROI on capital actually deployed --
  including the full 2022 bear market and 2020 COVID-recovery volatility.
  Both losing quarters (2023-Q2, 2024-Q3-partial) were small/near-breakeven,
  not blowups. Wired into live paper trading the same day, before this
  confirmation run was performed (an informed, user-directed choice, not
  a process violation) -- see the `CANDIDATE_B_*` constants near the top of
  `connections.py`. Full detail: grep `BACKTEST_FINDINGS.md` for
  `## 2026-08-24 — Candidate B confirmed on fresh 2020-2024 data`.
- **2026-08-25 fine-tuning pass, both items resolved with a "no change" answer.**
  A parameter re-sweep (1-year quick look, 24 combinations) found the current
  live indicator periods already sit at a local optimum -- nothing tested beat
  them. A DhanHQ implied-volatility filter (`band 10-20`: only enter when IV
  is 10-20%) looked like a real, cross-validated capital-efficiency win on
  screening data (+9.53%/+4.54% ROI vs baseline's +7.25%/+4.08% across two
  splits) -- then **failed a genuinely fresh 18-month confirmation test**
  (2025-03..2026-08, never touched before): both IV filter variants
  underperformed the no-filter baseline (3.27%/3.95% ROI vs baseline's
  4.02%). **Rejected** -- exactly the failure mode this project's
  fresh-data-confirmation discipline exists to catch. The IV data itself
  (16.2M+ backfilled values, zero warnings) remains archived and usable for
  a different hypothesis later. Full detail: grep `BACKTEST_FINDINGS.md` for
  `## 2026-08-25 — Fine-tuning pass`.

Full detail: grep `BACKTEST_FINDINGS.md` for `## 2026-08-22 — Trend-confirmed momentum`
(3 entries: parameter sweep, round 2 entry+exit sweep, per-trade loss post-mortem),
`## 2026-08-22 — Candidate B across all 7 quarters`, and
`## 2026-08-22 — Candidate B: signal confidence and open interest`.

## Second candidate: opening-range breakout (2026-08-22)

`OpeningRangeBreakoutStrategy` (`opening_range_bars=6`, 30-minute opening
range) + candidate B's exit shell (`stop_risk_fraction=1.6,
target_return=0.30`) — originally screened as "unremarkable" with the
wrong (trend-tuned) exit shell; fixing that turned it solidly profitable
with real dev/val generalization (win rate and PF improving together,
38.8%→42.9% / 2.52→2.75). **Also profitable in all 7 quarters** of the
extended history (Oct 2024 - May 2026): 298 trades, +182,937.85 combined,
win rate 38.8%-56.4% throughout, including 2025 Q4 (+24,171.00) — the
quarter where Baseline and the ML-filtered model both lost. A
structurally different signal from candidate B (session-open breakout,
not mid-day trend confirmation), so worth keeping as a second track
rather than folding into it. Labeled **Open** — same test-range
constraint as candidate B (see below). Full detail: grep
`BACKTEST_FINDINGS.md` for `## 2026-08-22 — Opening-range breakout`.

**2026-08-23 follow-up: candidate B's premium/OI filters do NOT transfer to
ORB** — `minimum_open_interest=100000` nearly halves its validation P&L
(excludes one of its *best* trades, not a worst one); premium floors are
a wash. A time-of-day pattern from ORB's own loss post-mortem (early
breakouts lose, late ones win) looked dramatic on one dev/val split but
was **rejected by the 7-quarter check** — cuts total trades in half and
total P&L by 59% with no consistent win-rate benefit. The exit shell was
also re-swept (systematically, for the first time) and confirmed correct
for an understood reason: unlike candidate B, ORB does not tolerate an
uncapped target. Current recommendation for ORB is unchanged:
`opening_range_bars=6` + `stop_risk_fraction=1.6, target_return=0.30`,
no additional filters. Grep `## 2026-08-23` entries in
`BACKTEST_FINDINGS.md` for the full analysis.

**DOWNGRADED to Rejected 2026-08-25.** Run through the identical 17-quarter
fresh-data check that confirmed candidate B (same 2020-2024 DhanHQ range,
unchanged parameters): only **10/17 quarters profitable (59%)**, a real
four-quarter losing streak through 2023, blended ROI of just **+0.82%**
across the whole span (candidate B's is +5.98% on the identical range).
Does not reproduce the "profitable in all 7 quarters" result the original
screening found -- that range was real but short (under 2 years) and
evidently not representative of a full market cycle. **Not added to any
live/combined portfolio.** Full detail: grep `BACKTEST_FINDINGS.md` for
`## 2026-08-25 — ORB downgraded`.

**2026-08-23, second round: three more ideas tested, all rejected or
neutral.** EMA-separation (trend strength) filter on candidate B —
rejected, monotonic degradation at every threshold. Real 15-minute
multi-timeframe confirmation vs. the existing same-timeframe EMA proxy —
nearly identical results, validates the original shortcut rather than
replacing it (the candle resampler was generalized to support real
resampling of non-1-minute source data along the way — see
`candle_resample.py`). Gating ORB's entries with candidate B's macro
trend — looked good on one dev/val split, rejected by the 7-quarter check
(worse in 6/7 quarters). Neither candidate's recommendation changed.
Grep the three `## 2026-08-23` entries after the exit re-sweep in
`BACKTEST_FINDINGS.md`.

**Diversification check: the two candidates are nearly uncorrelated
(daily P&L correlation 0.068).** Running both together (independent
parallel signals, not merged) gives a meaningfully better P&L-to-drawdown
ratio than either alone. Working plan once either gets its formal test
confirmation. Grep `## 2026-08-22 — Candidate B and opening-range
breakout: nearly uncorrelated` for the full analysis.

**Extended to three, 2026-08-23: the selective short strangle (see below)
is also near-zero correlated with both** (-0.021 vs. candidate B, 0.016
vs. ORB) and adds 3.5% more total profit to the combined book with no
increase in the portfolio's own max drawdown. See the "Data-integrity
bug found and fixed" 2026-08-23 entry for the full three-way numbers.

**Caution added 2026-08-25: this whole diversification argument rests on
ORB being a real, reliable profit source, and ORB was downgraded to
Rejected the same day** (see above) -- only 10/17 quarters profitable and
+0.82% blended ROI on the fresh 2020-2024 data, a small fraction of
candidate B's own +5.98% on the identical range. Low correlation with a
strategy that barely holds its own doesn't provide the diversification
benefit this section describes; the two-way (candidate B + short
strangle) and three-way portfolio numbers above have not been re-checked
on fresh data and should not be trusted as-is until ORB's role in them is
reconsidered or re-run without it.

## Data coverage (verify freshness before trusting this)

- Real Upstox `FIVE_MINUTE`: 2024-10-03 to 2026-08-20 (`derived_from_timeframe IS NULL`).
- Derived (resampled from real 1-minute, tagged `derived_from_timeframe='ONE_MINUTE'`):
  fills a small remaining gap. Backtest engines exclude derived data by
  default; pass `include_derived=True` to include it (needed for any
  range touching 2024-10-03..2025-12-31).
- 2024-10-03 is Upstox's verified hard platform ceiling — no older data exists to pull.
- Grep `## 2026-08-21 — Data-integrity incident` and `## 2026-08-22 — Historical extension`
  in `BACKTEST_FINDINGS.md` for why this matters and what was fixed.

## ML signal-quality filter (separate from the strategy work above)

- v1/v2: thin data, inconclusive. v3 (15x more data): real improvement.
  v4 (hyperparameter + threshold sweep): best single-split result. v5
  (rolling-origin, 6 folds): 5/6 quarters profitable — strong
  generalization evidence. v6 (open interest, new `upstox_ml_backtest_v2.py`
  engine): almost no improvement over v4's simpler model. v7 (added
  `is_macro_event_window` as an 8th feature): also negligible improvement
  over v4 — likely redundant with the existing `atr_normalized` feature.
- **v4 remains the best ML candidate.** Model file:
  `research/models/ml-signal-quality-v4-hyperparam-swept.json`.
- Grep `## 2026-08-22 — ML signal-quality filter` in `BACKTEST_FINDINGS.md`
  for all entries (v3-v7).

## Infrastructure already built (don't rediscover these)

- **Fast paths exist for `MomentumStrategy` and `TrendConfirmedMomentumStrategy`**
  (`signal_from_indicators` / `signal_from_indicators_with_macro` in
  `upstox_backtest.py`'s `generate_signals_from_candles`) — O(n) instead of
  O(n²). Any *other* strategy without one falls back to the slow per-step
  path; check before running a large backtest with a new strategy class.
- `run_upstox_backtest`/`run_upstox_ml_backtest` take `include_derived: bool`
  (default `False`) and `minimum_option_premium`/`exclude_macro_event_days`
  on `BacktestParameters`.
- `src/options_bot/market_events.py` — known RBI MPC / FOMC / Union Budget
  dates, verified via web search 2026-08-22. Extend as new schedules publish.
- `src/options_bot/strategy_experimental.py` — `MeanReversionStrategy`
  (Rejected — 0% win rate as implemented, AND the follow-up wider-stop
  hypothesis was tested with 9 exit-shell variants and also failed; every
  variant lost on both dev and val), `OpeningRangeBreakoutStrategy` (Open —
  see "Second candidate" above, weak with the default exit shell but
  strong with candidate B's), `TrendConfirmedMomentumStrategy` (see above).
- `src/options_bot/upstox_ml_backtest_v2.py` — supports post-contract ML
  features (open interest, days-to-expiry); v1 (`upstox_ml_backtest.py`)
  stays precontract-only and simpler.
- `research/roi_ledger.py` / `research/roi_all_runs.json` /
  `research/build_roi_ledger_html.py` — a durable, appendable record of
  every backtest run's invested capital and true return-on-capital %
  (kept separate from win rate), plus an HTML table generator. Call
  `record_run(group, label, start, end, result)` after any backtest worth
  keeping, then re-run the build script. Do this going forward instead of
  only printing to console -- the first ~180 rows had to be reconstructed
  after the fact because nothing was recording this as it ran.
- `src/options_bot/short_premium_backtest.py` — a new, separate engine for
  short-premium (non-directional) strategies, starting with a short
  strangle. Not a variant of `upstox_backtest.py`: selling options has a
  genuinely different risk shape (uncapped loss, not capped by a premium
  paid upfront) so it gets its own `ShortStrangleParameters`/
  `ShortPremiumResult` rather than reusing the long-only types. Run
  unconditionally (every day), the 7-quarter check came back **Rejected**
  (only 3/7 profitable, later corrected to 4/7 -- see below). Per user
  correction, kept as a *selective* tool instead of discarded:
  `maximum_opening_range_pct` skips days whose first 30 minutes already
  moved too much (same-day, no lookahead).
  **A real data bug (option-leg queries never filtered by `timeframe`,
  silently mixing ONE_MINUTE and FIVE_MINUTE candles) was found and fixed
  2026-08-23** -- every short-strangle number above was re-verified after
  the fix, and came back *more* favorable: selective net P&L (+14,285.15)
  now beats baseline's (+7,953.75) outright on 30% fewer trades, plus a
  ~58% worst-quarter drawdown cut. Correlation with candidate B and ORB is
  near-zero (-0.021 and 0.016) -- a genuine third diversifier. Adding it
  to the combined B+ORB book raises total profit 3.5% with **zero**
  increase in the portfolio's own max drawdown. Labeled **Open**,
  available as a tool, not deployed by default. See the 2026-08-23
  entries, especially "Data-integrity bug found and fixed." Tighter
  stop/target settings were also tried and don't help -- validation was
  unchanged across every setting tested; entry selectivity (already
  built) remains the real lever for this strategy, not exit tuning.
- `BacktestParameters.trailing_activation_return` — lets `trailing_stop`
  wait until a position is in real profit before it starts ratcheting,
  instead of from the very first candle. Tested against candidate B and
  not adopted (see 2026-08-23 entry) but the mechanism itself is real,
  tested infrastructure available for future use.
- `BacktestParameters.minimum_opening_range_pct` — the flip side of the
  strangle's filter (require a *wide*, not narrow, opening range).
  Tested against candidate B/ORB and rejected -- monotonic degradation on
  both, both splits. Real, tested infrastructure; just not a working
  filter for these two strategies.

## The one real constraint blocking further progress

Not more backtesting — a fresh, never-touched NIFTY/FIVE_MINUTE date range
to spend candidate B's one-shot `test` confirmation on
(`src/options_bot/research_ledger.py` enforces this mechanically). That
needs either a future data pull past 2026-08-20, or forward-paper evidence.
