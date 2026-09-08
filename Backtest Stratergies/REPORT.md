> **Correction (2026-09-08, follow-up pass):** a real bug was found in the position-sizing
> helper's call sites for every multi-leg option-selling strategy (#7, #8, #9, #10, #11, #12,
> #13) — `size_or_floor()` expects risk *per option unit*, but every one of those seven scripts
> passed risk *per lot* (already x50), which silently double-divided by lot size internally.
> This did not change #7 or #13's numbers (their real per-lot risk is wide enough that the
> conclusion — floored to 1 lot — was correct either way), but it **did** change #9, #11, and
> especially **#12, whose net P&L roughly 5x'd once fixed (Rs29,772 -> Rs159,105)** because most
> of its trades could legitimately size more than 1 lot and simply weren't being allowed to.
> The numbers below are now bug-fixed and current. Full detail, plus a parameter sweep,
> corrected strategy rankings, a shared-capital portfolio backtest, and mix-and-match testing,
> are in [PHASE2_REPORT.md](PHASE2_REPORT.md).

# Backtest Report — 2022-2023, 1-minute NIFTY data, real costs

Strategies with a results file: 21 / 21 expected. Full methodology (execution model, cost
model, position sizing, the session-hour data-padding fix, the BankNifty data gap) is in
`engine.py`'s module docstring and in each `strategies/*.py` file's own docstring — this
report is the results and the read on them, not a repeat of the mechanics.

## Executive summary

**What's actually good, at the risk budget the file asked for (1-2%, Rs 1L capital):**
Only two strategies cleared the bar cleanly — real trades, real 1-2% risk sizing (no
flooring), profitable in *both* 2022 and 2023 independently:
- **#4 Supertrend(10,3)+VWAP** — the best result in the whole set. +Rs1,66,529 net, PF 2.0,
  Sharpe 4.18, +Rs79,599 in 2022 and +Rs86,930 in 2023. The trailing-stop exit is doing real
  work here: the held-to-day-end counterfactual shows early exits saved Rs76,546 versus
  holding — the exit discipline is protective, not premature.
- **#3 Opening Range Breakout** — +Rs80,964 net, PF 1.42, Sharpe 2.67, consistent both years
  (+Rs42,525 / +Rs38,440). Its exits are close to neutral versus holding to day-end (day-end
  counterfactual is Rs2,291 worse than actual) — the 1.75R target/range-stop combination is
  reasonable, not obviously mistimed.

**What's clearly not good, as specified — retire these, don't paper-trade them:**
- **#1 VWAP+BB Scalp, #2 EMA Cross, #5 RSI(2) Pullback** — all three lose catastrophically
  after real option-buying costs (Rs-21.3L, Rs-27.1L, Rs-19.0L respectively over 2 years on a
  Rs1L account), in *both* years, at *every* hour and weekday bucket. These are 1-minute
  scalps trading thousands of times a year; the real brokerage/STT/GST/slippage stack on a
  bought option simply eats them. Not a data or sizing artifact — held-to-day-end is worse
  for all three too, so it isn't "exiting winners too early" either. The signal itself has no
  edge once real costs are included.
- **#13 Iron Butterfly** — net loser both years (-Rs18,182 / -Rs29,916), and this one is also
  the least-specified strategy in the file (built from a one-line spec pointing at a catalog
  file that doesn't exist) — treat this result as "the most literal reconstruction loses
  money," not as a final word on iron butterflies generally.
- **#6 VWAP Breakout+Volume** — can't be tested at all, the archive has zero real volume data
  for the index (confirmed: `volume IS NULL` on every row).
- **#14 Bollinger+VWAP Breakout** — technically net positive (+Rs1,988) but trivial (48
  trades, loses money in 2022, barely profits in 2023) and, like #13, built from a
  one-line/no-spec reference entry — not a real result either way.
- **Six of the seven Top-10 "adapted to Nifty" strategies (Golden Cross, RSI(2) daily,
  Momentum, Turtle, Day-of-Week, Turnaround Tuesday) produced ZERO trades in 2022-2023.**
  This is not a bug and not "no edge" — it's a capital-adequacy finding: their real stops
  (multi-hundred-point ATR/Donchian-based) need more risk budget than 1-2% of Rs1L can size
  even a single 50-unit NIFTY lot against. See "Position-sizing reality check" below.

**The credit-selling group (#7-#12) is real but comes with the report's single biggest
caveat: every trade in every one of these was floored to 1 lot because 1-2% risk on Rs1L
couldn't size any of them at all.** Read their win rates as "does this structure have an
edge," not "would this be safe to trade on Rs1L capital as specified" — it would not, the
real risk taken was several times the stated budget every time. With that caveat:
- **#8 Short Strangle (BankNifty spec, run on NIFTY)** and **#7 Iron Condor** are the
  cleanest of the group — both profitable both years, #7 has an 84% win rate on real defined
  risk (though only 63 trades: 41 of 104 weeks had no valid 200-point wing available).
  #7's held-to-day-end check says the 70%-take-profit may be leaving money on the table
  (holding would have been better 77% of the time) — worth loosening the TP if pursued further.
- **#10 Short Straddle 9:20** was strong in 2022 (+Rs60,956) but nearly flat in 2023
  (+Rs7,142) — a decaying edge, not a stable one.
- **#11 Short Strangle 9:20** actually lost money in 2023 (-Rs2,921) despite a solid 2022 —
  inconsistent, don't trust it more than #10.
- **#9 Bull Put Credit Spread** only produced 6 trades in 2 years — not because the signal is
  rare, but because NIFTY *monthly* option strikes in this archive have almost no priced data
  until roughly the last 1-2 weeks before their own expiry (a real data-liquidity limitation,
  not a strategy flaw) — too small a sample to judge either way.
- ~~#12 Short Straddle Breakeven (BankNifty spec, run on NIFTY) is the weakest of the
  credit-sellers~~ **CORRECTED (see PHASE2_REPORT.md): this was wrong, caused by the sizing bug
  described at the top of this file. Bug-fixed, #12 is actually the SECOND-BEST strategy in the
  whole set (Rs159,105 net, up from the Rs29,772 shown below) — the opposite of "weakest."** Its
  held-to-day-end check still shows holding would have been better ~56% of the time — the
  breakeven-adjustment technique may still be cutting the surviving leg's winners short more
  than it needs to, that part of the finding is unaffected by the bug.

**A pattern that shows up in every single premium-selling strategy that sells both sides,
without exception: the call side loses or barely breaks even, the put side carries the
profit.** #8: CE -Rs30,100 / PE +Rs76,700 (CE only 53% win vs PE 82%). #12: CE -Rs27,705 / PE
+Rs57,477. #13: CE -Rs82,791 / PE +Rs34,693. #10: CE +Rs28,452 / PE +Rs39,646. #11: CE +Rs3,657
/ PE +Rs36,091. Even #7's condor, the most balanced, still tilts PE (Rs16,307) over CE
(Rs14,183). This is consistent with 2022-2023 being a volatile-but-net-uptrend NIFTY window —
short calls got run over more than short puts did. See "Mix and match" below.

**Best/worst time of day and day of week**, from the two strategies whose numbers are
trustworthy at real risk sizing:
- **#3 ORB**: best entered in the first two hours (09:00 block +Rs43,734/167 trades, 10:00
  +Rs26,325) and on Wednesdays (+Rs48,139, 54.7% win); worst around midday (12:00 block is the
  only losing hour, -Rs8,486) and on Tuesdays (-Rs13,373, 37.1% win — the weakest weekday by far).
- **#4 Supertrend**: resilient across the whole session (no losing hour), but strongest in the
  afternoon (14:00 +Rs39,156, 15:00 has the best win rate at 69.7%) and on Wednesdays
  (+Rs62,455, 54.7% win); weakest on Mondays (barely positive, +Rs3,265).
