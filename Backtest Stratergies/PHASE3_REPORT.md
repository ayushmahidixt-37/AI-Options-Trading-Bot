# Phase 3 — full 6-year test, one rolling ₹1,00,000, no reinvestment

Short version of what changed and what it shows. (Full mechanics are in `PHASE2_REPORT.md`
and `REPORT.md` if you want the detail — this page skips straight to results.)

**Chart:** https://claude.ai/code/artifact/61714799-e3b4-4338-a057-a78182e654e0

## What was run
The five strategies that held up, each with its best-known settings from Phase 2, run
together as one portfolio: up to 5 trades open at once, one shared ₹1,00,000, **never
reinvested** — every trade is always sized off the same ₹1,00,000, not off whatever the
account has grown to. Tested on the full history the data supports: **2020-08-03 to
2026-08-27** (~6 years), not just the earlier 2022-2023 window.

## Result

| | |
|---|---:|
| Starting capital | ₹1,00,000 |
| Final capital | **₹25,57,791** |
| Total profit | ₹24,57,791 |
| Trades taken | 6,405 |
| Win rate | 54% |
| Worst drawdown | 17.75% of capital |
| Profit factor | 1.67 (₹1.67 made for every ₹1 lost) |

Money never sat idle waiting for a slot — 5 concurrent positions was enough room for all
five strategies at once, the whole 6 years.

## Where the money came from

| Strategy | Profit |
|---|---:|
| Supertrend + VWAP (7,2) | ₹15,73,014 |
| Straddle Breakeven (9:59) | ₹4,63,042 |
| Short Straddle 9:20 | ₹2,21,117 |
| Opening Range Breakout | ₹1,49,109 |
| Iron Condor (weekly) | ₹51,508 |

**Supertrend carries the portfolio** — about 64% of total profit from one strategy. The other
four are smaller but all real and all positive on their own.

## One strategy dropped
The BankNifty-style Short Strangle looked good in the original 2-year test but **did not hold
up over the full 6 years** — profit factor fell to 1.01 (essentially breakeven) with a 49.6%
drawdown. The short window was luck, not edge. Left out of the portfolio above.

## Addendum: what if the 5-at-once cap were removed?
Tested — no cap at all (effectively unlimited concurrent positions), same 5 strategies, same
6 years, same fixed ₹1,00,000. **Identical result, to the rupee: ₹25,57,791.** Checked directly:
across all 6 years, these 5 strategies never actually needed more than 5 positions open at the
same time anyway — the peak was exactly 5, reached and never exceeded. The 5-slot cap wasn't
holding anything back; it happened to be exactly generous enough already.

## Addendum 2: re-testing the dropped strategy (#8) with different parameters
Asked whether different settings could rescue #8 rather than dropping it outright. Tested,
full 6-year history each time: tightening the combined-stop cap (₹15,000/lot → ₹12,000, ₹10,000,
₹8,000/lot) made **no difference at all** — that cap almost never actually triggers before the
per-leg stop or time exit does, so it's not a useful lever. Moving the short strikes closer to
the money (delta 0.20 spec → 0.10 through 0.45) mattered a great deal:

| Short-strike delta | Net (6 yr) | Profit factor | Max drawdown | Sharpe |
|---|---:|---:|---:|---:|
| 0.10 (far OTM) | -Rs84,859 | 0.64 | 89.7% | -1.53 |
| 0.15 | -Rs38,912 | 0.82 | 66.2% | -0.73 |
| **0.20 (original spec)** | Rs2,455 | 1.01 | 49.6% | 0.05 |
| 0.25 | Rs33,956 | 1.13 | 43.7% | 0.57 |
| **0.30** | **Rs112,972** | **1.44** | **28.1%** | **1.97** |
| 0.35 | Rs117,819 | 1.37 | 26.2% | 1.75 |
| 0.40 | Rs125,984 | 1.33 | 28.2% | 1.61 |
| 0.45 | Rs148,427 | 1.33 | 26.0% | 1.65 |

**Selling closer to the money (delta 0.30) is a real, substantial fix** — turns a breakeven,
account-battering strategy into a genuinely profitable one, best risk-adjusted at 0.30
specifically (highest Sharpe). But it comes with an honest caveat: broken down by year, 2020
through 2025 are all solidly positive — **except the most recent stretch, 2026 (Jan-Aug), which
lost -Rs63,151 on its own**, dragging the 6-year total down by more than a third. Checked
whether this is a strategy problem or a market-wide one: Supertrend and ORB were both still
solidly positive in that same 2026 stretch, so it isn't every strategy having a bad year —
premium-selling specifically (this one worst, #10 also negative, #7 flat) had a rough 2026,
consistent with a more volatile/directional market being harder on option sellers generally.
**Recommendation: delta 0.30 is a legitimate improvement worth remembering, but given the
strategy's very recent performance, this one stays out of paper trading for now rather than
going in alongside the five that are already running** — not fully cleared just because one
parameter fixed the headline number.

## Addendum 3: the 4 Supertrend ideas, tested on the full 6 years
Each idea run standalone against the current (7,2), 5-min, 3-trades/day baseline (net
Rs15,73,014 over 6 years, PF 3.06, drawdown 4.1%, Sharpe 6.44) — then combined with each other
where a combination made sense.

| Idea | Result | Verdict |
|---|---|---|
| **1. Regime filter** (only trade gap-down days, or only gap-up days) | Gap-down: Rs1,62,200 total. Gap-up: Rs5,09,596 total. Both far below the Rs15,73,014 unfiltered baseline — restricting to ~1/3 of days loses much more than any per-trade quality gain. | **Not adopted.** (Interesting side-note: gap-up, not gap-down, was the better-quality subset here — opposite of an older, cruder finding elsewhere in this project. Different strategy, different instrument, doesn't carry over.) |
| **2. Daily-trend confirmation filter** | Rs10,65,348 — better profit factor (3.29 vs 3.06) but noticeably less total profit than trading every signal. | **Not adopted.** The strategy's own VWAP/TWAP confirmation is already doing this job. |
| **3. Trades/day cap** | 1/day: Rs6,41,215. 2/day: Rs13,43,443. 3/day (current): Rs15,73,014. 5/day: Rs15,77,982. Unlimited: identical to 5/day — no day ever produces more than 5 signals anyway. | **No change.** 3/day is already almost exactly the natural ceiling; more just isn't there. |
| **4. Faster timeframe (3-min instead of 5-min)** | Rs21,08,255 — **34% more profit** than the 5-min baseline. But: drawdown roughly doubles (8.6% vs 4.1%) and profit factor drops (2.44 vs 3.06). Tried fixing the extra risk three ways — tighter trade cap, the gap-up filter, a higher ATR multiplier — **none of it worked; every combination was worse than plain 3-min on both profit and risk at once.** | **A real tradeoff, not a free improvement.** More money, meaningfully more bumpy. Worth knowing about, not switching to by default. |

**The honest takeaway: the config already going into paper trading — (7,2), 5-min, every
signal, 3-trades/day cap — turns out to be close to the best of everything tested here.**
Three of the four ideas made it worse; the fourth (3-min) makes more money at real extra risk,
which is a choice for you to make deliberately, not something to adopt by default.

## Bottom line
Five strategies, run together, sharing one ₹1,00,000 that's never reinvested, would have
turned into about **₹25.6 lakh over 6 years** in this backtest — real trading costs included,
real position limits included. Supertrend(7,2) is doing most of the work; the other four add
diversification and roughly a third of the total. Next step is the same as before: paper-trade
it forward before risking real capital on a backtest, however good the number looks.
