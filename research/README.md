# Research pipeline

Supporting files for the continuous anti-overfitting backtest research
loop. **Read `INDEX.md` first** — a short current-state summary (best
candidate, data coverage, what infrastructure already exists) that points
at the right section headings instead of requiring a full read-through.
See `PROJECT_STATUS.md` for the headline and `BACKTEST_FINDINGS.md` for
the detailed, dated log of every backtest round (long and chronological by
design — `INDEX.md` is the fast way in).

- `INDEX.md` — start here. Update it whenever the current-best-candidate
  or data-coverage picture changes; keep it short (pointers, not detail).
- `prompts/` — the five role prompt templates (ideation, validate, run,
  evaluate, digest). Committed and reviewed like code; every subagent
  invocation loads these fresh from disk, never from chat history.
- `cycles/cycle-<n>/` — per-cycle working files (idea/plan/run/evaluate
  JSON output). Small, text, safe to commit.
- `range_usage_ledger.json` — a human-readable, diffable export of the
  `range_usage` table (see `src/options_bot/research_ledger.py`),
  produced by `options-bot backtest ledger --export-json ...`. This is
  metadata only (candidate names, roles, date ranges, outcome labels) —
  never the raw archive database, which stays local and is never
  committed to git.
- `train_signal_quality_model.py` — Windows-dev-machine-only training
  script for the ML signal-quality entry filter (see
  `src/options_bot/ml_features.py`/`ml_model.py`/`upstox_ml_backtest.py`
  and `BACKTEST_FINDINGS.md`'s 2026-08-21 entry). Never run on the Termux
  runtime; only its small JSON output is.
- `models/` — trained model weight files (JSON: feature list, standardized
  coefficients, threshold, training metadata). Small, diffable, safe to
  commit — never the raw training data.
- `materialize_resampled_candles.py` — Windows-dev-machine-only, derives
  5/10/15-minute candles from already-archived 1-minute Upstox data. Every
  row it writes is tagged `derived_from_timeframe='ONE_MINUTE'`
  (`market_candles` column); `run_upstox_backtest`/`run_upstox_ml_backtest`/
  the deep-analysis engine all filter this out, so derived candles can never
  again silently reach a documented finding. An earlier, untagged run of
  this script did exactly that on 2026-08-21 — see `BACKTEST_FINDINGS.md`'s
  data-integrity entry before ever re-running it.

Nothing in this directory drives live/forward-paper trading. The only
writable surface for the automated pipeline is this directory plus
`BACKTEST_FINDINGS.md` and `PROJECT_STATUS.md`'s headline section — see
`AGENTS.md` and the "Path to real use" discussion in this project's
planning history for the human-checkpoint boundary this is built around.
