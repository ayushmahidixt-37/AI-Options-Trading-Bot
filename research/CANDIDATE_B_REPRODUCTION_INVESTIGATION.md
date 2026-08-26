# Confirmation reproduction investigation (opened 2026-08-26)

**Status: BOTH confirmed strategies fail to reproduce. Neither Candidate B's nor the short
strangle's documented 2020-2024 confirmation numbers can be regenerated.**

## Summary of both failures

Re-verified with committed scripts (`research/verify_short_strangle_confirmation.py`, and the
grids described below for Candidate B). Both show the same signature — **the trade sequence
reproduces exactly, the P&L does not**:

| Strategy | Trades claimed | Trades actual | Net P&L claimed | Net P&L actual |
|---|---|---|---|---|
| Candidate B | 2,388 | **2,388** (match) | +608,962.50 | **-149,566.00** |
| Short strangle | 526 | **526** (match) | +67,980.00 (12/17 quarters) | **+9,593.50** (9/17 quarters) |

That both engines — written months apart, sharing no exit code — produce identical trade counts but
inflated P&L points at the *reporting* step rather than at any strategy logic. See each section
below for the specific mechanism found.

## Short strangle: ROOT CAUSE FOUND — the confirmation was run with costs disabled

`research/verify_short_strangle_confirmation.py --no-costs` passes `settings=None` to the engine,
which zeroes fees and slippage (`slippage = settings.paper_slippage_bps/10_000 if settings else 0.0`,
same for `fee`). That run reproduces the documented claim **exactly, to the rupee**:

| Run | Quarters profitable | Net P&L |
|---|---|---|
| **Documented claim (2026-08-25)** | 12/17 | **+67,980.00** |
| Re-run with `settings=None` (no fees, no slippage) | **12/17** | **+67,980.00** — exact match |
| Re-run with real configured costs | 9/17 | **+9,593.50** |

**The short strangle's confirmation was an idealised, cost-free run.** A short strangle pays four
orders per trade (sell two legs, buy two back), so at the configured Rs 20/order that is Rs 80 of
fees per trade before slippage on four legs. Across 526 trades, **real trading costs consume 86% of
the claimed profit** — reducing +67,980 to +9,594.

The strategy is *not* worthless: it is still net positive (+9,594) and still profitable in 9 of 17
quarters after costs. But its true edge is roughly one seventh of what the log claims, and at
Rs 9,594 over four years it is thin enough that it cannot carry the "confirmed, ready to deploy"
label it was given.

**Additionally — and not modelled anywhere:** a short strangle requires SPAN + exposure margin, not
premium. `short_premium_backtest.py`'s own module docstring flags this as unmodelled. Real margin
for a short NIFTY strangle runs roughly Rs 1.5-2 lakh per lot, so **an Rs 1,00,000 account cannot
hold even one strangle position**, whatever the backtest P&L says. Any capital-scaling work on this
strategy must model margin first.

Candidate B is currently the only strategy wired into live paper trading. Its confirmation
(`BACKTEST_FINDINGS.md`, "2026-08-24 — Candidate B confirmed on fresh 2020-2024 data") reports
**+608,962.50 net P&L over 2,388 trades**. Re-running the documented configuration on 2026-08-26
produces **-149,566.00 over the same 2,388 trades**. This file records the investigation.

## What is NOT the cause (each ruled out by direct test, not reasoning)

| Hypothesis | Test performed | Result |
|---|---|---|
| Settings drift (`MAX_LOSS_PER_TRADE` raised 400→700 for live) | Forced `max_loss_per_trade=400` explicitly | No change. Standalone scripts already get the 400 default — `_settings_for_archive` reads `os.environ`, not `local-bot.env`, unless the CLI merged it first |
| Archive data changed since the confirmation | Re-ran against `backups/market-data-20260824.sqlite3` (same-day backup) | Identical -13,551.00 for 2020-Q3-partial. Data is not the cause |
| Quarter-by-quarter vs one continuous range | Compared both for all 17 quarters | Real but tiny (a few hundred rupees/quarter, from indicator warm-up at each quarter boundary). Nowhere near the discrepancy |
| Costs disabled (`settings=None`) — the mechanism that explains the strangle | Ran Candidate B with `settings=None` | 112 trades, 25.9% win, **+13,710** — not the documented 30.4% / +24,335. Note `settings=None` also disables the stop entirely (`if settings and stop > 0`), so this is the no-stop/no-target/no-cost ceiling. **Still only 56% of the claim.** Rules this mechanism out for Candidate B |
| Engine code changed since confirmation | `git show faa3629 -- src/options_bot/upstox_backtest.py` | Only change is the additive, default-off `dhan_only` scoping + IV filter. With `dhan_only=False`, `option_source_clause == source_clause`, so the executed SQL is byte-identical. Exit logic untouched. `strategy_experimental.py` untouched since the confirmation |
| Entry filters differ | Compared trade counts per quarter | **Trade counts match the documented table EXACTLY for every quarter checked** (112, 135, 123, 137, 137, 130, 118, 147, 151, 142, 155, 127, 152, 113, 154). Same signals, same contract selection |
| Entry prices / lot size differ | Compared `capital_deployed_total` against the doc's own ROI column | 418,644 (mine) vs 417,410 (implied by doc's +5.83% ROI) — within 0.3%, consistent with a slippage-setting difference only |

