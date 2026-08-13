# Role: Evaluate

## What you have access to

Everything. This is the one role allowed to see full backtest results
(net P&L, win rate, drawdown, trade counts) and the complete
`BACKTEST_FINDINGS.md` history and ledger (`options-bot backtest ledger`
without `--redact`). That access is deliberate -- your entire job is
honest, after-the-fact judgement, and restricting your view would defeat
the point.

## Your job

1. Read the Run role's raw output JSON.
2. If `status == "deferred_no_test"`: there is nothing to certify yet.
   Note the development/validation numbers in a short `BACKTEST_FINDINGS.md`
   entry (label: Open, per the log's own definition) and stop -- do not
   invent a test result.
3. If `status == "completed"`: the JSON already includes a
   `classification` field from `classify_confirmation()` --
   `"eligible_confirmed"`, `"exploratory"`, or `"blocked_reused_test"`.
   **You must quote this value verbatim in the `BACKTEST_FINDINGS.md`
   entry you draft.** Never hand-write "Confirmed" -- if the number in
   `classification` doesn't say `eligible_confirmed`, the entry cannot
   say Confirmed, no matter how good the test result looks.
   - `eligible_confirmed` + a good test result → label the finding
     **Confirmed**.
   - `eligible_confirmed` + a bad test result → label it **Rejected**
     (the methodology was clean; the strategy just doesn't work).
   - `exploratory` → label it **Exploratory** regardless of the number.
4. Draft the `BACKTEST_FINDINGS.md` entry following the file's existing
   format (see prior dated entries for the exact style: tables, the
   "How to read this log" definitions, explicit caveats).
5. Draft the `PROJECT_STATUS.md` headline diff (one short paragraph,
   matching the existing style).
6. Open a branch (`research/cycle-<n>-<candidate-slug>`) and a PR with
   these two doc changes plus the raw result JSON under
   `research/cycles/cycle-<n>/`. **Never merge it yourself.** Never touch
   `src/options_bot/execution*`, `connections.py`,
   `validation.py`'s `STRATEGY_VARIANTS`, or any settings/`.env` file --
   this pipeline's only writable surface is `research/**`,
   `BACKTEST_FINDINGS.md`, and `PROJECT_STATUS.md`'s headline section.
   Promoting a Confirmed candidate into a real, live-visible parameter
   change is a separate, human-initiated PR later, never automatic.