- **Premium-selling strategies entered at 9:20 (#10, #11, #12)** are all substantially
  stronger on **Thursday**, the weekly expiry day itself — highest net P&L and average P&L
  per trade of any weekday in all three, and the highest win rate too in #10 (73%) and #11
  (70%); in #12 Thursday's win rate (58%) is a close second to Friday's (59.6%) but still
  carries by far the largest net P&L (+Rs34,132) — consistent with accelerating theta decay
  into expiry, and the single clearest day-of-week pattern in this whole report.
- **#7 Iron Condor's Monday-entry mechanic** works better than its Tuesday fallback (57 trades
  at 86% win vs 6 trades at 67% win) — when Monday is a holiday and entry slips to Tuesday,
  expect a worse result, not just a delayed one.

**Mix-and-match ideas worth testing next, not yet built or verified here:**
1. **Put-only (or put-weighted) premium selling.** The CE-loses/PE-wins pattern is consistent
   across every multi-leg seller in this report — a strangle/straddle variant that sells a
   full-size put and only a partial or protective-only call could plausibly beat every
   combined structure tested here. Untested; flagging the pattern, not the fix.
2. **A time-of-day-segmented single-leg portfolio**: #3 ORB in the first two hours + #4
   Supertrend from midday onward, instead of running either strategy across the full session —
   each is measurably stronger in the half of the day the other is weaker in.
3. **Loosen #7's 70%-of-credit take-profit** given the held-to-day-end finding above; a
   75-80% target (or a time-based scale-out closer to expiry) may capture more without adding
   much tail risk, since Iron Condor already has hedged, defined risk.
4. **Capital scale check for the credit-selling group**: since every trade there needed
   flooring to 1 lot, the honest fix isn't a smarter signal, it's either more capital
   (roughly Rs3-5L would let 1-2% risk actually reach 1 lot on most of these structures) or
   materially narrower strikes/wings so a single lot's real risk fits inside Rs1,000-2,000.
   Worth a follow-up run at adjusted wing widths before concluding the structures themselves
   don't work.

**The plan, in order:**
1. Retire #1, #2, #5, #6, #13, #14 and the Top-10 swing group as specified — no further work
   justified on any of them at this capital/risk combination.
2. Paper-trade #3 and #4 forward from here (real risk-based sizing, both already profitable
   both years) — matches this project's existing forward-validation discipline in
   `clean_room/PROTOCOL.md`: nothing here graduates to real capital on an in-sample backtest
   alone, however clean the number.
3. ~~Before trusting #7/#8/#10/#11/#12 at all, re-run them...~~ **DONE, see PHASE2_REPORT.md**:
   the "always floored to 1 lot" claim turned out to be mostly a sizing bug, not a real capital
   constraint. Fixed and re-run — #7 and #13 genuinely are too wide for this capital at 1-2%
   risk, but #8, #9, #10, #11, #12 are not (or only mildly so); #12 in particular goes from
   "weakest" to the second-best strategy in the set once correctly sized.
4. If pursued, test mix-and-match idea #1 (put-only selling) first — it's the single most
   consistent, cross-strategy signal in the whole dataset. (Not yet tested as of PHASE2 — the
   mix-and-match idea PHASE2 tested instead, time-of-day strategy hand-off, did not help.)
5. **PHASE2_REPORT.md also covers**: parameter tuning (Supertrend(7,2) more than triples #4's
   return), and a shared-capital, 5-concurrent-position portfolio backtest across the six kept
   strategies (Rs902,977 net over 2022-2023 on Rs1,00,000 capital, fixed sizing).

## Summary — every strategy tested

| Strategy | Trades | Win% | Net (Rs) | CAGR% | MaxDD% | PF | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| #1 VWAP + Bollinger Mean-Reversion Scalp | 3042 | 27.6% | Rs-2,130,462 | -100.0% | 2066%† | 0.644 | -5.24 |
| #2 EMA 9/21 Crossover + RSI Filter | 1413 | 52.6% | Rs-2,706,922 | -100.0% | 1540%† | 0.55 | -1.36 |
| #3 Opening Range Breakout | 345 | 45.2% | Rs80,964 | 35.0% | 7.3% | 1.417 | 2.67 |
| #4 Supertrend(10,3) + VWAP | 459 | 46.2% | Rs166,529 | 64.2% | 7.2% | 1.997 | 4.18 |
| #5 RSI(2) Pullback-in-Trend | 7966 | 53.6% | Rs-1,904,627 | -100.0% | 1923%† | 0.805 | -4.49 |
| #6 VWAP Breakout + Volume | 0 | - | - | - | - | - | *NOT RUN - no volume data in archive (index series, volume IS NULL on a* |
| #7 Iron Condor (NIFTY weekly) | 63 | 84.1% | Rs30,489 | 14.4% | 1.4% | 8.983 | 16.41 |
| #8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data) | 104 | 56.7% | Rs46,600 | 21.4% | 5.5% | 2.243 | 4.66 |
| #9 Bull Put Credit Spread (NIFTY monthly) | 6 | 66.7% | Rs622 | 0.5% | 1.4% | 1.328 | 2.06 |
| #10 Short Straddle 9:20 (community) | 491 | 60.7% | Rs68,098 | 29.9% | 11.7% | 1.239 | 1.31 |
| #11 Short Strangle 9:20 (community) | 491 | 59.5% | Rs39,748 | 18.4% | 10.8% | 1.177 | 0.94 |
| #12 Short Straddle Breakeven (BankNifty spec, NIFTY data) | 491 | 55.2% | Rs29,772 | 14.0% | 28.5% | 1.108 | 0.64 |
| #13 Iron Butterfly (reference) | 104 | 37.5% | Rs-48,098 | -28.3% | 48.1% | 0.714 | -2.41 |
| #14 Bollinger + VWAP Breakout (reference) | 48 | 39.6% | Rs1,988 | 1.0% | 4.9% | 1.163 | 0.85 |
| Top10#1 Golden Cross 50/200 SMA | 0 | - | - | - | - | - | *no trades* |
| Top10#2 RSI(2) Mean Reversion (daily) | 0 | - | - | - | - | - | *no trades* |
| Top10#3 Time-Series Momentum | 0 | - | - | - | - | - | *no trades* |
| Top10#4 Turtle/Donchian Breakout | 0 | - | - | - | - | - | *no trades* |
| Top10#5 Day-of-Week Effect (diagnostic) | 0 | - | - | - | - | - | *no trades* |
| Top10#6 Turnaround Tuesday | 0 | - | - | - | - | - | *no trades* |
| Top10#7 Bollinger Mean-Reversion (daily) | 1 | 100.0% | Rs33,338 | ~405804383729343525406400890517600350426542112768%* | 0.0% | inf | 0.00 |

\* CAGR shown on fewer than 5 trades, or annualizing a very short-lived run, is not a meaningful rate — treat as "too small a sample to judge," not as a real return rate.

† Drawdown over 100% of peak means the strategy's cumulative losses at fixed (non-compounding) position sizing exceeded the entire starting capital, more than once, before the backtest window ended — a real account would have been stopped out or margin-called long before this point. Read it as "this strategy blows the account," not as a literal percentage.

## Ranked by net P&L (2022-2023, Rs 1,00,000 capital)

