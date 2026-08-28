# Evaluation protocol — read before running anything

## Why this folder exists

The strategy in `STRATEGY.md` was **found by searching a six-year archive**. It
looks good on that archive, which proves very little: it was selected *because*
it looks good there. Two strategies in this project were previously called
"confirmed" on exactly that basis and both had to be retracted.

The only test that can settle it is data the rule was never derived from. This
folder exists to run that test in a way that cannot quietly become another
search.

## The rules

**1. Evaluate on data the strategy has never seen.**
The archive it came from covers **2020-08-03 to 2026-08-20**. Every date in that
range has been used for development, validation, or a held-out check. None of it
is clean. Valid evaluation data is dated **after 2026-08-20**, or is a different
underlying the strategy was never built on.

**2. One run, one result, recorded.**
Append every run to `RESULTS.md` — date, data range, trade count, P&L, drawdown —
*before* interpreting it. A run that is not recorded did not happen. Re-running
the same window until it looks better is the failure mode this protocol exists to
prevent.

**3. Change nothing.**
No parameter tuning, no "small adjustment", no trying 60/40 and then 58/42. If
the result is bad, that is the finding. Anything in `STRATEGY.md` that is altered
means the evaluation record starts again from zero.

**4. Costs stay on.** Non-negotiable. See `STRATEGY.md`.

**5. The evaluator should not be told what to expect.**
If a fresh agent runs this, it must not be given the historical results, the
development windows, or the fact that a particular number would be encouraging.
Everything it needs is in `STRATEGY.md` and `evaluate.py`.

## Pre-registered success criteria

Fixed now, before any evaluation data exists. Judged on the **accumulated**
forward record, not on individual runs cherry-picked from it.

| Criterion | Threshold |
|---|---|
| Minimum sample before judging | **40 trades** (~10 months at expected frequency) |
| Net P&L after real costs | **> 0** |
| Win rate | **≥ 33%** (baseline is ~34%; below this the RSI band is doing nothing) |
| Profit factor | **≥ 1.15** |
| Max drawdown | **≤ 35%** of peak |
| Longest losing streak | **≤ 20 trades** (the archive's worst was 14) |

**All six must hold.** Meeting four of six is a fail, not a partial pass.

If they all hold on 40+ genuinely unseen trades, the strategy graduates from
**Open** to **Confirmed**, and 4% risk sizing becomes defensible. Until then 2%
is the ceiling, and paper trading is the only appropriate venue.

## What a negative result means

That the edge was an artifact of the search. That is a *useful* outcome — it costs
a few months of paper trading instead of real capital, and it is the reason this
folder exists rather than a live account.

## Honest limitations of this design

- **Forward data accumulates slowly.** At ~1 trade/week, 40 trades is about ten
  months. There is no way to shorten that without weakening the test.
- **A clean-room run proves the strategy works on new data. It does not prove the
  strategy is good.** Even a passing result describes a modest edge on a
  low-frequency system.
- **The strategy could pass by luck.** 40 trades is a small sample; the criteria
  are set to make luck unlikely, not impossible.
