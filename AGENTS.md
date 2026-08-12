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
