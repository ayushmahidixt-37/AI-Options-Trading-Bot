# Repository agent instructions

1. Read `PROJECT_STATUS.md` before planning or modifying this repository.
2. Treat its safety boundary as mandatory unless the user explicitly requests a
   separately reviewed change and higher-priority instructions permit it.
3. Update `PROJECT_STATUS.md` in the same commit whenever completed features,
   current phase, next priorities, operating commands, file locations, known
   limitations, or safety decisions change.
4. Keep the status document concise enough to serve as the canonical handoff;
   do not place credentials, tokens, passwords, PINs, or TOTP secrets in it.
5. Run the checks listed in `PROJECT_STATUS.md` before completing code changes.
6. Log every real backtest round (development/validation/untouched-test
   results, not exploratory single-range runs) as a dated entry in
   `BACKTEST_FINDINGS.md` — what was tested, what was confirmed, what was
   rejected, and why. Update `PROJECT_STATUS.md`'s headline only with the
   short summary; keep the detailed record in `BACKTEST_FINDINGS.md`.
7. Never hand-write a "Confirmed" label. `src/options_bot/research_ledger.py`
   (`check_range`/`record_usage`/`classify_confirmation`) is the
   mechanical enforcement for rule 6's discipline — a test range must
   start strictly after every range ever recorded for that
   underlying/timeframe, and each candidate gets exactly one test
   attempt. Any `BACKTEST_FINDINGS.md` entry for a completed test must
   quote `classify_confirmation`'s actual return value verbatim, not a
   bot's own judgement. `src/options_bot/backtest_cli.py`
   (`options-bot backtest check-range/run/validate-split/ledger`) is the
   only sanctioned way to run a backtest outside the dashboard, so the
   ledger check and record happen atomically around every run.
8. Whenever new Upstox candle data is ingested (CLI, dashboard, or
   `pull_range`), update the "Historical Upstox backtest data — current
   coverage" date range in `PROJECT_STATUS.md` in the same commit. That
   line is the one place a new session should trust for "how much
   history do we actually have" without querying the archive directly.