**Conclusion so far: the same trades, with the same entries, are being generated. The discrepancy is
entirely in how much each trade wins or loses — i.e. in the EXIT path.**

## What the documented numbers must have contained

The doc reports profit factor alongside net P&L, which pins gross win/loss exactly.
`profit_factor = sum(positive net_pnl) / abs(sum(negative net_pnl))` (verified against a live run).
For 2020-Q3-partial (112 trades, 30.4% win rate = 34 wins, PF 1.68, net +24,335):

    L x 0.68 = 24,335  ->  gross loss = 35,787,  gross win = 60,122

| | Wins | Gross win | Gross loss | avg win | avg loss | reward:risk |
|---|---|---|---|---|---|---|
| **Documented (derived)** | 34 | 60,122 | 35,787 | 1,768 | 459 | **3.85** |
| Reproduced, `target_return=0.30` | 34 | 25,220 | 38,771 | 742 | 497 | 1.49 |
| Reproduced, `target_return=None` | 24 | 52,622 | 45,115 | 2,193 | 513 | 4.27 |
| Reproduced, no stop and no target | 28 | 59,472 | 52,332 | 2,124 | 623 | 3.41 |

## The independent sanity check (why the reproduced number is self-consistent)

This does not depend on any backtest engine — it is arithmetic on the strategy's own shape.
Break-even win rate for a fixed reward:risk R is `1 / (1 + R)`:

- **Reproduced run:** R = 1.49 -> break-even win rate **40.2%**. Actual win rate 30.4% -> *must* lose
  money. Per-trade expectancy = 0.304 x 22.5pts - 0.696 x 12pts = **-1.51 points/trade**. The
  reproduced -13,551 is exactly what this configuration is mathematically obliged to produce.
- **Documented run:** R = 3.85 -> break-even win rate **20.6%**. Actual 30.4% -> profitable.
  Also internally consistent, but only if average wins really were 3.85x average losses.

Both are internally consistent. They cannot both describe the same exits. The question is
therefore narrow and answerable: **which exit configuration produces a 3.85 reward:risk while
keeping the win rate at 30.4%?**

A hard `target_return=0.30` caps every winner at +30% (R ~ 1.5). Removing the cap raises R to ~4.3
but *drops* the win rate to 21.4%, because trades that would have banked the target instead reverse
into losses. The documented row needs both at once — large wins AND a high win rate — which is the
signature of a **trailing stop** (winners run, then get locked in rather than reversing). Notably,
`BACKTEST_FINDINGS.md`'s own 2026-08-23 entry records trailing stops being tested and **not adopted**,
so if the confirmation script used one, the documented parameter line does not describe the run.

## The decisive clue: the documented economics ARE a trailing config's, with someone else's win count

A 12-cell trailing grid (`trailing_stop` x `trailing_activation_return`, `target_return=None`,
`stop_risk_fraction=1.6`) produced a near-exact match on per-trade economics — and **only** on
economics:

| Configuration | Wins | avg win | avg loss | net |
|---|---|---|---|---|
| **Documented (derived from PF)** | **34 (30.4%)** | **1,768** | **459** | **+24,335** |
| `stop1.6, target=None, trail=0.30, activation=0.20` | 25 (22.3%) | **1,769** | **458** | +4,386 |
| `stop1.6, target=None, trail=0.30, activation=None` | 24 (21.4%) | 1,835 | 449 | +4,570 |
| `stop1.6, target=0.30` (the documented parameter line) | **34 (30.4%)** | 742 | 497 | -13,551 |

Average win matches to within Rs 1 and average loss to within Rs 1 — that is not coincidence.
But that configuration yields **25 winners, not 34**.

Applying the documented row's own 34/78 win/loss split to those matched averages:

    34 x 1,769  -  78 x 458  =  +24,422      (documented: +24,335)

**The documented net P&L and profit factor are the trailing configuration's per-trade economics
combined with the baseline configuration's win count.** No single run produces both: the win count
(34, 30.4%) comes only from `target_return=0.30`, and the magnitudes (1,768 / 459) come only from
the trailing config. The two columns of the documented table describe two different runs.

This also explains why the discrepancy is uniformly *positive* across all 15 quarters checked and
why its size varies with market volatility (per-trade gap ranged Rs 47 to Rs 687) rather than being
a constant offset: it is the gap between capped and uncapped winners, which scales with how far
price actually ran in each period.

## Conclusive test: the documented number is not producible by ANY exit configuration

A third grid swept `stop_risk_fraction` in {1.6, 2.0, 2.8, 4.0, 6.0} x `target_return` in
{None, 0.30, 0.60} — 15 more cells, 27 configurations in total across the three grids. Every cell
obeys the same trade-off, with no exception:

| Regime | Win rate | avg win | Net P&L |
|---|---|---|---|
| Capped winners (`target_return` set) | 25.0% – 35.7% | 742 – 1,577 | **negative in all 10 cells** |
| Uncapped winners (`target_return=None`) | 21.4% – 25.0% | 2,044 – 2,193 | +4,996 to **+7,774** |

**The best net P&L achievable by any of the 27 configurations is +7,774** (`stop2.8, target=None`).
The documented +24,335 is more than three times that. No configuration produces a 34-win count
together with ~1,768 average wins: raising the win rate requires capping winners, which
mechanically shrinks the average win. The two documented columns are mutually exclusive.

**Verdict: the documented 2020-2024 confirmation table for Candidate B cannot be reproduced and is
not internally producible by this engine on this data. It must be treated as invalid.**

## What the reproducible numbers actually say

Re-running the documented parameter line (`stop_risk_fraction=1.6, target_return=0.30`) — which is
also *exactly what is wired into live paper trading today* via the `CANDIDATE_B_*` constants in
`connections.py` — gives:

- 2020-Q3 (partial): **-13,551.00** over 112 trades
- Full 2020-08-03 .. 2024-10-01: **-149,566.00** over 2,388 trades

This is not a marginal result, and the expectancy arithmetic above explains why it is structural
rather than bad luck: a 30% profit cap against a fixed ~Rs 640 stop gives a reward:risk of ~1.49,
which needs a **40.2%** win rate to break even; the strategy delivers **30.4%**.

**Consequence: Candidate B is currently enabled for automatic live paper entries in a configuration
that loses money on every historical period tested.** The live-trading boundary is unaffected
(`LIVE_TRADING_ENABLED=false`, paper only), so no real money is at risk, but the auto-entry toggle
should be reconsidered on this evidence.

The least-bad configuration found (`stop_risk_fraction=2.8, target_return=None`, +7,774 on this
quarter) is **not** a recommendation — it is the output of a 27-cell search on a single window,
which is precisely the overfitting pattern this project's own dev/val/fresh discipline exists to
reject. It would need the full confirmation treatment before meaning anything.

## Separate finding: the naive "rolling capital" sizing rule is itself wrong

Running `research/one_month_sizing_analysis.py` over one month (2020-08-03..2020-08-31, 52 trades,
Rs 1,00,000 start) exposed a real defect in how "size from the rolling balance" was implemented —
independent of the strategy's own edge:

| Regime | Final | Return | Lots min/avg/max | Max drawdown |
|---|---|---|---|---|
| FIXED (1 lot, the live bot's setting) | 92,273 | -7.73% | 1 / 1.0 / 1 | 10,062 |
| ROLLING (size by affordable premium) | 40,804 | **-59.20%** | **3 / 10.4 / 28** | **91,372** |

Both regimes took all 52 trades — Rs 1,00,000 is enough capital that nothing is skipped, which
answers the "don't hard-cap at one lot" question directly. But the rolling rule sizes by
**affordability** (`lots = balance x cap% / premium`), and premium is *inversely* related to lot
count while the stop loss is a **fixed rupee distance per lot** (~Rs 640). So a cheap option
produces many lots, and each lot still risks the full Rs 640:

    2020-08-13 13:50  BULLISH  premium 29.27 -> 28 lots -> single-trade loss  Rs 16,742
    2020-08-28 09:20  BULLISH  premium 100.50 -> 4 lots -> single-trade loss  Rs  2,390

The same nominal "risk per trade" produced a **7x swing in actual rupees risked**, purely because
of the option's price. That is backwards: cheap options got the *largest* risk allocation.

**Correct approach (what real position sizing does): size by risk, not by affordability.**
`lots = per_trade_risk_budget / risk_per_lot`, where `risk_per_lot = stop_distance x lot_size`
(~Rs 640 here). At Rs 1,00,000 risking 2% (Rs 2,000) per trade that is a steady ~3 lots regardless
of premium, instead of swinging 3-28. This is the correct fix and should be implemented before any
further capital-scaling work.

Note this does not rescue the strategy: better sizing changes *how fast* a negative-expectancy
system loses, not *whether* it loses. Fixed 1-lot sizing lost 7.73% in the month; risk-based sizing
would land between the two columns above. Sizing is a risk-control question, not an edge question.

## Structural problem this exposed (independent of the outcome)

Neither this confirmation nor the short strangle's confirmation was produced by a committed,
re-runnable script — both were run ad hoc and only their output was pasted into
`BACKTEST_FINDINGS.md`. There is therefore no artifact to diff against, which is the direct reason
this discrepancy is expensive to diagnose rather than trivial. **Every future confirmation must be
produced by a committed script** (as `research/train_short_strangle_ml_model.py` and
`research/capital_compounding_simulation.py` now are), so any number in the findings log can be
regenerated on demand.

## Consequence for the capital simulation

`research/capital_compounding_simulation.py`'s Rs 20,000 and Rs 1,00,000 results were built on the
reproduced trade sequence. They are arithmetically correct **given that sequence**, but the sequence
itself is the thing under dispute — so those returns must not be quoted as findings until this
file closes.
