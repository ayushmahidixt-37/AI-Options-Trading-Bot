# Phase 2 — corrections, parameter tuning, and a shared-capital portfolio

Follow-up to [REPORT.md](REPORT.md) (read that first for the full 21-strategy backtest and
its methodology). This phase: (1) fixes a real sizing bug found while building the portfolio
simulator, (2) sweeps parameters on the six strategies REPORT.md flagged as worth keeping,
(3) backtests them together as one portfolio sharing a single Rs 1,00,000 capital pool with
up to 5 positions open at once, and (4) tests the specific mix-and-match idea from the brief
(a time-of-day-dedicated strategy hand-off) plus reports what a rolling/compounding capital
base actually does to these numbers.

## 1. The bug, and what it changed

`engine.py`'s `size_or_floor(capital, risk_pct, risk_per_unit_rupees, lot_size)` is written to
take risk **per option unit** (per single share/point of premium) and multiplies by `lot_size`
itself to get position-level risk. Every multi-leg selling strategy's script (#7, #8, #9, #10,
#11, #12, #13) instead computed an already-per-**lot** risk value and passed that in — a plain
mistake, mine originally in #7 and repeated into the others because I described the parameter
the same wrong way when briefing the agent that built #8-#13. The effect: the function silently
divided by `lot_size` an extra, unintended time, which almost always floored to 1 lot regardless
of whether the real 1-2% budget could actually have afforded more.

Fixed by passing the true per-unit value at every call site (see each script's diff and
`engine.py`'s now-expanded docstring on `size_or_floor`). Re-ran all seven affected scripts:

| Strategy | Before (buggy) | After (fixed) | Change |
|---|---:|---:|---:|
| #7 Iron Condor | Rs30,489, 100% floored | Rs30,489, 100% floored | **no change** — real risk/lot (~Rs8,300+) is wide enough that even correct math still floors |
| #8 Short Strangle | Rs46,600, 100% floored | Rs46,600, 93.3% floored | no P&L change (the 7 now-correctly-sized trades still land at exactly 1 lot) |
| #9 Bull Put Spread | Rs622 | Rs622 | no change — only 6 trades total, risk/lot too wide either way |
| #10 Straddle 9:20 | Rs68,098, 100% floored | Rs68,098, 76.2% floored | no P&L change, same reason as #8 |
| #11 Strangle 9:20 | Rs39,748 | **Rs48,045** | **+21%** — several trades now correctly size 2 lots |
| #12 Straddle Breakeven | Rs29,772, 100% floored | **Rs159,105, 5.9% floored** | **+434%** — most trades' real risk was well inside budget and just weren't being sized properly |
| #13 Iron Butterfly | -Rs48,098 | -Rs48,098 | no change — still a loser, wide wings mean wide risk/lot either way |

**#12 is the standout correction.** Once fixed, most of its trades legitimately size more than
1 lot inside the stated 1-2% risk budget — it goes from "the weakest of the credit-sellers,
always over-risked" to a genuinely strong, properly-sized result, better than #7 or #8 on raw
net P&L. This also means REPORT.md's "position-sizing reality check" section overstated the
problem for #8/#9/#10/#11/#12 (only #7 and #13 were genuinely, unavoidably too wide for a Rs 1L
account at 1-2% risk — the other five just had a bug in how that was being checked).

`portfolio.py` (built for section 3 below) had the exact same bug on first write, caught by a
sanity check that #3 and #4 — strategies that don't even use `size_or_floor`'s floor path —
were producing zero portfolio fills. Fixed there too before any portfolio numbers below were
generated.

## 2. Parameter sweep

