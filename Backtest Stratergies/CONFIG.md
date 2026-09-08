# CONFIG — single source of truth

**Every strategy parameter used anywhere in this project — backtest or live — must come from
this file, and only this file.** If a script's own constant ever disagrees with this file, this
file is right and the script has drifted; fix the script. This exists specifically so a live
implementation can't quietly diverge from what was actually backtested — see "Why this file
exists" at the bottom.

Last updated: 2026-09-08, after the full 6-year re-test (`PHASE3_REPORT.md`).

## Shared, every strategy

| Setting | Value |
|---|---|
| Capital | ₹1,00,000, one pool, shared across all strategies |
| Reinvestment | **None.** Every trade is sized off the original ₹1,00,000, never off accumulated profit. |
| Risk per trade | 2% of capital (`--risk 0.02`) |
| Max concurrent positions | 5 (checked over the full 6-year history: never actually needed, peak simultaneous was exactly 5 — see PHASE3_REPORT.md addendum) |
| Max per single strategy concurrently | 2 (also never actually binds with this roster) |
| Underlying | NIFTY (not BankNifty — this archive has no BankNifty data; #8/#12 substitute NIFTY, flagged in their own files) |
| Lot size | 50 (constant for the whole test window) |
| Costs modelled | Brokerage ₹20/order, exchange transaction charge, STT (0.05% pre-2023-04-01, 0.0625% after — options sell-side), 18% GST, stamp duty 0.003% buy-side, ≥1 tick (0.05) slippage per leg on entry and exit |
| Data window tested | 2020-08-03 → 2026-08-27 (full archive) and 2022-01-01 → 2023-12-31 (original) |

## The 5 adopted strategies

### #3 — Opening Range Breakout
`strategies/s03_orb.py` — default config, unchanged from spec.
| Param | Value |
|---|---|
| Timeframe | 5-min |
| Opening range | first 3 candles (9:15–9:30) |
| Minimum range to trade | 40 index points |
| Target | entry ± 1.75 × range size |
| Stop | opposite end of the range |
| Max attempts/day | 2 |
| Execution | real ATM option (CE on long breakout, PE on short) |

### #4 — Supertrend + VWAP  ⭐ largest contributor
`strategies/s04_supertrend_vwap.py` — **improved from spec's (10,3) after the Phase-2 sweep.**
| Param | Value |
|---|---|
| Timeframe | 5-min |
| Supertrend period | **7** (spec was 10) |
| Supertrend multiplier | **2** (spec was 3) |
| Max trades/day | 3 (tested 1/2/5/unlimited on full history — 3 is already ~the natural ceiling) |
| VWAP | session TWAP-proxy (index has no volume) |
| Stop | Supertrend line value, trailing |
| Target | none — holds until Supertrend flips against the position |
| Execution | real ATM option (CE long / PE short) |
| Tested, not adopted | regime filter (gap-day only), daily-trend filter, 3-min timeframe — see PHASE3_REPORT.md Addendum 3 for why each was rejected |

### #7 — Iron Condor (NIFTY weekly)
`strategies/s07_iron_condor.py` — default config; the sweep's alternatives (50% TP, straddle-width wings) all lost to this on 2023 out-of-sample.
| Param | Value |
|---|---|
| Entry | Monday of expiry week, 09:30–09:45 (next trading day if Monday's a holiday) |
| Short strikes | ~0.15 delta OTM (Black-Scholes, from stored IV) |
| Long hedges | 200 points further OTM than the short strikes |
| Take-profit | 70% of net credit |
| Stop | combined MTM loss ≥ 1.5× net credit |
| Time exit | 15:00 on expiry day |

### #10 — Short Straddle 9:20
`strategies/s10_short_straddle_920.py` — default config (25%+5pt beat every tighter SL tested on 2023 OOS).
| Param | Value |
|---|---|
| Entry | 9:20 AM, every trading day |
| Strikes | ATM Call + ATM Put (spot rounded to nearest 50) |
| Per-leg stop | 25% above own entry premium + 5-point buffer, independent per leg |
| Re-entry | once, at 12:30 PM, only if BOTH legs already stopped |
| Time exit | 15:06 |

### #12 — Short Straddle with Breakeven Adjustment
`strategies/s12_straddle_breakeven_banknifty.py` — default config. **The sizing-bug fix (see
PHASE2_REPORT.md §1) matters most here — this strategy's real numbers only became trustworthy
after that correction.**
| Param | Value |
|---|---|
| Entry | 9:59 AM, every trading day |
| Strikes | ATM Call + ATM Put (spot rounded to nearest 100) |
| Call stop | 20% above own entry premium |
| Put stop | 23% above own entry premium |
| Breakeven adjustment | the moment either leg stops, the surviving leg's stop moves to ITS OWN breakeven (entry price) |
| Re-entry | once, at 12:30 PM, only if both legs closed by then |
| Time exit | 15:06 |

## Dropped — do not re-enable without re-reading why

**#8 — Short Strangle Hard-Stop.** Looked fine on 2022-2023 (net +₹46,600). Full 6-year test:
profit factor 1.01 (breakeven), 49.6% max drawdown — the short window wasn't representative.
A parameter fix exists (short strikes at 0.30 delta instead of the spec's 0.20 — turns it into
PF 1.44, 28% drawdown, Sharpe 1.97) but 2026 year-to-date alone lost ₹63,151 even at that fixed
setting, so it stays out of paper trading until that recent stretch is better understood. If
revisited: `strategies/s08_banknifty_strangle.py --target-delta 0.30`.

**Everything else in the original 21** (#1, #2, #5, #6, #9, #11, #13, #14, and the Top-10
daily/swing group) — retired per `REPORT.md`'s original findings; not re-examined since.

## Where each number came from
- `REPORT.md` — the original 21-strategy, 2022-2023 backtest.
- `PHASE2_REPORT.md` — the sizing-bug fix (§1, changes #9/#11/#12's real numbers) and the
  parameter sweep that found #4's (7,2) and #8's 75%-SL improvements (§2).
- `PARAMETER_SWEEP_FINDINGS.md` — the sweep's full grids and per-config numbers.
- `PHASE3_REPORT.md` — the full 6-year re-test, the portfolio backtest (5 concurrent positions,
  fixed capital), the #8 drop decision, the #8 delta re-test, and the 4 Supertrend ideas.

## Why this file exists
This project already hit exactly the failure this file guards against once: `strategy.py` /
`strategy_experimental.py`'s "Candidate B" was independently re-implemented for live trading
away from wherever its backtest numbers came from, and its live-wired parameters didn't match
what was actually validated — the confirmation was retracted after the live version couldn't
reproduce the backtested P&L. See `PROJECT_STATUS.md`'s 2026-08-26 entries. The fix isn't "be
more careful next time," it's structural: **one file of parameters, and a live implementation
that imports the same strategy code this backtest used, rather than a second hand-written copy
of the rule.** See PHASE4 (next step) for how that applies to putting these five live.