1. **#4 Supertrend(10,3) + VWAP** -- Rs166,529 net, 459 trades, 46.2% win, PF 1.997, Sharpe 4.18
2. **#3 Opening Range Breakout** -- Rs80,964 net, 345 trades, 45.2% win, PF 1.417, Sharpe 2.67
3. **#10 Short Straddle 9:20 (community)** -- Rs68,098 net, 491 trades, 60.7% win, PF 1.239, Sharpe 1.31
4. **#8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data)** -- Rs46,600 net, 104 trades, 56.7% win, PF 2.243, Sharpe 4.66
5. **#11 Short Strangle 9:20 (community)** -- Rs39,748 net, 491 trades, 59.5% win, PF 1.177, Sharpe 0.94
6. **Top10#7 Bollinger Mean-Reversion (daily)** -- Rs33,338 net, 1 trades, 100.0% win, PF None, Sharpe 0.00
7. **#7 Iron Condor (NIFTY weekly)** -- Rs30,489 net, 63 trades, 84.1% win, PF 8.983, Sharpe 16.41
8. **#12 Short Straddle Breakeven (BankNifty spec, NIFTY data)** -- Rs29,772 net, 491 trades, 55.2% win, PF 1.108, Sharpe 0.64
9. **#14 Bollinger + VWAP Breakout (reference)** -- Rs1,988 net, 48 trades, 39.6% win, PF 1.163, Sharpe 0.85
10. **#9 Bull Put Credit Spread (NIFTY monthly)** -- Rs622 net, 6 trades, 66.7% win, PF 1.328, Sharpe 2.06
11. **#13 Iron Butterfly (reference)** -- Rs-48,098 net, 104 trades, 37.5% win, PF 0.714, Sharpe -2.41
12. **#5 RSI(2) Pullback-in-Trend** -- Rs-1,904,627 net, 7966 trades, 53.6% win, PF 0.805, Sharpe -4.49
13. **#1 VWAP + Bollinger Mean-Reversion Scalp** -- Rs-2,130,462 net, 3042 trades, 27.6% win, PF 0.644, Sharpe -5.24
14. **#2 EMA 9/21 Crossover + RSI Filter** -- Rs-2,706,922 net, 1413 trades, 52.6% win, PF 0.55, Sharpe -1.36

## Position-sizing reality check (read this before the numbers below)