Full grids, in-sample/out-of-sample split, and every judgment call are in
[PARAMETER_SWEEP_FINDINGS.md](PARAMETER_SWEEP_FINDINGS.md) (built off a small web-research pass
on published settings for each strategy type — Supertrend period/multiplier conventions, ORB
timeframe win-rate studies, iron condor delta/DTE/wing-width practice, AlgoTest's "straddle-
width" IV-adaptive wing method, and the 9:20 straddle's commonly-cited 25% SL). Grids were kept
small and literature-informed rather than blindly searched, and a config only "wins" if it beats
the current default on **2023 specifically** (out-of-sample; 2022 was the tuning window) — not
just on the pooled two-year total. Two changes survived that bar:

| # | Strategy | Change | 2023 OOS net, before -> after | Full-window net, before -> after |
|---|---|---|---:|---:|
| 4 | Supertrend+VWAP | period/mult (10,3) -> **(7,2)** | Rs86,930 -> Rs309,491 | Rs166,529 -> **Rs576,789** |
| 8 | Short Strangle | per-leg SL 100% -> **75%** | Rs15,128 -> Rs17,662 | Rs46,600 -> **Rs53,395** |

Everything else (#3, #7, #10, #12) kept its current default — either nothing beat it
out-of-sample, or a config that looked better pooled/in-sample lost narrowly on 2023 alone and
was correctly rejected by the OOS rule (this happened for both #10 and #12's tighter-SL
variants — a useful demonstration of why the discipline matters).

**#4's improvement is the single biggest finding in this whole project.** Faster Supertrend
settings (shorter ATR period, smaller multiplier -> more, earlier signals) more than tripled net
P&L, lifted the win rate from 46% to 54%, profit factor from 2.0 to 3.2, Sharpe from 4.2 to 7.3,
and **cut** max drawdown from 7.2% to 3.2%. Better on every axis at once, confirmed out-of-sample
— this is now the anchor strategy for everything below.

The sizing floor itself did not budge for #7/#8/#10/#12 across any parameter tested (confirmed
independently of the bug above, since these sweeps were re-run after the fix) — see section 1's
table for the real, corrected floor rates. Closing that gap needs more capital or fundamentally
different (narrower/cheaper) structures, not different parameters within these strategies.

## 3. Portfolio backtest — one capital pool, up to 5 concurrent positions

### Roster and mechanics
Six strategies, each strategy's best confirmed configuration: #3 ORB (unchanged), #4 Supertrend
**(7,2)**, #7 Iron Condor (unchanged), #8 Short Strangle **75% SL**, #10 Straddle 9:20
(unchanged), #12 Straddle Breakeven (unchanged, now bug-fixed). Every trade any of the six
would have taken individually is a *candidate*; it's only actually taken if a concurrency slot
is free (max 5 open at once) and it doesn't breach a per-strategy diversification cap. Every
trade that IS taken is re-sized from the trade's own real risk-per-lot against **current**
capital, and its P&L is recomputed exactly (not approximated) from the stored real entry/exit
premiums. Full mechanics in `portfolio.py`'s module docstring.

### Does the diversification cap ("not 5 of the same") actually matter?
Tested max-per-strategy at 1, 2, and 3 (out of 5 total slots) — **identical result at all three
settings.** With six strategies whose signals are naturally spread across different times/days/
structures, two positions from the same strategy are rarely both open at once anyway — the cap
never actually binds. The "make sure it's not 5 of the same" instinct is right in principle, but
with this particular roster it's already true by construction; no explicit cap was needed to
enforce it.

### Does allowing 5 concurrent positions actually help, vs one at a time?
Yes, substantially — this is the clearest result in this section.

Both rows below use the same fixed-capital (non-compounding) sizing, so this is a clean,
like-for-like comparison of concurrency alone:

| | 1 position at a time | Up to 5 concurrent |
|---|---:|---:|
| Candidates taken | 447 of 2,365 | 2,293 of 2,365 |
| Candidates missed (no free slot) | 1,918 | 72 |
| Net P&L missed, at original per-trade sizing | Rs796,741 | Rs65,863 |
| Net P&L (fixed capital, no compounding) | Rs172,099 | **Rs902,977** |
| Win rate | 59.3% | 56.1% |
| Profit factor | 1.89 | 1.80 |
| Max drawdown | 9.5% | 12.0% |
| Sharpe | 3.84 | 5.72 |

Going from 1 to 5 concurrent slots takes total profit to roughly **5.25x** and lifts Sharpe from
3.84 to 5.72, at the cost of a somewhat higher max drawdown (9.5% -> 12.0%, still modest) —
because most of the 6 strategies' signals arrive independently of each other, forcing them to
queue for a single slot means the large majority of candidates simply expire unfilled (that's
the 1,918 missed above, versus real trading capital sitting mostly idle) rather than actually
reducing risk. 5 slots captures nearly all of it (only 72 missed) for a large step up in return,
which is the direct, data-backed answer to "why keep more than one trade open at a time."

### The mix-and-match idea from the brief: dedicate the first ~45 min to one strategy, hand the rest of the day to another
Tested literally: ORB restricted to 09:00-10:00 entries only, Supertrend restricted to
10:00-15:30 only, both strategies otherwise unchanged, same 5-slot/shared-capital portfolio.

| | Un-gated (both compete all day) | Time-gated (ORB AM / Supertrend PM) |
|---|---:|---:|
| Net P&L (fixed capital) | **Rs902,977** | Rs818,578 |

**This specific idea does not help — it's about 9% worse than just letting both strategies
compete for slots across the full session.** The reason: ORB's own signal logic already fires
almost entirely in the first two hours by construction (see REPORT.md's hourly breakdown), and
Supertrend is resilient across the whole session with no losing hour — artificially fencing them
off from each other's time windows only removes some of Supertrend's legitimately profitable
morning trades without adding anything ORB wasn't already going to do on its own. Worth stating
plainly since it's the brief's own suggested combination: tested, and rejected by the data.

### Rolling (compounding) capital — what the brief specifically asked to check
All the numbers above use **fixed** capital (each trade always sized off the original Rs 1L,
never off accumulated profit) — the standard, conservative way to compare strategies. Re-run
with position sizing against the account's **actual, growing** equity instead:

| Capital model | Net P&L (2 years) | Final equity | CAGR | Sharpe |
|---|---:|---:|---:|---:|
| Fixed (no compounding) | Rs902,977 | Rs10,02,977 | 220% | 5.72 |
| Rolling, capped at 20 lots/trade | Rs1,10,27,939 | Rs1,11,27,939 | 976% | 6.11 |
| Rolling, **uncapped** | Rs6,00,41,72,170 | Rs6,00,42,72,170 | 25,547% | 3.03 |

**Read this carefully — the uncapped number is not a realistic forecast, it's a demonstration
of why one is needed.** With ~2,300 trades over 2 years at consistent positive expectancy (PF
1.8, 56% win rate) and 1-2% risk each, fixed-fractional compounding is mathematically explosive
— that's arithmetic, not a strategy claim. A real account cannot actually deploy that much size
into NIFTY weekly options without moving the market or running out of open interest to trade
against; this backtest has no order-book/liquidity data to model that ceiling precisely, so the
20-lot cap is a rough, explicitly-labeled stand-in for "a real, if generous, retail ceiling," not
a calibrated one. **The fixed-capital row (Rs902,977, ~220% over 2 years) is the number to trust
as a floor-case estimate; the rolling rows show the combined edge is strong enough that
reinvesting profits compounds it fast, capacity permitting** — worth revisiting with a real
capacity/liquidity model before sizing a live account off the rolling numbers.

## 4. What to actually use, revised

1. **#4 Supertrend(7,2)+VWAP is now the clear anchor strategy** — the single best result in the
   entire project by a wide margin after tuning, on every metric, confirmed out-of-sample.
2. **#12 Straddle Breakeven (bug-fixed) is now the second-best result**, ahead of both #7 and
   #8 on raw net P&L, and the sizing correction means its numbers can actually be trusted at
   something close to the stated risk budget (only 5.9% of trades still need the floor, down
   from 100%).
3. Run **#3, #4(7,2), #7, #8(75% SL), #10, #12** together as one portfolio, capital shared, up
   to 5 positions concurrently, fixed (non-compounding) sizing as the credible baseline — this
   is a Rs902,977 / ~220% two-year result on Rs1,00,000 capital, PF 1.80, max drawdown 12.0%,
   Sharpe 5.72. Concurrency is doing real work (roughly doubles the single-position-at-a-time
   result) and the diversification cap, while a sound instinct, doesn't need to bind explicitly
   given this particular six-strategy mix.
4. **Do not adopt the first-30-minutes/rest-of-day time-gating idea** — tested, it cost ~9% of
   the portfolio's profit rather than adding to it.
5. **Do not plan around the rolling-capital numbers as a forecast** — they're real arithmetic
   given the trades' actual edge, but assume unlimited liquidity this project cannot verify.
   Use the fixed-capital figures for planning; treat the compounding upside as a reason to
   revisit sizing once real capacity constraints (broker limits, open interest, slippage-at-size)
   are known, not as an expected outcome.
6. Everything else in REPORT.md's "retire" list stands unchanged — this phase did not
   re-examine #1, #2, #5, #6, #13, #14, or the Top-10 daily/swing group.

## Sources consulted for the parameter sweep

- [Supertrend Indicator: Formula, Best Settings And Strategies](https://blog.elearnmarkets.com/supertrend-indicator-strategy-trading/)
- [SuperTrend Indicator Best Settings & Strategy Guide 2026](https://quantzee.com/supertrend-indicator-settings-guide/)
- [Opening Range Breakout Win Rates: 5, 15 and 30 Min Tested](https://orbsetups.com/research/5-minute-vs-15-minute-vs-30-minute-opening-range-which-timeframe-has-the-best-win-rate/)
- [Best Intraday Breakout Strategy for Nifty 50 (8+ Year Backtest Results)](https://intradaylab.com/blog/nifty-orb-breakout-strategy-backtest)
- [Iron Condor Strike Selection: How to Choose the Right Delta](https://optionstradingiq.com/iron-condor-strike-selection/)
- [Iron Condor Success Rate: How to Achieve 86% Wins](https://optionstradingiq.com/iron-condor-success-rate/)
- [Intraday Iron Condor Built Using Straddle Width — AlgoTest Blog](https://algotest.in/blog/intraday-iron-condor-built-using-straddle-width/)
- [How to Master Backtesting on AlgoTest — 9:20 Straddle](https://docs.algotest.in/Time-Based-Algo-Trading/how-to-backtest/backtest-920-Straddle/)
- [The 920 Straddle Strategy Defined — AlgoTest Blog](https://algotest.in/blog/the-920-straddle-strategy-defined/)
- An "optimized 920 straddle, 80%+ annual return" Medium post was found but returned HTTP 403 to
  automated fetching — same result as when this was tried during the original strategy-file
  research (see `strategies-and-parameters.md`'s own note on it). Still unverified; not used.
