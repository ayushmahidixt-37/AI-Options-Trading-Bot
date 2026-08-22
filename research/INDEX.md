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
- **Still labeled Open, not Confirmed.** No fresh NIFTY/FIVE_MINUTE test
  range exists — every range from 2024-10-03 through 2026-08-20 has
  already been touched by dev/val/screening work. Confirmation needs a
  future data pull or accumulated forward-paper evidence, not more
  backtesting on what we already have.

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

**Diversification check: the two candidates are nearly uncorrelated
(daily P&L correlation 0.068).** Running both together (independent
parallel signals, not merged) gives a meaningfully better P&L-to-drawdown
ratio than either alone. Working plan once either gets its formal test
confirmation. Grep `## 2026-08-22 — Candidate B and opening-range
breakout: nearly uncorrelated` for the full analysis.

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

## The one real constraint blocking further progress

Not more backtesting — a fresh, never-touched NIFTY/FIVE_MINUTE date range
to spend candidate B's one-shot `test` confirmation on
(`src/options_bot/research_ledger.py` enforces this mechanically). That
needs either a future data pull past 2026-08-20, or forward-paper evidence.
