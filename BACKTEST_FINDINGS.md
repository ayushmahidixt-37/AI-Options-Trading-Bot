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
  called Confirmed rather than Exploratory. The same applies to "Skip
  10:25-11:25" below.

## 2026-08-29 — Cross-strategy time-of-day/day-of-week breakdown, and a new candidate

**Data used:** the same real Upstox archive as above (2026-01-01 to
2026-07-31), still the only period ever ingested — no new calendar days
have accrued since the 2026-08-12 entry, so the untouched-test-range
problem noted above is unchanged.

### Full-range pass (all 11 existing variants, no split — context only)

Same caveat as every full-range pass in this log: single backtest per
variant over the whole 7-month archive, not a development/validation/test
split. Useful for spotting cross-strategy patterns, not for picking a
winner.

| Strategy | Trades | Net P&L | Best hour | Worst hour | Best day | Worst day |
|---|---|---|---|---|---|---|
| Baseline | 277 | +15,400.35 | 11:25-12:25 (+318) | 10:25-11:25 (-308) | Thursday (+258) | Friday (-93) |
| Strict RSI | 209 | +15,529.45 | 12:25-13:25 (+586) | 10:25-11:25 (-327) | Monday (+197) | Friday (-26) |
| ATR floor | 260 | +5,541.25 | 12:25-13:25 (+324) | 10:25-11:25 (-311) | Thursday (+258) | Tuesday (-253) |
| Morning entries | 91 | +12,808.90 | 11:25-12:25 (+483) | 10:25-11:25 (-333) | Thursday (+716) | Friday (-354) |
| Tuesday-Thursday | 169 | +10,565.75 | 09:25-10:25 (+732) | 10:25-11:25 (-296) | Thursday (+258) | Tuesday (-56) |
| No expiry day | 214 | +4,425.70 | 09:25-10:25 (+439) | 10:25-11:25 (-307) | Thursday (+258) | Friday (-93) |
| Tighter stop | 277 | -490.20 | 11:25-12:25 (+307) | 10:25-11:25 (-238) | Thursday (+278) | Friday (-240) |
| 30-minute hold | 277 | +4,817.05 | 12:25-13:25 (+185) | 10:25-11:25 (-165) | Wednesday (+104) | Tuesday (-69) |
| 20% target | 277 | +9,965.70 | 12:25-13:25 (+285) | 14:25-15:00 (-151) | Monday (+149) | Tuesday (-108) |
| 10% trailing stop | 277 | +922.25 | 12:25-13:25 (+345) | 10:25-11:25 (-196) | Wednesday (+130) | Tuesday (-170) |
| No stop-loss cap | 277 | -17,717.15 | 13:25-14:25 (+544) | 10:25-11:25 (-1,081) | Wednesday (+620) | Tuesday (-602) |

Two patterns hold across nearly every variant, not just one:

- **10:25-11:25 is the worst hour in 9 of 11 strategies** — often by a
  wide margin (-1,081/trade on "No stop-loss cap"). This is the most
  consistent single signal seen across every version tested so far.
- **Thursday is the best day in 6 of 11 strategies; Tuesday or Friday is
  the worst day in 9 of 11.**

### New candidate: "Skip 10:25-11:25" — development/validation only

Motivated directly by the pattern above. Adds two new
`BacktestParameters` fields, `excluded_entry_start`/`excluded_entry_end`
(`src/options_bot/backtest.py`), since the existing `entry_start`/
`entry_end` can only express one contiguous window and can't carve a
single hour out of the middle of the day. Same development (Jan-Mar) /
validation (Apr-May) ranges as the first split above, for direct
comparison against Baseline:

| Variant | Dev | Validation |
|---|---|---|
| Baseline (for comparison) | 68t/+20,517.50 | 86t/-626.80/15,561DD |
| **Skip 10:25-11:25** | 59t/+23,418.35 | 76t/**+2,943.25**/12,399DD |

Beats Baseline on both legs, and — like "Morning entries" — is one of
only two variants tested so far that stays *positive* on validation
(Baseline and most others go negative there). Lower drawdown than
Baseline too (12,399 vs 15,561). **Status: `dev_validation_only` — no
test leg was attempted.** The archive has no fresh test range available
(see "Ideas proposed but not yet tested" above); this is `check_range`
reporting `dev_validation_only` correctly, not a shortcut. Not Confirmed,
not yet Exploratory — genuinely **Open**, same bar "Morning entries" is
waiting on: a calendar period past 2026-07-31 that this project has never
looked at in any form.

### Data used for this log

The 7-month archive analyzed here was pulled to a local Termux device via
the dashboard's Upstox ingestion, then transferred once for this analysis
via a temporary GitHub Release asset (deleted after use) — never
committed to git history. Raw market-data SQLite dumps do not belong in
this repository (binary, grows without bound, would break the
fetch/merge cycle `scripts/termux_web.sh` runs on every launch); this log
is the durable record of what was learned from that data, not the data
itself.