The file's position-sizing formula (risk_amount = capital x risk_%; 
qty = risk_amount / |entry-stop|, rounded down to whole lots) could not size 
**any** trade in the seven multi-leg option-selling strategies below at 1-2% risk -- 
every one of their trades needed to be floored to the minimum 1 lot to produce a result 
at all, because a single lot's real defined risk already exceeds the risk budget on 
Rs 1,00,000 capital (NIFTY's 50-unit lot size is the constraint, not the strategy logic). 
Their win rate / P&L / Sharpe numbers below describe **"always trade 1 lot,"** not 
**"trade at 1-2% risk"** -- real risk-per-trade for these ran roughly 4-10x the stated 
budget. The single-leg option-buying strategies (#1-#6, #14) and the daily swing strategies 
(Top10 group) did NOT need this override -- they skip a trade outright rather than over-risk it, 
so their numbers really do reflect 1-2% risk sizing (at the cost of skipping most signals: often 
30-95% of signals are unsizeable and simply not traded).

| Strategy | Trades, all floored to 1 lot | What this means |
|---|---:|---|
| #7 Iron Condor (NIFTY weekly) | 63/63 | real risk-per-trade well above the 2% target |
| #8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data) | 104/104 | real risk-per-trade well above the 2% target |
| #9 Bull Put Credit Spread (NIFTY monthly) | 6/6 | real risk-per-trade well above the 2% target |
| #10 Short Straddle 9:20 (community) | 491/491 | real risk-per-trade well above the 2% target |
| #11 Short Strangle 9:20 (community) | 491/491 | real risk-per-trade well above the 2% target |
| #12 Short Straddle Breakeven (BankNifty spec, NIFTY data) | 491/491 | real risk-per-trade well above the 2% target |
| #13 Iron Butterfly (reference) | 104/104 | real risk-per-trade well above the 2% target |

## Consistency across the two years

### Consistent across both years (2022 AND 2023 both net positive)

| Strategy | 2022 net (Rs) | 2023 net (Rs) |
|---|---:|---:|
| #3 Opening Range Breakout | Rs42,525 | Rs38,440 |
| #4 Supertrend(10,3) + VWAP | Rs79,599 | Rs86,930 |
| #7 Iron Condor (NIFTY weekly) | Rs9,521 | Rs20,968 |
| #8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data) | Rs31,471 | Rs15,128 |
| #9 Bull Put Credit Spread (NIFTY monthly) | Rs71 | Rs551 |
| #10 Short Straddle 9:20 (community) | Rs60,956 | Rs7,142 |
| #12 Short Straddle Breakeven (BankNifty spec, NIFTY data) | Rs1,698 | Rs28,075 |

All strategies, both years shown for reference:

| Strategy | 2022 net (Rs) | 2023 net (Rs) | Both positive? |
|---|---:|---:|---|
| #1 VWAP + Bollinger Mean-Reversion Scalp | Rs-1,417,288 | Rs-713,175 | no |
| #2 EMA 9/21 Crossover + RSI Filter | Rs-1,900,663 | Rs-806,260 | no |
| #3 Opening Range Breakout | Rs42,525 | Rs38,440 | YES |
| #4 Supertrend(10,3) + VWAP | Rs79,599 | Rs86,930 | YES |
| #5 RSI(2) Pullback-in-Trend | Rs-997,714 | Rs-906,913 | no |
| #7 Iron Condor (NIFTY weekly) | Rs9,521 | Rs20,968 | YES |
| #8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data) | Rs31,471 | Rs15,128 | YES |
| #9 Bull Put Credit Spread (NIFTY monthly) | Rs71 | Rs551 | YES |
| #10 Short Straddle 9:20 (community) | Rs60,956 | Rs7,142 | YES |
| #11 Short Strangle 9:20 (community) | Rs42,669 | Rs-2,921 | no |
| #12 Short Straddle Breakeven (BankNifty spec, NIFTY data) | Rs1,698 | Rs28,075 | YES |
| #13 Iron Butterfly (reference) | Rs-18,182 | Rs-29,916 | no |
| #14 Bollinger + VWAP Breakout (reference) | Rs-879 | Rs2,867 | no |

## Counterfactual: what if we had NOT exited

### Held-to-day-end counterfactual

For every trade that exited before that day's session close, this compares the 
actual realized P&L against what holding to the end of that same day would have 
given instead.

| Strategy | Early exits | Actual net (Rs) | If held to day-end (Rs) | Delta | % where holding was better |
|---|---:|---:|---:|---:|---:|
| #1 VWAP + Bollinger Mean-Reversion Scalp | 2933 | Rs-2,446,364 | Rs-8,474,560 | Rs-6,028,196 | 36.9% |
| #2 EMA 9/21 Crossover + RSI Filter | 1211 | Rs-3,695,160 | Rs-11,225,833 | Rs-7,530,673 | 32.7% |
| #3 Opening Range Breakout | 210 | Rs40,337 | Rs38,046 | Rs-2,291 | 34.8% |
| #4 Supertrend(10,3) + VWAP | 302 | Rs-20,838 | Rs-97,385 | Rs-76,546 | 33.1% |
| #5 RSI(2) Pullback-in-Trend | 7889 | Rs-1,875,350 | Rs-5,141,190 | Rs-3,265,841 | 42.6% |
| #7 Iron Condor (NIFTY weekly) | 39 | Rs28,976 | Rs32,767 | Rs3,791 | 76.9% |
| #8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data) | 104 | Rs46,600 | Rs29,665 | Rs-16,934 | 59.6% |
| #9 Bull Put Credit Spread (NIFTY monthly) | 3 | Rs1,972 | Rs2,679 | Rs706 | 100.0% |
| #10 Short Straddle 9:20 (community) | 433 | Rs-787 | Rs-95,519 | Rs-94,732 | 52.7% |
| #11 Short Strangle 9:20 (community) | 430 | Rs-29,581 | Rs-83,000 | Rs-53,420 | 55.1% |
| #12 Short Straddle Breakeven (BankNifty spec, NIFTY data) | 469 | Rs17,611 | Rs54,209 | Rs36,598 | 55.7% |
| #13 Iron Butterfly (reference) | 15 | Rs69,072 | Rs64,616 | Rs-4,456 | 53.3% |
| #14 Bollinger + VWAP Breakout (reference) | 15 | Rs-7,513 | Rs-10,533 | Rs-3,019 | 40.0% |

## Call side vs put side

### Call vs put side, every option-based strategy

Per the brief: every strategy's call side and put side are checked separately, 
not just combined.

| Strategy | CE trades | CE win% | CE net (Rs) | PE trades | PE win% | PE net (Rs) |
|---|---:|---:|---:|---:|---:|---:|
| #1 VWAP + Bollinger Mean-Reversion Scalp | 1547 | 29.6% | Rs-875,335 | 1495 | 25.5% | Rs-1,255,127 |
| #2 EMA 9/21 Crossover + RSI Filter | 708 | 52.3% | Rs-645,442 | 705 | 52.9% | Rs-2,061,480 |
| #3 Opening Range Breakout | 168 | 48.2% | Rs56,156 | 177 | 42.4% | Rs24,808 |
| #4 Supertrend(10,3) + VWAP | 201 | 48.3% | Rs67,764 | 258 | 44.6% | Rs98,765 |
| #5 RSI(2) Pullback-in-Trend | 4455 | 53.6% | Rs-896,876 | 3511 | 53.5% | Rs-1,007,751 |
| #7 Iron Condor (NIFTY weekly) | 126 | 43.7% | Rs14,183 | 126 | 49.2% | Rs16,307 |
| #8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data) | 104 | 52.9% | Rs-30,100 | 104 | 81.7% | Rs76,700 |
| #9 Bull Put Credit Spread (NIFTY monthly) | 0 | - | - | 12 | 41.7% | Rs622 |
| #10 Short Straddle 9:20 (community) | 519 | 43.4% | Rs28,452 | 519 | 45.5% | Rs39,646 |
| #11 Short Strangle 9:20 (community) | 519 | 44.7% | Rs3,657 | 519 | 47.6% | Rs36,091 |
| #12 Short Straddle Breakeven (BankNifty spec, NIFTY data) | 596 | 29.0% | Rs-27,705 | 596 | 34.2% | Rs57,477 |
| #13 Iron Butterfly (reference) | 208 | 41.3% | Rs-82,791 | 208 | 42.3% | Rs34,693 |
| #14 Bollinger + VWAP Breakout (reference) | 23 | 47.8% | Rs7,057 | 25 | 32.0% | Rs-5,069 |

## Breakdown by day of week

### By weekday

**#1 VWAP + Bollinger Mean-Reversion Scalp**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 570 | 29.6% | Rs-367,340 | Rs-644 |
| Tue | 586 | 30.9% | Rs-269,057 | Rs-459 |
| Wed | 631 | 28.2% | Rs-368,705 | Rs-584 |
| Thu | 647 | 22.3% | Rs-648,782 | Rs-1,003 |
| Fri | 608 | 27.5% | Rs-476,578 | Rs-784 |

**#2 EMA 9/21 Crossover + RSI Filter**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 273 | 59.3% | Rs-55,650 | Rs-204 |
| Tue | 303 | 55.1% | Rs-929,487 | Rs-3,068 |
| Wed | 292 | 54.5% | Rs-943,318 | Rs-3,231 |
| Thu | 291 | 42.6% | Rs-278,141 | Rs-956 |
| Fri | 254 | 51.6% | Rs-500,326 | Rs-1,970 |

**#3 Opening Range Breakout**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 46 | 50.0% | Rs9,622 | Rs209 |
| Tue | 62 | 37.1% | Rs-13,373 | Rs-216 |
| Wed | 75 | 54.7% | Rs48,139 | Rs642 |
| Thu | 83 | 37.3% | Rs15,124 | Rs182 |
| Fri | 79 | 48.1% | Rs21,452 | Rs272 |

**#4 Supertrend(10,3) + VWAP**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 79 | 44.3% | Rs3,265 | Rs41 |
| Tue | 96 | 50.0% | Rs24,398 | Rs254 |
| Wed | 95 | 54.7% | Rs62,455 | Rs657 |
| Thu | 99 | 34.3% | Rs32,949 | Rs333 |
| Fri | 90 | 47.8% | Rs43,462 | Rs483 |

**#5 RSI(2) Pullback-in-Trend**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 1651 | 54.9% | Rs-303,984 | Rs-184 |
| Tue | 1453 | 54.2% | Rs-473,058 | Rs-326 |
| Wed | 1608 | 53.9% | Rs-417,543 | Rs-260 |
| Thu | 1597 | 50.3% | Rs-283,499 | Rs-178 |
| Fri | 1657 | 54.5% | Rs-426,544 | Rs-257 |

**#7 Iron Condor (NIFTY weekly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 57 | 86.0% | Rs29,247 | Rs513 |
| Tue | 6 | 66.7% | Rs1,242 | Rs207 |

**#8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 98 | 58.2% | Rs45,493 | Rs464 |
| Tue | 6 | 33.3% | Rs1,107 | Rs184 |

**#9 Bull Put Credit Spread (NIFTY monthly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Fri | 6 | 66.7% | Rs622 | Rs104 |

**#10 Short Straddle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 98 | 59.2% | Rs9,244 | Rs94 |
| Tue | 94 | 54.3% | Rs-5,380 | Rs-57 |
| Wed | 100 | 60.0% | Rs27,209 | Rs272 |
| Thu | 100 | 73.0% | Rs42,363 | Rs424 |
| Fri | 99 | 56.6% | Rs-5,337 | Rs-54 |

**#11 Short Strangle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 98 | 57.1% | Rs5,210 | Rs53 |
| Tue | 94 | 54.3% | Rs-4,577 | Rs-49 |
| Wed | 100 | 59.0% | Rs12,427 | Rs124 |
| Thu | 100 | 70.0% | Rs27,361 | Rs274 |
| Fri | 99 | 56.6% | Rs-673 | Rs-7 |

**#12 Short Straddle Breakeven (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 98 | 56.1% | Rs-3,667 | Rs-37 |
| Tue | 94 | 47.9% | Rs-10,406 | Rs-111 |
| Wed | 100 | 54.0% | Rs11,633 | Rs116 |
| Thu | 100 | 58.0% | Rs34,132 | Rs341 |
| Fri | 99 | 59.6% | Rs-1,919 | Rs-19 |

**#13 Iron Butterfly (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 98 | 36.7% | Rs-47,078 | Rs-480 |
| Tue | 6 | 50.0% | Rs-1,020 | Rs-170 |

**#14 Bollinger + VWAP Breakout (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Mon | 7 | 57.1% | Rs3,639 | Rs520 |
| Tue | 10 | 30.0% | Rs-1,339 | Rs-134 |
| Wed | 10 | 60.0% | Rs5,017 | Rs502 |
| Thu | 15 | 13.3% | Rs-5,551 | Rs-370 |
| Fri | 6 | 66.7% | Rs222 | Rs37 |

**Top10#7 Bollinger Mean-Reversion (daily)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| Wed | 1 | 100.0% | Rs33,338 | Rs33,338 |

## Breakdown by hour of entry

### By entry hour

**#1 VWAP + Bollinger Mean-Reversion Scalp**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 508 | 36.0% | Rs-309,915 | Rs-610 |
| 10:00 | 376 | 27.9% | Rs-201,230 | Rs-535 |
| 11:00 | 456 | 24.1% | Rs-416,513 | Rs-913 |
| 12:00 | 490 | 24.3% | Rs-453,569 | Rs-926 |
| 13:00 | 496 | 25.6% | Rs-242,870 | Rs-490 |
| 14:00 | 462 | 27.7% | Rs-193,654 | Rs-419 |
| 15:00 | 254 | 26.4% | Rs-312,711 | Rs-1,231 |

**#2 EMA 9/21 Crossover + RSI Filter**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 210 | 47.6% | Rs-1,140,238 | Rs-5,430 |
| 10:00 | 153 | 50.3% | Rs-1,007,929 | Rs-6,588 |
| 11:00 | 188 | 58.0% | Rs-54,247 | Rs-289 |
| 12:00 | 186 | 58.1% | Rs50,050 | Rs269 |
| 13:00 | 273 | 52.7% | Rs-197,354 | Rs-723 |
| 14:00 | 252 | 55.2% | Rs-177,035 | Rs-703 |
| 15:00 | 151 | 43.7% | Rs-180,170 | Rs-1,193 |

**#3 Opening Range Breakout**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 167 | 41.9% | Rs43,734 | Rs262 |
| 10:00 | 79 | 44.3% | Rs26,325 | Rs333 |
| 11:00 | 36 | 50.0% | Rs4,579 | Rs127 |
| 12:00 | 19 | 36.8% | Rs-8,486 | Rs-447 |
| 13:00 | 18 | 50.0% | Rs8,743 | Rs486 |
| 14:00 | 16 | 68.8% | Rs3,476 | Rs217 |
| 15:00 | 10 | 60.0% | Rs2,594 | Rs259 |

**#4 Supertrend(10,3) + VWAP**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 88 | 43.2% | Rs22,115 | Rs251 |
| 10:00 | 57 | 43.9% | Rs32,921 | Rs578 |
| 11:00 | 67 | 41.8% | Rs16,248 | Rs243 |
| 12:00 | 67 | 43.3% | Rs33,290 | Rs497 |
| 13:00 | 71 | 39.4% | Rs10,717 | Rs151 |
| 14:00 | 76 | 53.9% | Rs39,156 | Rs515 |
| 15:00 | 33 | 69.7% | Rs12,083 | Rs366 |

**#5 RSI(2) Pullback-in-Trend**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 877 | 51.8% | Rs-140,875 | Rs-161 |
| 10:00 | 1430 | 52.3% | Rs-512,314 | Rs-358 |
| 11:00 | 1355 | 57.3% | Rs-99,856 | Rs-74 |
| 12:00 | 1358 | 56.0% | Rs-270,552 | Rs-199 |
| 13:00 | 1228 | 55.1% | Rs-361,432 | Rs-294 |
| 14:00 | 1171 | 53.3% | Rs-279,712 | Rs-239 |
| 15:00 | 547 | 41.5% | Rs-239,886 | Rs-439 |

**#7 Iron Condor (NIFTY weekly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 63 | 84.1% | Rs30,489 | Rs484 |

**#8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 104 | 56.7% | Rs46,600 | Rs448 |

**#9 Bull Put Credit Spread (NIFTY monthly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 6 | 66.7% | Rs622 | Rs104 |

**#10 Short Straddle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 491 | 60.7% | Rs68,098 | Rs139 |

**#11 Short Strangle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 491 | 59.5% | Rs39,748 | Rs81 |

**#12 Short Straddle Breakeven (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 491 | 55.2% | Rs29,772 | Rs61 |

**#13 Iron Butterfly (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 104 | 37.5% | Rs-48,098 | Rs-462 |

**#14 Bollinger + VWAP Breakout (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 09:00 | 1 | 0.0% | Rs-744 | Rs-744 |
| 10:00 | 5 | 20.0% | Rs-1,093 | Rs-219 |
| 11:00 | 4 | 0.0% | Rs-2,224 | Rs-556 |
| 12:00 | 4 | 50.0% | Rs1,975 | Rs494 |
| 13:00 | 2 | 0.0% | Rs-1,231 | Rs-615 |
| 14:00 | 20 | 60.0% | Rs5,434 | Rs272 |
| 15:00 | 12 | 33.3% | Rs-128 | Rs-11 |

**Top10#7 Bollinger Mean-Reversion (daily)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 15:00 | 1 | 100.0% | Rs33,338 | Rs33,338 |

## Breakdown by month

### By month

**#1 VWAP + Bollinger Mean-Reversion Scalp**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 147 | 33.3% | Rs-18,072 | Rs-123 |
| 2022-02 | 132 | 28.8% | Rs-146,711 | Rs-1,111 |
| 2022-03 | 130 | 22.3% | Rs-108,986 | Rs-838 |
| 2022-04 | 130 | 33.8% | Rs-115,146 | Rs-886 |
| 2022-05 | 124 | 23.4% | Rs-153,339 | Rs-1,237 |
| 2022-06 | 131 | 23.7% | Rs-82,151 | Rs-627 |
| 2022-07 | 148 | 23.0% | Rs-128,846 | Rs-871 |
| 2022-08 | 131 | 31.3% | Rs-112,774 | Rs-861 |
| 2022-09 | 140 | 24.3% | Rs-127,001 | Rs-907 |
| 2022-10 | 115 | 27.8% | Rs-138,476 | Rs-1,204 |
| 2022-11 | 101 | 23.8% | Rs-130,779 | Rs-1,295 |
| 2022-12 | 134 | 24.6% | Rs-155,005 | Rs-1,157 |
| 2023-01 | 132 | 26.5% | Rs-13,026 | Rs-99 |
| 2023-02 | 120 | 27.5% | Rs-30,393 | Rs-253 |
| 2023-03 | 125 | 24.8% | Rs-77,376 | Rs-619 |
| 2023-04 | 100 | 24.0% | Rs-95,852 | Rs-959 |
| 2023-05 | 119 | 28.6% | Rs-111,021 | Rs-933 |
| 2023-06 | 126 | 28.6% | Rs-130,774 | Rs-1,038 |
| 2023-07 | 137 | 35.0% | Rs32,980 | Rs241 |
| 2023-08 | 135 | 25.2% | Rs-72,241 | Rs-535 |
| 2023-09 | 126 | 28.6% | Rs-60,815 | Rs-483 |
| 2023-10 | 128 | 24.2% | Rs-112,201 | Rs-877 |
| 2023-11 | 111 | 30.6% | Rs-43,974 | Rs-396 |
| 2023-12 | 120 | 37.5% | Rs1,518 | Rs13 |

**#2 EMA 9/21 Crossover + RSI Filter**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 55 | 60.0% | Rs-88,123 | Rs-1,602 |
| 2022-02 | 46 | 52.2% | Rs87,507 | Rs1,902 |
| 2022-03 | 65 | 58.5% | Rs-891,603 | Rs-13,717 |
| 2022-04 | 55 | 56.4% | Rs33,343 | Rs606 |
| 2022-05 | 58 | 60.3% | Rs74,770 | Rs1,289 |
| 2022-06 | 70 | 57.1% | Rs-813,780 | Rs-11,625 |
| 2022-07 | 56 | 44.6% | Rs-83,260 | Rs-1,487 |
| 2022-08 | 60 | 56.7% | Rs-13,889 | Rs-231 |
| 2022-09 | 71 | 53.5% | Rs7,634 | Rs108 |
| 2022-10 | 51 | 52.9% | Rs-123,507 | Rs-2,422 |
| 2022-11 | 56 | 44.6% | Rs-132,592 | Rs-2,368 |
| 2022-12 | 65 | 56.9% | Rs42,839 | Rs659 |
| 2023-01 | 66 | 54.5% | Rs-17,892 | Rs-271 |
| 2023-02 | 61 | 47.5% | Rs14,470 | Rs237 |
| 2023-03 | 64 | 59.4% | Rs68,273 | Rs1,067 |
| 2023-04 | 47 | 57.4% | Rs88,770 | Rs1,889 |
| 2023-05 | 49 | 61.2% | Rs101,977 | Rs2,081 |
| 2023-06 | 59 | 47.5% | Rs90,151 | Rs1,528 |
| 2023-07 | 72 | 47.2% | Rs-25,405 | Rs-353 |
| 2023-08 | 66 | 50.0% | Rs22,621 | Rs343 |
| 2023-09 | 52 | 40.4% | Rs-698,226 | Rs-13,427 |
| 2023-10 | 54 | 46.3% | Rs-489,236 | Rs-9,060 |
| 2023-11 | 72 | 41.7% | Rs28,099 | Rs390 |
| 2023-12 | 43 | 58.1% | Rs10,140 | Rs236 |

**#3 Opening Range Breakout**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 13 | 46.2% | Rs4,096 | Rs315 |
| 2022-02 | 7 | 57.1% | Rs127 | Rs18 |
| 2022-03 | 11 | 27.3% | Rs-3,370 | Rs-306 |
| 2022-04 | 18 | 38.9% | Rs-384 | Rs-21 |
| 2022-05 | 9 | 66.7% | Rs16,008 | Rs1,779 |
| 2022-06 | 7 | 71.4% | Rs7,947 | Rs1,135 |
| 2022-07 | 17 | 47.1% | Rs4,144 | Rs244 |
| 2022-08 | 16 | 50.0% | Rs-1,014 | Rs-63 |
| 2022-09 | 5 | 60.0% | Rs6,577 | Rs1,315 |
| 2022-10 | 15 | 20.0% | Rs-6,908 | Rs-461 |
| 2022-11 | 17 | 58.8% | Rs6,620 | Rs389 |
| 2022-12 | 10 | 60.0% | Rs8,683 | Rs868 |
| 2023-01 | 14 | 64.3% | Rs16,499 | Rs1,178 |
| 2023-02 | 21 | 33.3% | Rs-1,728 | Rs-82 |
| 2023-03 | 14 | 64.3% | Rs13,200 | Rs943 |
| 2023-04 | 15 | 33.3% | Rs-2,807 | Rs-187 |
| 2023-05 | 15 | 40.0% | Rs378 | Rs25 |
| 2023-06 | 16 | 31.2% | Rs-1,728 | Rs-108 |
| 2023-07 | 23 | 39.1% | Rs-592 | Rs-26 |
| 2023-08 | 26 | 34.6% | Rs-5,065 | Rs-195 |
| 2023-09 | 17 | 58.8% | Rs7,305 | Rs430 |
| 2023-10 | 8 | 62.5% | Rs7,053 | Rs882 |
| 2023-11 | 14 | 50.0% | Rs1,461 | Rs104 |
| 2023-12 | 17 | 35.3% | Rs4,464 | Rs263 |

**#4 Supertrend(10,3) + VWAP**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 17 | 35.3% | Rs-1,557 | Rs-92 |
| 2022-02 | 9 | 33.3% | Rs131 | Rs15 |
| 2022-03 | 10 | 40.0% | Rs571 | Rs57 |
| 2022-04 | 9 | 55.6% | Rs13,966 | Rs1,552 |
| 2022-05 | 7 | 71.4% | Rs10,623 | Rs1,518 |
| 2022-06 | 19 | 57.9% | Rs13,588 | Rs715 |
| 2022-07 | 23 | 56.5% | Rs17,385 | Rs756 |
| 2022-08 | 20 | 40.0% | Rs15,016 | Rs751 |
| 2022-09 | 16 | 50.0% | Rs1,996 | Rs125 |
| 2022-10 | 21 | 47.6% | Rs1,890 | Rs90 |
| 2022-11 | 24 | 29.2% | Rs-4,284 | Rs-179 |
| 2022-12 | 24 | 45.8% | Rs10,273 | Rs428 |
| 2023-01 | 19 | 42.1% | Rs10,466 | Rs551 |
| 2023-02 | 21 | 47.6% | Rs956 | Rs46 |
| 2023-03 | 16 | 62.5% | Rs13,120 | Rs820 |
| 2023-04 | 21 | 52.4% | Rs-350 | Rs-17 |
| 2023-05 | 23 | 52.2% | Rs4,420 | Rs192 |
| 2023-06 | 26 | 38.5% | Rs2,874 | Rs111 |
| 2023-07 | 28 | 46.4% | Rs9,028 | Rs322 |
| 2023-08 | 26 | 46.2% | Rs24,389 | Rs938 |
| 2023-09 | 24 | 45.8% | Rs9,327 | Rs389 |
| 2023-10 | 19 | 42.1% | Rs7,884 | Rs415 |
| 2023-11 | 25 | 36.0% | Rs-5,880 | Rs-235 |
| 2023-12 | 12 | 58.3% | Rs10,697 | Rs891 |

**#5 RSI(2) Pullback-in-Trend**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 312 | 54.2% | Rs-79,641 | Rs-255 |
| 2022-02 | 338 | 56.5% | Rs-113,992 | Rs-337 |
| 2022-03 | 355 | 53.5% | Rs-178,393 | Rs-503 |
| 2022-04 | 302 | 55.0% | Rs-97,138 | Rs-322 |
| 2022-05 | 349 | 57.0% | Rs66,602 | Rs191 |
| 2022-06 | 364 | 54.9% | Rs-77,121 | Rs-212 |
| 2022-07 | 376 | 57.7% | Rs-61,849 | Rs-164 |
| 2022-08 | 328 | 54.3% | Rs-146,393 | Rs-446 |
| 2022-09 | 367 | 54.8% | Rs-85,398 | Rs-233 |
| 2022-10 | 295 | 55.3% | Rs-72,577 | Rs-246 |
| 2022-11 | 329 | 54.4% | Rs-31,050 | Rs-94 |
| 2022-12 | 359 | 53.2% | Rs-120,765 | Rs-336 |
| 2023-01 | 332 | 53.0% | Rs-94,089 | Rs-283 |
| 2023-02 | 294 | 54.8% | Rs-62,559 | Rs-213 |
| 2023-03 | 344 | 56.4% | Rs-94,303 | Rs-274 |
| 2023-04 | 252 | 45.6% | Rs-183,249 | Rs-727 |
| 2023-05 | 374 | 49.2% | Rs-107,121 | Rs-286 |
| 2023-06 | 359 | 52.4% | Rs-85,454 | Rs-238 |
| 2023-07 | 320 | 51.9% | Rs-76,869 | Rs-240 |
| 2023-08 | 356 | 52.5% | Rs-49,069 | Rs-138 |
| 2023-09 | 320 | 51.9% | Rs24,085 | Rs75 |
| 2023-10 | 338 | 52.4% | Rs-75,089 | Rs-222 |
| 2023-11 | 307 | 47.9% | Rs-121,952 | Rs-397 |
| 2023-12 | 296 | 54.7% | Rs18,757 | Rs63 |

**#7 Iron Condor (NIFTY weekly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 2 | 50.0% | Rs450 | Rs225 |
| 2022-07 | 2 | 100.0% | Rs1,154 | Rs577 |
| 2022-08 | 2 | 100.0% | Rs2,057 | Rs1,029 |
| 2022-10 | 2 | 50.0% | Rs545 | Rs272 |
| 2022-11 | 4 | 100.0% | Rs2,733 | Rs683 |
| 2022-12 | 3 | 100.0% | Rs2,582 | Rs861 |
| 2023-01 | 4 | 100.0% | Rs2,838 | Rs710 |
| 2023-02 | 4 | 100.0% | Rs2,859 | Rs715 |
| 2023-03 | 2 | 50.0% | Rs212 | Rs106 |
| 2023-04 | 4 | 100.0% | Rs1,464 | Rs366 |
| 2023-05 | 5 | 100.0% | Rs2,916 | Rs583 |
| 2023-06 | 4 | 100.0% | Rs2,524 | Rs631 |
| 2023-07 | 5 | 80.0% | Rs2,045 | Rs409 |
| 2023-08 | 4 | 100.0% | Rs3,160 | Rs790 |
| 2023-09 | 4 | 100.0% | Rs1,427 | Rs357 |
| 2023-10 | 5 | 60.0% | Rs1,457 | Rs291 |
| 2023-11 | 4 | 50.0% | Rs-387 | Rs-97 |
| 2023-12 | 3 | 33.3% | Rs453 | Rs151 |

**#8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 5 | 40.0% | Rs-3,917 | Rs-783 |
| 2022-02 | 4 | 50.0% | Rs4,193 | Rs1,048 |
| 2022-03 | 4 | 25.0% | Rs456 | Rs114 |
| 2022-04 | 4 | 100.0% | Rs4,575 | Rs1,144 |
| 2022-05 | 5 | 60.0% | Rs5,773 | Rs1,155 |
| 2022-06 | 4 | 100.0% | Rs8,007 | Rs2,002 |
| 2022-07 | 4 | 25.0% | Rs1,297 | Rs324 |
| 2022-08 | 5 | 20.0% | Rs-1,509 | Rs-302 |
| 2022-09 | 4 | 100.0% | Rs5,228 | Rs1,307 |
| 2022-10 | 5 | 80.0% | Rs3,385 | Rs677 |
| 2022-11 | 4 | 75.0% | Rs2,883 | Rs721 |
| 2022-12 | 4 | 50.0% | Rs1,100 | Rs275 |
| 2023-01 | 5 | 60.0% | Rs3,684 | Rs737 |
| 2023-02 | 4 | 50.0% | Rs297 | Rs74 |
| 2023-03 | 4 | 25.0% | Rs-3,315 | Rs-829 |
| 2023-04 | 4 | 50.0% | Rs1,415 | Rs354 |
| 2023-05 | 5 | 60.0% | Rs-369 | Rs-74 |
| 2023-06 | 4 | 50.0% | Rs1,481 | Rs370 |
| 2023-07 | 5 | 80.0% | Rs4,552 | Rs910 |
| 2023-08 | 4 | 75.0% | Rs3,783 | Rs946 |
| 2023-09 | 4 | 75.0% | Rs1,979 | Rs495 |
| 2023-10 | 5 | 40.0% | Rs-293 | Rs-59 |
| 2023-11 | 4 | 50.0% | Rs1,170 | Rs293 |
| 2023-12 | 4 | 25.0% | Rs745 | Rs186 |

**#9 Bull Put Credit Spread (NIFTY monthly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-03 | 1 | 100.0% | Rs548 | Rs548 |
| 2022-07 | 1 | 0.0% | Rs-477 | Rs-477 |
| 2023-01 | 1 | 100.0% | Rs591 | Rs591 |
| 2023-02 | 1 | 0.0% | Rs-1,421 | Rs-1,421 |
| 2023-04 | 1 | 100.0% | Rs553 | Rs553 |
| 2023-05 | 1 | 100.0% | Rs828 | Rs828 |

**#10 Short Straddle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 20 | 65.0% | Rs8,354 | Rs418 |
| 2022-02 | 20 | 55.0% | Rs6,733 | Rs337 |
| 2022-03 | 21 | 61.9% | Rs3,923 | Rs187 |
| 2022-04 | 19 | 63.2% | Rs5,167 | Rs272 |
| 2022-05 | 21 | 71.4% | Rs8,190 | Rs390 |
| 2022-06 | 22 | 59.1% | Rs6,790 | Rs309 |
| 2022-07 | 21 | 57.1% | Rs-4,869 | Rs-232 |
| 2022-08 | 20 | 60.0% | Rs5,442 | Rs272 |
| 2022-09 | 22 | 50.0% | Rs-4,994 | Rs-227 |
| 2022-10 | 18 | 83.3% | Rs13,448 | Rs747 |
| 2022-11 | 21 | 47.6% | Rs1,282 | Rs61 |
| 2022-12 | 22 | 77.3% | Rs11,491 | Rs522 |
| 2023-01 | 21 | 71.4% | Rs7,479 | Rs356 |
| 2023-02 | 20 | 50.0% | Rs-14,154 | Rs-708 |
| 2023-03 | 21 | 61.9% | Rs4,674 | Rs223 |
| 2023-04 | 17 | 52.9% | Rs2,426 | Rs143 |
| 2023-05 | 22 | 63.6% | Rs-495 | Rs-22 |
| 2023-06 | 21 | 66.7% | Rs4,868 | Rs232 |
| 2023-07 | 21 | 52.4% | Rs-3,484 | Rs-166 |
| 2023-08 | 22 | 77.3% | Rs6,150 | Rs280 |
| 2023-09 | 20 | 65.0% | Rs2,770 | Rs139 |
| 2023-10 | 20 | 40.0% | Rs-4,405 | Rs-220 |
| 2023-11 | 20 | 55.0% | Rs852 | Rs43 |
| 2023-12 | 19 | 47.4% | Rs462 | Rs24 |

**#11 Short Strangle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 20 | 65.0% | Rs10,199 | Rs510 |
| 2022-02 | 20 | 55.0% | Rs7,377 | Rs369 |
| 2022-03 | 21 | 52.4% | Rs-2,089 | Rs-99 |
| 2022-04 | 19 | 57.9% | Rs970 | Rs51 |
| 2022-05 | 21 | 71.4% | Rs7,040 | Rs335 |
| 2022-06 | 22 | 59.1% | Rs3,448 | Rs157 |
| 2022-07 | 21 | 57.1% | Rs-7,013 | Rs-334 |
| 2022-08 | 20 | 65.0% | Rs4,966 | Rs248 |
| 2022-09 | 22 | 63.6% | Rs2,383 | Rs108 |
| 2022-10 | 18 | 77.8% | Rs9,630 | Rs535 |
| 2022-11 | 21 | 38.1% | Rs-500 | Rs-24 |
| 2022-12 | 22 | 68.2% | Rs6,259 | Rs284 |
| 2023-01 | 21 | 71.4% | Rs5,038 | Rs240 |
| 2023-02 | 20 | 60.0% | Rs-8,477 | Rs-424 |
| 2023-03 | 21 | 61.9% | Rs3,957 | Rs188 |
| 2023-04 | 17 | 47.1% | Rs29 | Rs2 |
| 2023-05 | 22 | 59.1% | Rs-3,483 | Rs-158 |
| 2023-06 | 21 | 66.7% | Rs3,712 | Rs177 |
| 2023-07 | 21 | 47.6% | Rs-3,299 | Rs-157 |
| 2023-08 | 22 | 77.3% | Rs3,100 | Rs141 |
| 2023-09 | 20 | 60.0% | Rs-1,044 | Rs-52 |
| 2023-10 | 20 | 50.0% | Rs-1,975 | Rs-99 |
| 2023-11 | 20 | 55.0% | Rs-32 | Rs-2 |
| 2023-12 | 19 | 36.8% | Rs-446 | Rs-23 |

**#12 Short Straddle Breakeven (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 20 | 65.0% | Rs3,681 | Rs184 |
| 2022-02 | 20 | 55.0% | Rs-1,886 | Rs-94 |
| 2022-03 | 21 | 52.4% | Rs789 | Rs38 |
| 2022-04 | 19 | 36.8% | Rs-13,401 | Rs-705 |
| 2022-05 | 21 | 42.9% | Rs300 | Rs14 |
| 2022-06 | 22 | 40.9% | Rs-7,262 | Rs-330 |
| 2022-07 | 21 | 66.7% | Rs9,613 | Rs458 |
| 2022-08 | 20 | 75.0% | Rs9,445 | Rs472 |
| 2022-09 | 22 | 50.0% | Rs-8,117 | Rs-369 |
| 2022-10 | 18 | 55.6% | Rs3,875 | Rs215 |
| 2022-11 | 21 | 38.1% | Rs-7,808 | Rs-372 |
| 2022-12 | 22 | 68.2% | Rs12,469 | Rs567 |
| 2023-01 | 21 | 57.1% | Rs-118 | Rs-6 |
| 2023-02 | 20 | 45.0% | Rs-5,322 | Rs-266 |
| 2023-03 | 21 | 57.1% | Rs3,536 | Rs168 |
| 2023-04 | 17 | 35.3% | Rs-1,248 | Rs-73 |
| 2023-05 | 22 | 68.2% | Rs2,922 | Rs133 |
| 2023-06 | 21 | 71.4% | Rs8,630 | Rs411 |
| 2023-07 | 21 | 52.4% | Rs4,445 | Rs212 |
| 2023-08 | 22 | 63.6% | Rs6,538 | Rs297 |
| 2023-09 | 20 | 65.0% | Rs2,307 | Rs115 |
| 2023-10 | 20 | 60.0% | Rs9,546 | Rs477 |
| 2023-11 | 20 | 55.0% | Rs184 | Rs9 |
| 2023-12 | 19 | 42.1% | Rs-3,346 | Rs-176 |

**#13 Iron Butterfly (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 5 | 0.0% | Rs-12,427 | Rs-2,485 |
| 2022-02 | 4 | 50.0% | Rs2,105 | Rs526 |
| 2022-03 | 4 | 25.0% | Rs2,218 | Rs554 |
| 2022-04 | 4 | 0.0% | Rs-10,289 | Rs-2,572 |
| 2022-05 | 5 | 60.0% | Rs7,083 | Rs1,417 |
| 2022-06 | 4 | 50.0% | Rs3,112 | Rs778 |
| 2022-07 | 4 | 0.0% | Rs-11,175 | Rs-2,794 |
| 2022-08 | 5 | 20.0% | Rs-3,765 | Rs-753 |
| 2022-09 | 4 | 25.0% | Rs1,088 | Rs272 |
| 2022-10 | 5 | 60.0% | Rs6,832 | Rs1,366 |
| 2022-11 | 4 | 25.0% | Rs-4,944 | Rs-1,236 |
| 2022-12 | 4 | 50.0% | Rs1,981 | Rs495 |
| 2023-01 | 5 | 60.0% | Rs-2,075 | Rs-415 |
| 2023-02 | 4 | 25.0% | Rs-7,486 | Rs-1,872 |
| 2023-03 | 4 | 25.0% | Rs-568 | Rs-142 |
| 2023-04 | 4 | 25.0% | Rs-7,352 | Rs-1,838 |
| 2023-05 | 5 | 60.0% | Rs1,465 | Rs293 |
| 2023-06 | 4 | 75.0% | Rs2,504 | Rs626 |
| 2023-07 | 5 | 40.0% | Rs-6,420 | Rs-1,284 |
| 2023-08 | 4 | 100.0% | Rs14,037 | Rs3,509 |
| 2023-09 | 4 | 25.0% | Rs-11,301 | Rs-2,825 |
| 2023-10 | 5 | 40.0% | Rs1,532 | Rs306 |
| 2023-11 | 4 | 50.0% | Rs-1,908 | Rs-477 |
| 2023-12 | 4 | 0.0% | Rs-12,344 | Rs-3,086 |

**#14 Bollinger + VWAP Breakout (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-01 | 1 | 0.0% | Rs-400 | Rs-400 |
| 2022-02 | 1 | 100.0% | Rs731 | Rs731 |
| 2022-07 | 1 | 0.0% | Rs-202 | Rs-202 |
| 2022-08 | 1 | 0.0% | Rs-1,088 | Rs-1,088 |
| 2022-10 | 1 | 0.0% | Rs-282 | Rs-282 |
| 2022-11 | 4 | 25.0% | Rs362 | Rs91 |
| 2023-01 | 3 | 66.7% | Rs-12 | Rs-4 |
| 2023-02 | 2 | 50.0% | Rs1,437 | Rs719 |
| 2023-03 | 1 | 0.0% | Rs-198 | Rs-198 |
| 2023-04 | 4 | 25.0% | Rs2,476 | Rs619 |
| 2023-05 | 1 | 100.0% | Rs886 | Rs886 |
| 2023-06 | 12 | 41.7% | Rs-1,702 | Rs-142 |
| 2023-07 | 3 | 0.0% | Rs-1,915 | Rs-638 |
| 2023-08 | 2 | 50.0% | Rs794 | Rs397 |
| 2023-09 | 1 | 100.0% | Rs905 | Rs905 |
| 2023-10 | 1 | 100.0% | Rs25 | Rs25 |
| 2023-11 | 8 | 50.0% | Rs506 | Rs63 |
| 2023-12 | 1 | 0.0% | Rs-335 | Rs-335 |

**Top10#7 Bollinger Mean-Reversion (daily)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022-06 | 1 | 100.0% | Rs33,338 | Rs33,338 |

## Breakdown by year

### By year

**#1 VWAP + Bollinger Mean-Reversion Scalp**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 1563 | 26.7% | Rs-1,417,288 | Rs-907 |
| 2023 | 1479 | 28.5% | Rs-713,175 | Rs-482 |

**#2 EMA 9/21 Crossover + RSI Filter**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 708 | 54.7% | Rs-1,900,663 | Rs-2,685 |
| 2023 | 705 | 50.5% | Rs-806,260 | Rs-1,144 |

**#3 Opening Range Breakout**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 145 | 47.6% | Rs42,525 | Rs293 |
| 2023 | 200 | 43.5% | Rs38,440 | Rs192 |

**#4 Supertrend(10,3) + VWAP**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 199 | 45.7% | Rs79,599 | Rs400 |
| 2023 | 260 | 46.5% | Rs86,930 | Rs334 |

**#5 RSI(2) Pullback-in-Trend**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 4074 | 55.1% | Rs-997,714 | Rs-245 |
| 2023 | 3892 | 52.0% | Rs-906,913 | Rs-233 |

**#7 Iron Condor (NIFTY weekly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 15 | 86.7% | Rs9,521 | Rs635 |
| 2023 | 48 | 83.3% | Rs20,968 | Rs437 |

**#8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 52 | 59.6% | Rs31,471 | Rs605 |
| 2023 | 52 | 53.8% | Rs15,128 | Rs291 |

**#9 Bull Put Credit Spread (NIFTY monthly)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 2 | 50.0% | Rs71 | Rs35 |
| 2023 | 4 | 75.0% | Rs551 | Rs138 |

**#10 Short Straddle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 247 | 62.3% | Rs60,956 | Rs247 |
| 2023 | 244 | 59.0% | Rs7,142 | Rs29 |

**#11 Short Strangle 9:20 (community)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 247 | 60.7% | Rs42,669 | Rs173 |
| 2023 | 244 | 58.2% | Rs-2,921 | Rs-12 |

**#12 Short Straddle Breakeven (BankNifty spec, NIFTY data)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 247 | 53.8% | Rs1,698 | Rs7 |
| 2023 | 244 | 56.6% | Rs28,075 | Rs115 |

**#13 Iron Butterfly (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 52 | 30.8% | Rs-18,182 | Rs-350 |
| 2023 | 52 | 44.2% | Rs-29,916 | Rs-575 |

**#14 Bollinger + VWAP Breakout (reference)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 9 | 22.2% | Rs-879 | Rs-98 |
| 2023 | 39 | 43.6% | Rs2,867 | Rs74 |

**Top10#7 Bollinger Mean-Reversion (daily)**

| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |
|---|---:|---:|---:|---:|
| 2022 | 1 | 100.0% | Rs33,338 | Rs33,338 |
