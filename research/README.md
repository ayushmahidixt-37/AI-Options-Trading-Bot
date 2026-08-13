# Research pipeline

Supporting files for the continuous anti-overfitting backtest research
loop. See `PROJECT_STATUS.md` for the headline and `BACKTEST_FINDINGS.md`
for the detailed, dated log of every backtest round.

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

Nothing in this directory drives live/forward-paper trading. The only
writable surface for the automated pipeline is this directory plus
`BACKTEST_FINDINGS.md` and `PROJECT_STATUS.md`'s headline section — see
`AGENTS.md` and the "Path to real use" discussion in this project's
planning history for the human-checkpoint boundary this is built around.
