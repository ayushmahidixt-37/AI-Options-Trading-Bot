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

**Rejected — repeated failure across multiple sample sizes (56, 121, and
277 trades, three separate real backtests over this project's history):**
Tighter stop-loss (0.6 fraction) and 10% trailing stop. Both looked
promising on a small sample early on and got worse every time the sample
grew. Do not re-test these without new evidence.

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
