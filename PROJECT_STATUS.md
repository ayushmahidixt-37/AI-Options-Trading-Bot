# Project Status and Session Handoff

> **Read this file first in every new development session.** Keep it updated in
> the same commit whenever scope, safety decisions, completed work, current
> priorities, operating instructions, or known limitations change.

**Last updated:** 2026-08-26

**2026-08-26 (latest) — the short strangle's confirmation is ALSO retracted: it was run with fees and
slippage disabled. Both "confirmed" strategies are now invalid.** Root cause found and reproduced
exactly: passing `settings=None` zeroes costs, and doing so regenerates the claimed +67,980.00 to the
rupee. With real costs the same 526 trades give **+9,593.50** (9/17 quarters, not 12/17) — a strangle
pays four orders per trade, and **real costs consume 86% of the claimed profit**. Unlike Candidate B
this strategy is *not* rejected: it stays net positive after costs, so it is downgraded
Confirmed -> **Open**, with roughly one seventh of the claimed edge. Also confirmed:
`settings=None` does **not** explain Candidate B (cost-free gives +13,710 vs the claimed +24,335), so
the two failures have different mechanisms — Candidate B's remains "two runs' columns merged".
**Answering the original Rs 1,00,000 question on verified numbers, with correct risk-based sizing
(2% of balance per trade):** 2020-2024 -> Rs 31,766 (**-68.2%**); the most current data,
2025-03..2026-08 -> Rs 63,525 (**-36.5%**). Rs 1,00,000 is ample capital (only 25 of 662 recent
trades skipped) — the constraint is that the strategy has no edge, not the account size. Risk-based
sizing is materially less destructive than the naive affordability rule (-68% vs -99%), so that fix
is worth keeping regardless. **Margin note:** a short strangle posts SPAN+exposure margin (~Rs 1.5-2
lakh/lot), not premium — so **Rs 1,00,000 cannot hold even one strangle position**; margin must be
modelled before any capital work on that strategy. All verification now runs from committed scripts
(`research/verify_short_strangle_confirmation.py`, `research/capital_compounding_simulation.py`,
`research/one_month_sizing_analysis.py`).

**2026-08-26 (latest) — Candidate B's confirmation is RETRACTED: it does not reproduce, and the
strategy as configured loses money.** Found while building a Rs 1,00,000 capital-scaling simulation:
replaying the confirmed trade sequence gave a net loss where the log claimed +608,962.50. Investigated
to root cause rather than papered over. The trade counts, entries and capital deployed all reproduce
*exactly* — the divergence is entirely in exit P&L. Ruled out by direct test: settings drift, archive
data changes (re-ran against the same-day backup), quarter-chunking, and engine code changes. The
decisive finding: the documented profit factor pins average win/loss at 1,768/459, which a
trailing-stop config reproduces to within **one rupee** — but that config yields 25 winners, not the
34 the table claims. **The documented P&L is one run's per-trade economics carried by a different
run's win count.** 27 configurations were then swept; none reproduces the table, and the best net any
of them achieves (+7,774) is about a third of what was claimed. Re-running the documented parameter
line — which is exactly what is wired into live paper trading — gives **-149,566 over 2020-2024**.
Independent arithmetic confirms it is structural, not bad luck: a 30% profit cap against a fixed
~Rs 640 stop needs a **40.2%** win rate to break even; the strategy delivers **30.4%**.
**Consequence: Candidate B is enabled for automatic paper entries in a money-losing configuration**
(live trading itself remains disabled — no real money was ever at risk — but the auto-entry toggle
should be reconsidered). Also found: the naive "rolling capital" sizing rule is itself wrong — it
sizes by affordability, so cheap options got the *largest* risk (a premium-29 option took 28 lots and
lost Rs 16,742 in one trade vs Rs 2,390 for a premium-100 option); correct rule is to size by risk.
**Process failure this exposes:** neither this confirmation nor the short strangle's was produced by a
committed script, which is why a wrong number survived two days. Every future confirmation must be.
**The short strangle's own confirmation (+67,980) was produced the same ad-hoc way and has not been
re-verified — treat it as suspect until it is.** Full working:
`research/CANDIDATE_B_REPRODUCTION_INVESTIGATION.md` and BACKTEST_FINDINGS.md's 2026-08-26 entry.
**2026-08-25 (latest, later) — an ML entry filter for the short strangle was rejected on fresh data, and that
same fresh check surfaced a more urgent problem: the already-confirmed manual filter itself lost money on the
most recent 17.5 months.** Built a day-level ML entry filter (`short_strangle_ml_features.py` + a `ml_model`
parameter on `run_short_strangle_backtest`, reusing the project's existing hand-rolled logistic-regression
infrastructure) to let the model decide when to sell the strangle instead of the hand-tuned opening-range
cutoff. Trained on 542 labeled days (2020-08 to 2023-04) -- a real, large sample unlike Candidate B's earlier
68-trade ML attempt -- and looked like a genuine win on both development and validation (net P&L nearly
doubled). **Failed the fresh 2025-03..2026-08 test outright** -- worse than doing nothing at all, the same
overfitting pattern that rejected the IV filter hours earlier. **More importantly: on that same fresh range,
the existing manual filter also lost money** (-4,817.45 net P&L, 65 trades) -- this doesn't overturn the
2020-2024 confirmation, but it's real evidence the strategy's edge may not be holding up on the most current
data. The quarter-by-quarter breakdown (added same day) shows this isn't a uniform decay: four rough quarters
(2025-Q2 through 2026-Q1) followed by the two most recent quarters both strongly positive -- a case for
watching a few more weeks of live paper data before deciding, not for abandoning the strategy.
**Recommendation: do not enable the live "ENABLE AUTO STRANGLE" toggle from a standing start** (it's off by
default and stays that way) until more recent evidence accumulates. Also found and fixed a real, unrelated
performance bug along the way: `market_candles` had no index on
`source`, so every Dhan/Upstox-filtered query in the ~6GB archive was a full table scan (simple counts took
300-400+ seconds); added `market_candles_source_idx`, cutting an unfinished 12+-minute backtest down to under a
minute. See `BACKTEST_FINDINGS.md`'s "Short strangle ML entry filter" 2026-08-25 entry for full numbers.

**2026-08-25 (earlier) — short strangle re-confirmed on fresh data, a real (not assumed) combined-portfolio
check with Candidate B, `MAX_OPEN_POSITIONS` raised 2→5, and live (paper-only) short-strangle execution
built and tested.** The short strangle held up on the same fresh 2020-2024 range that downgraded ORB: 12/17
quarters profitable, +67,980 net P&L. Run together with Candidate B directly from both engines' trade-level
P&L (the user asked explicitly whether this had actually been tested combined or just assumed) -- daily P&L
correlation -0.36, combined max drawdown 14,540 vs Candidate B's own 13,455 alone (naive sum of both would
have been 39,850). This is the first genuinely-tested two-way combination in the project and supersedes the
now-flagged three-way numbers that depended on the since-rejected ORB. `MAX_OPEN_POSITIONS` raised from 2 to
5 in `local-bot.env` to make room for a strangle (2 slots/trade) alongside Candidate B. Built and tested the
same day: `connections.py::create_short_strangle_proposal` (entry gate, opening-range filter, OTM leg
selection matching the backtest's exact query), `paper_monitor.py`'s daily once-per-day auto-entry (with
automatic call-leg rollback if the put leg fails to open -- never leaves a naked single leg) and paired exit
monitoring (evaluates both legs' combined buy-back cost together, matching how the strategy was actually
backtested, never either leg alone), plus a separate "ENABLE/DISABLE AUTO STRANGLE" dashboard toggle
independent of Candidate B's. Foundation layer (SELL-side schema migration, direction-aware fill/P&L/risk
math) was tested against a copy of the real production database first. Full suite: 284 passed, same 4
pre-existing unrelated failures. `LIVE_TRADING_ENABLED` remains `false` throughout -- paper-only. See
`BACKTEST_FINDINGS.md`'s 2026-08-25 "Short strangle re-confirmed..." entry for the full numbers.

**2026-08-25 (earlier) — ORB downgraded to Rejected on fresh data, and the dashboard now
starts itself automatically on trading days.** ORB was run through the identical 17-quarter
fresh-2020-2024-data check that confirmed candidate B: only 10/17 quarters profitable, +0.82%
blended ROI vs candidate B's +5.98% on the same range -- does not reproduce the "profitable in
all 7 quarters" result its original (shorter) screening found. Not added to any live/combined
portfolio; the existing two-/three-way diversification numbers in `research/INDEX.md` are flagged
as needing a fresh re-check since they rest on ORB being solid. Separately: the paper dashboard
had actually been down all morning (killed during yesterday's backfill work and never manually
restarted) -- caught and fixed, and a Windows Scheduled Task (`OptionsBotDailyDashboard`,
`scripts/windows_daily_start.ps1`) now starts it automatically ~09:05 IST every trading day,
skipping weekends and the verified 2026 NSE holiday calendar (now populated in
`local-bot.env`'s `NSE_HOLIDAYS`), with wake-from-sleep enabled so a sleeping machine doesn't
silently skip it.

**2026-08-25 (same day, earlier) — a fine-tuning pass on Candidate B, both items resolved "no change needed."**
A single-year parameter re-sweep (24 combinations of indicator periods) found the current live
config already sits at a local optimum -- nothing beat it. A DhanHQ implied-volatility entry
filter looked like a genuine capital-efficiency win in screening (band 10-20% IV: +9.53%/+4.54%
ROI vs baseline across two splits) but **failed a fresh, never-touched 18-month confirmation
test** (3.27%/3.95% vs baseline's 4.02%) -- rejected, exactly the failure mode this project's
discipline exists to catch. Along the way: backfilled real historical IV onto the entire
2020-2024 archive (16.2M values, zero warnings) via a new additive UPDATE-based path, plus a
second fresh Dhan backfill for 2025-03..2026-08 (5.7M candles, zero failures) specifically to
get a genuinely held-out test range. Found and fixed a real bug in the backtest engine along the
way: reading Dhan-reconstructed and real-Upstox option data for the same strike/expiry
simultaneously creates an arbitrary SQL tie-break; `run_upstox_backtest` now has a scoped
`dhan_only` mode that fixes this without affecting the (shared-token) underlying series. See
`BACKTEST_FINDINGS.md`'s final 2026-08-25 entry for the full tables.

**2026-08-24 — DhanHQ historical backfill (Aug 2020-Oct 2024), a real credentials/dashboard-performance
bug fix, Candidate B wired into live paper trading, and Candidate B CONFIRMED on the fresh data.** Backfilled
~4 years of previously-unavailable NIFTY underlying + option data via DhanHQ (reconstructed from their
ATM-relative rolling feed, validated against real overlapping Upstox data before trusting it — see
`dhan_ingest.py`'s module docstring), extending the archive from ~19 months to ~6 years. Found and fixed a real
bug along the way: the new `DHAN_*` credential names weren't in `credentials.py`'s strict allowlist, which was
silently breaking Angel One connectivity too (the loader raises on the first unknown name, discarding everything
already parsed) — not just a Dhan-specific issue. Also found the dashboard was re-scanning the whole 6+ GB archive
(including a full `PRAGMA quick_check`) on every ~15s monitor tick and every page load; both are now cached/deferred
appropriately, with explicit user-triggered actions (the "Verify database" button, CSV export) still running a
real check. Wired Candidate B (`TrendConfirmedMomentumStrategy` + its proven exit/entry-filter shell) into live
paper trading, replacing the plain, unfiltered baseline strategy that had been live this whole time — added a
genuine profit-target exit (previously stop-only, no target at all), the proven premium/OI entry filters, and
caught a real math bug where naively re-deriving the stop budget from a raised risk cap would have had every
Candidate B trade rejected by the risk engine (fixed with a fixed, proven rupee budget instead — see
`connections.py`'s `CANDIDATE_B_*` constants). Then confirmed Candidate B on the fresh 2020-2024 data — unchanged
parameters, 17 quarters, 15/17 profitable, +593,543.75 net P&L, holding up through the entire 2022 bear market.
See `BACKTEST_FINDINGS.md`'s final 2026-08-24 entry for the full table and caveats.

**2026-08-23 — two things done: tighter short-strangle stop/target tested (doesn't help), and the paper
dashboard is live for the first time.** Swept tighter stop/target settings against the strangle's already-adopted
selective config — validation was completely unchanged across every setting (same 2 trades, same P&L, never once
tripped differently), development got flat-to-worse. Not adopted; entry selectivity remains the real lever for
this strategy, not exit tuning. Separately, actually started `options-bot web` (not just a boot test) on this
Windows machine, paper mode, live disabled, confirmed responding and requiring login. Paper account is at 0/100
real trades — that's the actual next milestone now, not more backtesting; markets were closed (Sunday) when this
was started, so it's ready to catch the next trading session. See `BACKTEST_FINDINGS.md`'s final 2026-08-23 entry.

**2026-08-23 (same day, earlier) — closed out the "build on what worked" batch: the flip-side opening-range filter was
tested and rejected for candidate B/ORB, and the three-way compounding demo landed on a month the strangle
happened to sit out entirely.** `minimum_opening_range_pct` (require a *wide* open, the opposite of what helps
the strangle) was added to `BacktestParameters` and swept against both candidates — cleanly rejected, monotonic
degradation on both strategies and both splits, no ambiguity. Extending the Rs 1,00,000 compounding simulation to
all three strategies found the selective strangle recorded zero trades in April 2026 specifically (both its
real validation-period trades landed in May) — not a bug, just why the full 19-month three-way portfolio check
(documented in the prior entry) is the number to trust for this strategy, not any one month's curve. This closes
out the four-item "on top of existing best" list from earlier today. See `BACKTEST_FINDINGS.md`'s two final
2026-08-23 entries.

**2026-08-23 (same day, earlier) — a real data bug was found and fixed in the short-strangle engine, and the corrected
numbers are actually better.** Building the three-way portfolio check (candidate B + ORB + short strangle) turned
up a tell: every single trade in the loss post-mortem held for an identical 335 minutes. Traced to
`short_premium_backtest.py` never filtering its option-leg queries by `timeframe`, silently mixing `ONE_MINUTE`
and `FIVE_MINUTE` candles for the same contract. Fixed, regression-tested, and every short-strangle result from
today was re-run. Corrected: baseline is now 4/7 quarters profitable (was 3/7), and **selective net P&L
(+14,285.15) now beats baseline's (+7,953.75) outright** on 30% fewer trades — no longer just a risk trade, a
real return improvement too, plus the already-known ~58% worst-quarter drawdown cut. The three-way portfolio
result is the headline: candidate B, ORB, and the selective short strangle are all pairwise near-zero correlated,
and **adding the strangle to the combined B+ORB book increases total profit by 3.5% with zero increase in the
portfolio's own max drawdown.** Also found: the strangle's stop/target never actually fires at current widths —
every trade rides to force-exit — a real, untested next lever. See `BACKTEST_FINDINGS.md`'s "data-integrity bug"
entry for full corrected figures.

**2026-08-23 (same day, earlier) — short strangle kept as a selective tool, not discarded: a real risk improvement, not a
return improvement.** Per user correction, added `maximum_opening_range_pct` so the strategy only deploys on
days whose first 30 minutes look calm (same-day, no lookahead). 7-quarter check vs. the unconditional version:
total net P&L is essentially a wash (+13,123.10 selective vs. +13,241.40 baseline, on 30% fewer trades), but the
worst quarter's drawdown falls 59.8% (18,002.50 → 7,243.55) and one more quarter turns profitable (4/7 vs 3/7).
**Labeled Open** — a genuine consistency/risk improvement, not yet a confirmed return edge. The engine and filter
are real, tested, and available; this is exactly what was asked for — a tool used selectively, not deployed
blindly and not thrown away. See `BACKTEST_FINDINGS.md`'s "selective deployment" entry.

**2026-08-23 (same day, earlier) — the short strangle's 7-quarter check is in, and it settles the dev/val question: rejected
as currently configured.** The striking split (every combo lost on dev, won on val) was not a stable regime
effect as hypothesized — across all 7 quarters the strategy is profitable in only 3 of 7 (2024 Q4, 2025 Q1, 2026
Q2), losing in the other 4, with profit factor swinging from 6.68 down to 0.32 quarter to quarter. Total is a thin
+13,241.40 over 90 trades. Unlike candidate B and ORB (both profitable in literally every quarter tested), this
specific config does not show a stable edge. **Rejected, not confirmed** — the underlying idea isn't disproven,
but a fixed daily entry with zero market-condition awareness is too blunt; adding entry selectivity (a volatility
or range-bound filter) is the natural next step if this gets revisited. See `BACKTEST_FINDINGS.md`'s 2026-08-23
"7-quarter check" follow-up under the short strangle entry.

**2026-08-23 (same day, earlier) — three direct user requests actioned: dynamic exits, a short-premium strategy, and a
compounding simulation.** (1) "Follow-up instead of a hard sell": added `trailing_activation_return` (trailing
stop only arms after real profit exists, not from candle one) and swept it against candidate B — every variant
underperformed the current hard target; option premiums move too fast in % terms for a 10-20% trail width not to
clip winners early. **Not adopted**, though "no target, no trailing" (already known from the earlier exit
re-sweep) remains a small real edge over the hard cap. (2) A new short-strangle (non-directional, sell premium)
engine was built from scratch (`short_premium_backtest.py`) and backtested for the first time — every parameter
combination lost money on development and made money on validation, a striking but not-yet-trustworthy pattern
(needs the same 7-quarter check that resolved every other dev/val conflict this session). **Labeled Open**, next
thing to verify, not adopted. (3) A Rs 1,00,000 compounding-month simulation (April 2026): with a realistic 5%
of current balance risked per trade, candidate B alone ends the month at ~+17%, ORB alone at ~+2.5%, combined at
~+20% — a naive 100%-of-balance sizing was tried first and produced an absurd +1465%, which was caught and
rejected rather than reported. See `BACKTEST_FINDINGS.md`'s three 2026-08-23 entries after the cross-confirmation
one for full detail, caveats, and the sensitivity table across sizing assumptions.

**2026-08-23 (same day, earlier) — three more "next step" ideas tested, all with a clear answer.** (1) EMA-separation
magnitude (trend strength, not just direction) as a candidate-B filter: cleanly rejected, monotonic degradation at
every threshold on both dev and val. (2) Real 15-minute multi-timeframe confirmation (the candle resampler was
generalized to support this — `source_bucket_minutes`, previously 1-minute-only) vs. the existing same-timeframe
EMA proxy: nearly identical results (same trade count on both splits, P&L within a few percent) — retroactively
validates the original architectural shortcut rather than changing anything. (3) Gating ORB's entries with
candidate B's macro-trend agreement: looked promising on one dev/val split (dev improved on every metric) but the
7-quarter check exposed it as overfit to that one window — worse in 6 of 7 quarters, total P&L down 22.7%.
**Rejected.** Net effect: candidate B and ORB's recommendations are unchanged after this whole round, but three
plausible ideas are now ruled out with real evidence instead of left untested, and the codebase gained a properly
generalized candle resampler as reusable infrastructure. See `BACKTEST_FINDINGS.md`'s three 2026-08-23 entries
following the exit re-sweep.

**2026-08-23 (same day, earlier) — ORB's loss post-mortem found a striking time-of-day pattern; the 7-quarter check rejects
it as a general rule.** Ran ORB through the same premium/OI-floor checks and per-trade post-mortem that found
candidate B's free filters. Neither transfers — `minimum_open_interest=100000` actually **nearly halves** ORB's
validation net P&L (one excluded trade turns out to be one of its best, not worst), and premium floors are a wash;
filters don't automatically port between strategies, even on the same underlying. The post-mortem then found
something that looked much cleaner: winners averaged 210 minutes since session open, losers 96 minutes (right as
the opening range forms), every loser hit its stop and every winner hit its target. But it showed up as a "weak
development, strong validation" split — this project's own distrusted shape — and the 7-quarter check confirmed
the suspicion was right: applying `entry_start>=10:45` across all 7 quarters cuts total trades in half (298→143)
and total P&L by 59% (+182,937.85→+75,536.45), with win rate flat-to-worse in 2 of the other 6 quarters. **The
dramatic validation number was a fluke of that one window, not a real pattern — rejected.** A clean demonstration
of why this project checks striking single-split results against more history before trusting them. Also
re-swept both candidates' exit shells now that entry filters exist: candidate B's current pick is confirmed
near-optimal (a marginal alternative exists, not adopted, real win-rate cost); ORB's confirmed correct *and now
for an understood reason* — removing its target cap looks great on development and collapses on validation,
unlike candidate B where the same change helps both together. See `BACKTEST_FINDINGS.md`'s 2026-08-23 entries.

**2026-08-22 (same day, earlier) — tested whether a learned model beats the simple hard OI filter on candidate B; it doesn't.**
Trained a fresh OI-aware ML model (precontract + postcontract features, same architecture as ML v6) directly on
candidate B's own signal for the first time. Best threshold found barely improves on doing nothing (net P&L
+56,657.60 vs +56,508.70 unfiltered, ROI 4.63% vs 4.55%) and clearly underperforms the plain hard
`minimum_open_interest=100000` cutoff already adopted (+58,586.50, ROI 5.05%, higher win rate too). **Not
adopted** — candidate B's recommendation is unchanged. Useful general lesson: a well-targeted hand-found threshold
beat a 10-feature learned model here, because the underlying relationship (OI, premium) is a clean, roughly
monotonic effect that a direct sweep finds exactly, while the model has to split attention across several
lower-signal features too. See `BACKTEST_FINDINGS.md`'s 2026-08-22 "OI-aware ML" entry. Also: the backtest run
ledger (every run's invested capital / true ROI %, not just win rate) now has a durable home at
`research/roi_all_runs.json` / `research/roi_ledger.py` / `research/build_roi_ledger_html.py` — see that module's
docstring for how to keep it current going forward.

**2026-08-22 (same day, earlier) — candidate B and opening-range breakout are nearly
uncorrelated: a genuine diversification benefit, not just two profitable
strategies.** With both independently validated across all 7 quarters,
checked whether running them together helps. Daily P&L correlation:
0.068 (essentially zero) across 324 active trading days (155 both fired,
80/89 only one did). Combined max drawdown (13,909.25, on a simplified
same-day-netted equity curve) is well below the naive sum of their
individual drawdowns (18,659.90), while combined net P&L nearly doubles
candidate B alone (+407,834.40 vs +226,792.45) — a better P&L-to-drawdown
ratio than either strategy alone (29.3x vs 20.4x/24.1x). **Working plan:
run both as independent parallel signal sources once either gets its
formal test confirmation** — this doesn't replace that confirmation for
either one. See `BACKTEST_FINDINGS.md`'s 2026-08-22 "nearly uncorrelated"
entry.

**2026-08-22 (same day, earlier) — opening-range breakout holds up across all 7
quarters too: a second real, independently-generalizing candidate.** Ran
the same fixed parameters (`opening_range_bars=6` + candidate B's exit
shell) unchanged across all 7 quarters of history, the identical check
that built confidence in candidate B. Every single quarter is net
profitable (298 trades total, +182,937.85 combined, win rate a sane
38.8%-56.4% throughout). Notably profitable in 2025 Q4 (+24,171.00, 56.4%
win rate) — the same quarter where plain Baseline and the ML-filtered
model both had their one loss — suggesting this session-open-breakout
signal isn't riding the exact same edge as the trend-following family.
**Both candidate B and this candidate are now labeled Open with strong
generalization evidence, both blocked by the same constraint: no fresh
test range exists for either.** See `BACKTEST_FINDINGS.md`'s 2026-08-22
"Opening-range breakout across all 7 quarters" entry.

**2026-08-22 (same day, earlier) — a proper dev/val split found a second real
candidate: opening-range breakout, once given the right exit shell.**
`OpeningRangeBreakoutStrategy` had only ever been screened with the
default (trend-tuned) exit shell and looked "real but unremarkable" — one
of the six variants tested here even loses money on validation
(-4,228.80) with that shell. Swapping in candidate B's exit shell
(`stop_risk_fraction=1.6, target_return=0.30`) turns every variant
solidly profitable, roughly tripling win rate in each case. Best result:
`opening_range_bars=6` (30-minute opening range) + that exit shell — dev
49t/38.8%/+29,517.55/PF2.52, val 35t/42.9%/+22,943.80/PF2.75, win rate and
profit factor improving together dev→val, the generalization shape this
project trusts. **Labeled Open, a genuine second candidate** (not just
candidate B with different numbers — fires on session-open breakouts, not
mid-day trend confirmation) — same test-range constraint as candidate B.
See `BACKTEST_FINDINGS.md`'s 2026-08-22 "Opening-range breakout" entry.

**2026-08-22 (same day, earlier) — added a small, free open-interest filter to
candidate B; tested and rejected signal confidence as a filter.** Two more
already-captured-but-unused data fields (per-signal confidence, per-contract
open interest) were wired up as new `BacktestParameters` fields and swept
against candidate B. `minimum_open_interest=100000` is a genuinely free
win — development is byte-identical (no dev trade even had OI that low),
validation improves on every metric (76 vs 78 trades, win rate 44.9%→46.1%,
net P&L +57,125.90→+58,586.50, PF 3.00→3.17, same drawdown) by dropping 2
trades that were both net losers. **Added to the candidate B
recommendation.** Confidence filtering shows the opposite shape — every
threshold that raises win rate also shrinks total profit, with no free
tier — **not adopted**. See `BACKTEST_FINDINGS.md`'s 2026-08-22 "Candidate
B: signal confidence and open interest" entry.

**2026-08-22 (same day, earlier) — mean-reversion's wider-stop hypothesis was tested
and refuted.** The alternative-strategies screening pass had rejected
`MeanReversionStrategy` (0% win rate) but flagged that its tight,
trend-tuned exit stop was untested for a reversion setup. Ran 9 exit-shell
variants (stop_risk_fraction 0.8 through uncapped, plus target/trailing
combinations) against the same Development/Validation split as every other
candidate. Every variant lost money on both splits; widening the stop
raised the win rate but made net P&L *worse*, not better (the original
tight 0.8 stop was the least-bad of all nine on validation). **Stays
Rejected** — the specific fix proposed for it has now also failed; any
future revisit needs to start from the entry signal itself, not the exit
shell. See `BACKTEST_FINDINGS.md`'s 2026-08-22 "Mean-reversion re-test"
entry.

**2026-08-22 (same day, earlier) — added the macro-event feature to the ML filter too
(v7): negligible effect.** Same `is_macro_event_window` flag that clearly
helped candidate B's raw strategy performance, added as an 8th ML feature
and retrained (same hyperparameters as v4) on the original `MomentumStrategy`
base signal. Result: 83t/43.4%/+38,707.05/PF2.57 vs. v4's
84t/42.9%/+38,175.00/PF2.52 — within noise, not a meaningful improvement.
Likely explanation: the model already has `atr_normalized`, and scheduled
macro events largely show up as elevated ATR anyway, making the explicit
flag partly redundant. v4 remains the best ML candidate. See
`BACKTEST_FINDINGS.md`'s 2026-08-22 "v7" entry.

**2026-08-22 (same day, earlier) — a known-event calendar was built and tested, and
came back the opposite of the naive hypothesis.** New
`src/options_bot/market_events.py` (verified RBI MPC / FOMC / Union Budget
dates) and `BacktestParameters.exclude_macro_event_days`. Cross-referencing
candidate B's actual trades against it and honestly re-running the
backtest both point the same way: macro-event-window trades average more
than double the P&L of ordinary days (₹696.79 vs. ₹341.76 across 825
trades) for this directional, target-driven strategy — avoiding them makes
every metric worse. **Not added to the candidate B recommendation.** Also
added `research/INDEX.md` — a short current-state pointer file (best
candidate, data coverage, what infrastructure exists) so future sessions
don't need to read the entire chronological `BACKTEST_FINDINGS.md` just to
get oriented; read it first for backtest/strategy work. See
`BACKTEST_FINDINGS.md`'s 2026-08-22 "known-event calendar" entry for the
full analysis.

**2026-08-22 (same day, earlier) — a per-trade loss post-mortem found and validated a
small, real improvement to candidate B.** Pulled the exact signal-time
conditions for all 92 validation trades; RSI/ATR/confidence/time-of-day
didn't discriminate winners from losers, but entry premium did — cheap
options (under ~₹20) had a structural problem: the points-based stop
distance can exceed the entire premium, so the stop mathematically can't
fire, and the flat per-order fee can exceed the whole gross gain on a tiny
move. Added `BacktestParameters.minimum_option_premium` and honestly
re-ran the full backtest (not just summed the affected bucket by hand):
skipping trades under ₹20 gives win rate 44.9% (was 43.5%), net P&L
+57,125.90 (was +56,508.70), profit factor 3.00 (was 2.86), and identical
drawdown — same or better on every metric, for free, though a modest
improvement, not the dramatic "problem solved" a naive read of the
₹20-100 bucket's 66.7% win rate would have suggested. See
`BACKTEST_FINDINGS.md`'s 2026-08-22 "loss post-mortem" entry for the full
analysis and the updated candidate B recommendation (now includes
`minimum_option_premium=20`).

**2026-08-22 (same day, earlier) — candidate B is profitable in all 7 quarters of the
extended history, the strongest generalization evidence this project has
produced (at the time).** Ran the winning fixed entry+exit combination
(`fast_period=5, slow_period=10, macro_period=60, rsi_period=21` /
`stop_risk_fraction=1.6, target_return=0.30`) unchanged across every
quarter from Oct 2024 through May 2026 — not just the two it was picked
on. Every single quarter is net profitable (1,001 trades total,
+304,282.05 combined, win rate a sane 33-49% throughout, no collapses).
Most notably, **it's profitable in 2025 Q4 (+26,884.25)** — the one
quarter where both plain Baseline and the ML-filtered model lost earlier
this session. Two honest caveats: the first three quarters draw on a
real+derived data mix with some archive gaps (later quarters are clean),
and this is still exploratory screening, not the formal one-shot test —
every range here has already been touched today. See
`BACKTEST_FINDINGS.md`'s 2026-08-22 "strongest generalization evidence"
entry for the full per-quarter table. Still Open; the clear frontrunner
for whenever a fresh test range becomes available.

**2026-08-22 (same day, earlier) — pushed the trend-confirmed momentum sweep further:
entry refinement plus, for the first time, an exit-shell sweep on top of
it.** `fast_period=5, slow_period=10` (down from 13) found a further small
entry improvement; combining it with the exit shell (`stop_risk_fraction=1.6,
target_return=0.30`) rather than the plain tight-stop default gives this
project's best-evidenced candidate yet: **92 validation trades, 43.5% win
rate, +56,508.70 net P&L, 3,370.70 drawdown, profit factor 2.86** — within
2% of the single best net-P&L combination found, but meaningfully lower
drawdown and the highest win rate of any variant tried. All roughly triple
Baseline's win rate and net P&L at half its drawdown. See
`BACKTEST_FINDINGS.md`'s 2026-08-22 "round 2" entry for the full stage-by-
stage tables and three final candidate options (best net P&L / best
balanced / best risk-adjusted). Still Open — same test-range constraint as
every trend-confirmed entry.

**2026-08-22 (same day, earlier) — a parameter sweep on trend-confirmed momentum
produced the best-evidenced candidate of the entire session (at the time).** Fixed the
76-minute-per-run cost first (gave the strategy its own precomputed-series
fast path, ~9x faster, proven byte-identical before trusting it), which
made an 8-combination sweep practical. Two independent levers — faster
EMAs (`fast_period=5, slow_period=13` instead of 9/21) and a wider RSI
period (21 instead of 14) — each beat the default on every metric on both
Development and Validation; combined, they compound rather than showing
diminishing returns: **86 validation trades, 26.7% win rate, +45,154.00 net
P&L, 2,844.60 drawdown, 3.19 profit factor** — more than double Baseline's
net P&L at less than half its drawdown, with dev and validation improving
together (no overfitting red flag). See `BACKTEST_FINDINGS.md`'s
2026-08-22 "best result of the whole session" entry for the full sweep
table. Still labeled Open — genuinely test-eligible, but no fresh
NIFTY/FIVE_MINUTE range exists past 2026-08-20 to spend that one-shot
confirmation on yet.

**2026-08-22 (same day, earlier) — trend-confirmed momentum holds up on a proper
Development/Validation split, and was (at the time) this project's strongest
un-tested candidate.** Follow-up to the screening pass below: on the
out-of-sample validation range it beats Baseline on net P&L (+20,590.15
vs. +19,716.75) and profit factor (2.06 vs. 1.81) with fewer trades — dev
and validation improve together, none of the "strong val, weak dev" red
flags this project's discipline distrusts. **It is genuinely eligible for
this project's one-shot test-range confirmation, but no fresh test range
currently exists** — every range from 2024-10-03 through 2026-08-20 has
already been touched by something today. Stacking the ML filter on top
(retrained on this strategy's own signals) gave only a modest further
lift (+7% net P&L, identical drawdown) — the two leads don't compound
much, echoing an existing finding that stacked quality filters show
diminishing returns. Building this candidate's own signal (no fast path
exists for `TrendConfirmedMomentumStrategy` yet) took 76 minutes for one
18-month backtest — worth a fast-path investment if this strategy gets
used again. See `BACKTEST_FINDINGS.md`'s 2026-08-22 entry for full tables.

**2026-08-22 (same day, earlier) — three alternative strategies checked against real
data, one clean rejection and one genuinely interesting result.** New
`src/options_bot/strategy_experimental.py` (backtest-only, never in the
live path): `MeanReversionStrategy` (Bollinger Bands + RSI, fades extremes
instead of following trend), `OpeningRangeBreakoutStrategy` (session
opening-range breaks), `TrendConfirmedMomentumStrategy` (existing momentum
rule + a slower macro-trend EMA agreement gate). First screening pass
(Jan-Mar 2026, single range, not yet the full dev/val/test discipline):
**mean-reversion lost on every one of 32 trades — a clean rejection**
(likely needs reversion-appropriate stop distances, not trend-tuned ones);
opening-range breakout is real but weaker than Baseline on every metric;
**trend-confirmed momentum matches Baseline's win rate and profit factor
at 37% lower drawdown** by being more selective — the strongest of the
three, worth a proper validation pass. See `BACKTEST_FINDINGS.md`'s
2026-08-22 "Three alternative strategies" entry for the full table.
Also added `indicators.bollinger_bands()` (new, tested) and confirmed
`candle_resample.resample_candles()` isn't safely reusable for
5-minute→15-minute aggregation (it assumes a 1-minute source granularity)
— the multi-timeframe strategy uses differently-scaled EMAs on the same
series instead of literal resampling for this reason.

**2026-08-22 (earlier same day) — real Upstox coverage extended back to the platform's
actual hard ceiling (2024-10-03), plus three more bugs found and fixed along
the way.** Built mostly from a live Upstox re-pull, with a small remaining
gap (438 option tokens + the underlying's pre-2026 span) filled by locally
resampling from already-archived real 1-minute data instead of more live API
calls — faster and, unlike a live pull, fully reproducible. See
`BACKTEST_FINDINGS.md`'s 2026-08-22 entry for the full real-vs-derived data
table and an out-of-sample 15-month check (Baseline: 848t/17.7% win/
+55,458.50; Strict RSI55+ATR20: 323t/31.6% win/+40,565.50 — both net
profitable, RSI55/ATR20's win-rate edge over Baseline holds up across very
different market regimes, though quarter-by-quarter performance is uneven).
Also fixed: the underlying NIFTY index's `ONE_MINUTE`-tagged rows were
actually mislabeled 5-minute data (unrelated pre-existing bug); a per-signal
query was re-scanning the whole `market_candles` table every observation,
turning a 15-month backtest into a 12+-hour hang (now under 9 minutes); and
`gap_summary()` wasn't scoped to the backtest's date range, so every prior
Upstox `DATA QUALITY WARNING`/gap count in `BACKTEST_FINDINGS.md` reflects
the whole archive at the time, not the range actually tested. Nothing here
changes the paper-only safety boundary.

**2026-08-22 (later same day) — ML signal-quality filter retrained on the
extended history, plus a real O(n²) performance bug found and fixed in
signal generation itself.** The two prior ML attempts were likely limited
by thin training data (68/155 raw signals); retraining on the newly
extended history gave ~15x more (1,012 raw signals, 449/48 trades on
dev/validation after filtering) — the best result any ML attempt has
produced (validation win rate 43.75%, profit factor 2.17, both up
substantially from v1/v2). See `BACKTEST_FINDINGS.md`'s 2026-08-22 "v3:
retrained on the extended history" entry for the full numbers and honest
caveats (still Open, not Confirmed — no test-range attempt is appropriate
here). Getting there required two more fixes: `train_signal_quality_model.py`
gained an `--include-derived` flag (without it, the script silently
couldn't use any of the newly-extended archive at all); and
`generate_signals_from_candles` had a real quadratic-time bug — it
recomputed EMA/RSI/ATR from scratch over an ever-growing candle window on
every single step, which made an 18-month training run hang for 30+
minutes instead of finishing. Fixed by precomputing each indicator series
once per backtest call (`MomentumStrategy.signal_from_indicators` split out
so the decision rule and the indicator computation are decoupled) — proven
byte-identical to the old behavior by a new regression test comparing the
fast path against a naive full-recompute reference on 400 candles with
multiple signal flips in both directions, before being trusted for
anything. The same run now completes in about 3 minutes. Nothing here
changes the paper-only safety boundary or wires the ML filter into any
live/forward-paper path.

**2026-08-21 — data-integrity incident found and fixed; every prior
backtest number is now a frozen, non-reproducible snapshot.** A research
script that derives coarser candles from 1-minute data silently mixed
derived bars into the same archive rows real Upstox pulls use, and — while
investigating the cleanup — a deeper problem surfaced: the near-ATM
contract-discovery logic (`upstox_ingest.py`) turns out not to be stable
across invocation time, so even a fresh, byte-identical re-pull of the same
date range no longer reproduces the original 2026-08-12 dataset. See
`BACKTEST_FINDINGS.md`'s 2026-08-21 "Data-integrity incident" entry for the
full account, the fixes (a new `derived_from_timeframe` archive column, the
backtest engines now filter on it, regression tests added), and the new
official Baseline reference (`baseline-2026-08-21-repull`: 157t/+69,049.15
dev, 92t/+19,716.75 val — win rate 21.0%/19.6%, both notably higher than the
old archive's 8.8%/12.8%, reflecting a materially different real contract
universe, not a strategy change). Every dev/validation/test number recorded
before this date should be read as historical color, not something to
reproduce or regression-test against. Nothing here changes the paper-only
safety boundary; this is entirely within the read-only historical-backtest
feature.

**Current phase:** Forward paper evidence collection and validation review;
Upstox historical-backtesting feature complete and merged to `main`. Two
real-device (Termux) findings have been fixed: a Cloudflare bot-block (HTTP
403 error 1010, missing User-Agent) and a contract field-name mismatch
(`KeyError: 'expired_instrument_key'`) caused by Upstox's own inconsistent
documentation. Ingestion now tracks archive coverage and skips already-
fetched date ranges automatically, and the deep-analysis view now surfaces
plain-language best/worst highlights (even from a small sample, clearly
marked preliminary) alongside the existing gated Suggestions. The Highlights
card now links to a plain-text "Copy analysis for Claude" export so the full
breakdown can be pasted directly into a chat for tuning discussion.

A real 7-month Upstox archive (Jan-Jul 2026) has now been pulled and
analyzed end-to-end through the development/validation/test-range
discipline. **See `BACKTEST_FINDINGS.md` for the detailed, dated log of
every backtest round, what was confirmed, what was rejected, and why** —
this file only carries the headline. Current headline: "Morning entries"
(09:30-12:00 entry window) is the most promising lead so far (+1,064.90
net P&L, 5,994 drawdown, 31 trades on the Jun-Jul range) but is labeled
**Exploratory, not Confirmed** — that Jun-Jul range had already been
touched by an earlier full-range pass before the split ran, and was
reused again afterward to compare morning-window variants, so it doesn't
meet this project's bar for a clean, untouched confirmation. A genuinely
fresh, never-yet-analyzed period is needed before treating it as more
than a lead. Three ideas are rejected with real confidence, on grounds
unaffected by that caveat (repeated across independent sample sizes, or
a plain development-vs-validation reversal, not a test-range claim):
tighter stop-loss, 10% trailing stop, and removing the stop-loss cap
entirely (the last one is a clean textbook overfitting example —
best-looking backward result of anything tried, worst-performing forward
result of anything tried).

**New infrastructure: a machine-readable range-usage ledger and a
non-interactive backtest CLI**, built to make the anti-overfitting
discipline above structurally enforced rather than prose-only. See
`src/options_bot/research_ledger.py`, `src/options_bot/backtest_cli.py`
(`options-bot backtest check-range/run/validate-split/ledger`), and
`research/` (role prompt templates for a planned 5-role automated
research loop — idea, validate, run, evaluate, digest). The ledger
mechanically refuses to let a "confirmed" label come from a reused date
range or a candidate's second test attempt; see the module docstrings
for the exact rule. This closes the gap that let the leakage bug above
happen in the first place. Still 100% read-only historical backtesting —
this infrastructure has no write access to live/forward-paper execution
paths, and nothing it produces reaches `main` without the normal
PR review process.

**2026-08-20 — development/validation-only parameter sweep, looking for a
higher win rate.** See `BACKTEST_FINDINGS.md`'s 2026-08-20 entries for the
full tables (~30 candidates across two passes). Headline: Baseline's real
win rate is only 8.8-12.8%, far below intuition — it's net-profitable
historically only because winners are much larger than the many small
stop-outs. Layering a moderate stop widening (`stop_risk_fraction=1.6` vs.
Baseline's 0.8) with a 50% profit target, a 20% trailing stop, a Strict RSI
filter (55/45), and an ATR floor (20) reaches **31.9%/29.3% win rate
(dev/val) — roughly 2.5x Baseline — with better absolute validation net
P&L than Baseline and under a third of Baseline's drawdown**, consistent
across both splits. Labeled **Open**, not Exploratory or Confirmed — no
test-range attempt was spent (none is currently possible; the ledger has
no fresh Upstox data past 2026-07-31 to test against). A **ceiling, not
just a stopping point**, was found: shrinking the profit target toward
noise level pushes win rate to ~48% but makes the strategy unprofitable
(concretely demonstrated — 5% target: dev +2,030/val -1,864; 2% target:
both clearly negative, profit factor ~0.42) — proof that win rate alone is
the wrong target. Tightening signal filters further than RSI55/ATR20 keeps
raising win rate (up to 32.0%) but shrinks trade count/total P&L into
thin-sample territory. ~30% win rate at strong profitability looks like
the practical ceiling from parameter tuning alone; closing the gap to 50%
further would need a different signal design in `strategy.py` (e.g.
mean-reversion/hit-rate-optimized), not more `BacktestParameters` tuning.

**2026-08-21 — ML signal-quality entry filter, first attempt.** See
`BACKTEST_FINDINGS.md`'s 2026-08-21 entry for the full comparison. Built a
hand-rolled (no numpy/scikit-learn, so it can run on Termux with zero new
dependency), 7-feature logistic-regression entry filter layered on top of
the existing deterministic `MomentumStrategy` signal — new modules
`ml_features.py`/`ml_model.py`/`upstox_ml_backtest.py`, a new
`options-bot backtest ml-validate-split` CLI subcommand, and a Windows-dev-
only `research/train_signal_quality_model.py`. A real correctness bug was
caught and fixed during this work: the training script's first threshold-
selection pass filtered an already-built unfiltered trade list post-hoc,
the exact trap `upstox_ml_backtest.py` exists to prevent (giving a kept
trade the wrong exit price) — caught by two independent code paths
disagreeing on the same number, fixed by routing threshold selection
through the real filtered backtest engine. **Headline result: the trained
model did not beat the existing best hand-tuned candidate** (31.9%/29.3%
win rate, +28,622.55/+19,133.25 net P&L) — it reaches a comparable win rate
(33.3%/27.3%) but on far fewer trades (15/22 vs 47/58), so total net P&L is
much smaller (+9,146.75/+3,003.75) and validation profit factor is worse
(1.41 vs 1.87). Likely cause: only 68 labeled development trades is thin
data for a 7-feature model to beat two well-chosen manual thresholds.
Labeled **Open** — a negative first result on a still-plausible idea, not
proof the approach is wrong. **Tried a same-day follow-up** (retrained on a
bigger Jan-May window, 155 raw signals vs. 68): also not trustworthy, but
for a different reason — the chosen threshold keeps only 9 development
trades while keeping 20 on validation, the same "strong validation, thin/
weak development" shape this project's own findings log already flags as
unreliable elsewhere. Neither ML attempt confirms nor cleanly rejects the
approach; at the time of writing this paragraph, the hand-tuned "Strict
RSI 55/45 + ATR floor 20" candidate above was the best evidenced result
from this research effort — **see the entry immediately below: that
candidate has since been tested and Rejected.** Not wired
into `service.py`/`web.py`/any live or forward-paper path — gated behind
`ML_SIGNAL_FILTER_ENABLED` (default `false`), which nothing currently reads.

**2026-08-21 (later same day) — the wall broke, and the flagship candidate
failed its first real test.** `credentials.env` was populated locally with
a working `UPSTOX_ACCESS_TOKEN`; a fresh pull brought the archive from
2026-07-31 up to **2026-08-20** (24,766 candles, zero warnings), making
2026-08-01 onward this project's first-ever genuinely untouched test
range. Spent that one-time attempt on the leading hand-tuned candidate
(`stop_risk_fraction=1.6, target_return=0.50, trailing_stop=0.20,
bullish_rsi_min=55, bearish_rsi_max=45, minimum_atr=20`) via
`options-bot backtest validate-split --test-start 2026-08-01 --test-end
2026-08-20`. `classify_confirmation()` returned `eligible_confirmed` (clean
methodology, genuinely fresh range) but the test result itself was bad — 4
trades, 0 winners, net P&L **-2,640.40**. Per this project's own rule,
`eligible_confirmed` + a bad result means **Rejected**, not Confirmed. See
`BACKTEST_FINDINGS.md`'s 2026-08-21 "First genuine test-range result"
entry for the full numbers and honest interpretation (most likely: ~30
hand-picked candidates searched over one dataset found a pattern specific
to that dataset, not a generalizable edge — exactly the failure mode this
project's ledger discipline exists to catch before real capital is at
risk). **No candidate from this entire research effort is currently
Confirmed.** Nothing here changes the paper-only safety boundary.
**Production status:** Not approved for live trading

## Objective

Build a tablet-operated NIFTY options research and paper-trading system that:

- obtains NIFTY and option market data from Angel One;
- stores validated five-minute data locally for future analysis;
- generates deterministic signals and simulates conservative fills;
- reports through a localhost dashboard and outbound-only Telegram alerts; and
- proves reliability and strategy behavior before any live-trading discussion.

This project does not promise passive income or profitability. Historical and
paper results are simulations and may not represent future results.

## Non-negotiable safety boundary

- `TRADING_MODE=paper` is the only accepted application mode.
- `LIVE_TRADING_ENABLED=false` and `AUTO_START=false` remain mandatory.
- The currently reachable Angel integration is market-data only.
- Telegram is outbound-only; it does not accept trading commands.
- Automatic entries create paper-ledger rows only and are disabled by default.
- Enabling automatic paper entries requires an explicit toggle confirmation;
  disabling the toggle prevents new entries while preserving exit monitoring.
- Never add a live-order call as an incidental part of another phase.
- Never commit or print Angel, Telegram, password, PIN, or TOTP secrets.
- Upstox is used only as a read-only historical-data source for offline
  backtesting; it must never place orders and is not a second trading
  broker. Never print or log an Upstox access token.

## What is implemented

### Tablet operation and UI

- FastAPI/Jinja2 dashboard protected with HTTP Basic authentication.
- Termux installer/launcher and localhost operation at `127.0.0.1:8000`.
- Initial local-only login is `admin` / `12345`; the launcher stores the
  password in `.termux-data/web-password` with owner-only permissions.
- Health, connection, archive, signal, paper position, journal, and safety
  controls are visible in one dashboard.

### Market data and archive

- Angel One session login, NIFTY LTP, and closed five-minute candles.
- EMA 9, EMA 21, RSI 14, ATR 14, freshness checks, and deterministic
  bullish/bearish/no-trade intelligence.
- Background refresh with reconnect and bounded catch-up behavior.
- Durable SQLite master archive at `.termux-data/market-data.sqlite3`.
- Duplicate-safe NIFTY candles, option candles, and retained instrument history.
- Nearest-expiry NIFTY option collection around ATM, CSV export, database
  backup, coverage/gap reporting, and SQLite integrity verification.

### Backtesting

- Offline-only replay from archived observations and option candles.
- Date filters, conservative slippage/fees, stop handling, force exit,
  per-trade details, drawdown, profit factor, and CSV export.
- Backtests report insufficient data rather than inventing missing prices.

### Paper trading and risk

- Two-step manually confirmed one-lot paper proposals.
- Optional automatic paper-only entries, explicitly enabled and persisted.
- The automation toggle starts a paper-monitor cycle immediately; periodic page
  refreshes always return to the GET dashboard instead of reloading POST routes.
- Browser refreshes and history navigation that revisit any `/actions/...` URL
  are redirected to the matching dashboard workspace instead of returning 405.
- Forward-paper collection defaults to two different concurrent contracts and
  no daily trade-count cap (`0`); fresh-signal deduplication, one-lot sizing,
  duplicate-contract, entry-window, daily-loss, capital, and quote gates remain.
- Paper capital and position tables expose premium committed, total entry cost,
  available capital, estimated equity, fresh LTP/P&L, and quote time, with a
  fixed 15-second monitor and dashboard refresh cadence.
- One attempt per closed signal candle prevents restart duplicates.
- Central checks for entry time, quote freshness, lots, maximum trade loss,
  open positions, duplicate symbols, daily trades, daily loss, and capital.
- Automatic stop, signal-reversal, and force-exit monitoring.
- Fresh position quotes, estimated unrealized P&L, restart recovery visibility,
  Telegram lifecycle alerts, and typed paper kill switch.

### Journal and reporting

- Durable trade context: signal time/direction, NIFTY spot, indicators,
  confidence, contract, expiry, strike, risk, and option excursion.
- Today, 7-day, 30-day, and all-time paper analytics.
- Win rate, average win/loss, reward-to-risk, profit factor, net P&L, and
  maximum drawdown.
- Durable safety/audit events.

### Operational hardening

- Persistent heartbeat, Angel/archive/paper-cycle timestamps, failure count,
  recovery time, storage headroom, stale-data entry lock, and dashboard status.
- Deduplicated repeated-failure and recovery Telegram notifications.
- One deduplicated post-force-exit Telegram daily report.
- Automatic archive backup rotation using the configured retention count.
- Termux startup now fails clearly on tracked local changes and requires a
  fast-forward to the latest `origin/main`, preventing silent use of old code.
- Termux safely migrates untouched legacy `1`-position/`3`-trades defaults to
  the two-position/no-count-cap forward-paper collection profile.
- CI pins Ruff below the next breaking rule expansion so local and GitHub lint
  checks evaluate the same documented rule set.

### Strategy validation workspace

- Explicit non-overlapping development, validation, and untouched test ranges.
- Side-by-side baseline, RSI, ATR, time-of-day, expiry-day, stop, maximum-hold,
  target, and trailing-stop variants without changing forward-paper settings.
- A candidate is selected from validation data; only that candidate is evaluated
  on the untouched test range, with CSV comparison export.

### Upstox historical backtesting — complete (Batch 3 of 3)

A second, strictly read-only data source is being added to speed up strategy
validation beyond what forward-paper collection alone can provide. Upstox is
data-only: it must never place orders and is not a second trading broker.

- `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_ACCESS_TOKEN` are recognized
  credential names (`credentials.py`); `UPSTOX_BACKTEST_ENABLED` (default
  `false`), `UPSTOX_TIMEOUT_SECONDS`, and `UPSTOX_MAX_LOOKBACK_DAYS` (default
  `180`) are new non-secret settings (`config.py`).
- `upstox_data.py`: a thin, mockable read-only client (instrument search,
  expiries, expired option contracts, expired historical candles, and the
  free non-expired Historical Candle Data V3 endpoint for underlying spot
  candles). Raises a clear error on an expired/invalid token or when an
  endpoint requires the paid Upstox Plus tier.
- `market_archive.py`: an idempotent migration adds a nullable
  `open_interest` column to `market_candles`; a new `save_upstox_candles()`
  method writes Upstox rows under `source="upstox"`, kept separate from the
  always-running Angel `save_candles()` path.
- `upstox_ingest.py`: discovers expired NIFTY option contracts for a
  requested date range (near-ATM strike selection, chunked per-contract
  requests, rate-limit pacing), pulls both option and underlying candles, and
  records each run via the existing `collection_runs` table.
- **Hard platform limit, not a budget/rate issue**: Upstox's expiry-discovery
  endpoint only returns expiries from roughly the last 6 months, so this
  feature cannot reach further back than that regardless of subscription
  tier or request volume. `UPSTOX_MAX_LOOKBACK_DAYS` mirrors this ceiling so
  requests fail fast with a clear message instead of a doomed round trip.
- **Operational note**: Upstox access tokens are short-lived (typically
  daily) with no long-lived refresh grant for this flow, unlike Angel's TOTP
  login — expect to re-authorize manually on a recurring basis.
- `upstox_backtest.py`: a walk-forward, no-lookahead replay
  (`generate_signals_from_candles`) that generates signals directly from raw
  Upstox underlying candles — no `strategy_observations` writes, kept fully
  separate from the in-production Angel-observation-based backtest.
  `run_upstox_backtest()` reuses the same conservative entry/exit/fee logic
  and restricts every `market_candles`/`instruments` lookup to
  `source='upstox'` so Angel and Upstox data can never be cross-matched in
  one run (verified by a dedicated test). Returns the existing
  `BacktestResult`/`OptionBacktestTrade` types unchanged.
- `upstox_analysis.py`: explainable, aggregate-only breakdowns (time-of-day,
  day-of-week, expiry-day, volatility regime, per-variant comparison using
  the existing `validation.py` strategy variants) and `generate_suggestions()`,
  which emits plain comparative statements only when both compared buckets
  have at least 20 supporting trades and the win-rate gap exceeds a 10
  percentage-point noise floor. No model, no fitting — every suggestion is a
  hypothesis mined from historical data, not a proven edge, and is meant to
  be manually retested through the existing development/validation/test
  split discipline, never tuned against the same data it was mined from.
- **New "Historical backtest" dashboard tab.** Two forms: pull Upstox data
  for a date range (`/actions/upstox-ingest`), then run a backtest over the
  archived data (`/actions/upstox-backtest`), which also computes the deep
  analysis breakdowns and suggestions in the same action. A CSV export
  (`/upstox/trades.csv`) mirrors the existing Research tab's pattern. Every
  route fails with a clear on-page message (never a stack trace) when the
  feature is disabled, credentials are missing, or Upstox itself is
  unreachable — network-level failures (DNS, blocked/refused connections,
  timeouts) are caught explicitly, not just HTTP error codes.
- `run_strategy_validation()` now accepts an injectable `runner` parameter
  (defaults to the existing Angel-observation-based `run_momentum_backtest`,
  unchanged), so Upstox-sourced strategy variants can go through the
  identical development/validation/untouched-test selection discipline
  instead of a shortcut.
- Manually verified end-to-end in development: booted the dashboard with
  `UPSTOX_BACKTEST_ENABLED=true` and a placeholder token, confirmed the new
  tab renders, confirmed the disabled/missing-credential/network-failure
  paths all show friendly messages instead of crashing (a real bug — an
  unhandled network exception causing a 500 — was found and fixed this way,
  with a regression test added), and confirmed the backtest action correctly
  reports `INSUFFICIENT DATA` against an empty archive.
- **Confirmed on a real Termux device**: the "Historical backtest" tab
  renders and the ingest form submits correctly. That run surfaced a real
  production issue not reachable from development testing: Upstox's API
  sits behind Cloudflare, which returned `HTTP 403` (Cloudflare error 1010,
  "blocked access based on your browser's signature") because the client
  sent no `User-Agent` header, so Python's default (`Python-urllib/...`)
  was flagged as a bot. Fixed by sending a standard browser-style
  `User-Agent`/`Accept-Language`, covered by a regression test.
- **Second real-device finding**: past the Cloudflare block, ingestion
  crashed with `KeyError: 'expired_instrument_key'`. Upstox's own docs are
  internally inconsistent about this field's name — the "Backtesting" guide
  calls it `expired_instrument_key`, but the confirmed Expired Future
  Contracts example response names it `instrument_key`, and real Expired
  Option Contracts responses use `instrument_key` too. `plan_ingestion` now
  accepts either name (and similarly for `strike_price`/`strike` and
  `instrument_type`/`option_type`), and raises a clear diagnostic error
  (listing the actual fields present) instead of a raw `KeyError` if a
  future response shape doesn't match either name — covered by regression
  tests for both the fallback and the diagnostic-error path.
- A real 7-month pull against live Upstox data (Jan-Jul 2026) has been
  completed and analyzed end-to-end, including the full
  development/validation/untouched-test discipline. See
  `BACKTEST_FINDINGS.md` for the detailed log.
- **Ingestion tracks archive coverage and skips already-fetched data.**
  `MarketArchive.has_upstox_candles()`/`upstox_coverage_ranges()` let
  `pull_range()` skip any date-chunk already archived for a token instead of
  re-calling Upstox for it (an explicit `force_refetch` checkbox on the tab
  bypasses this when needed). The "Pull historical data" card now shows
  which date ranges are already available before you pull anything, and the
  ingestion result message reports how many chunks were skipped as
  already-cached.
- **Deep analysis now leads with plain-language highlights.** A new
  "Highlights" card shows the best/worst-performing group per dimension
  (time-of-day, day-of-week, expiry-day, volatility) from *any* sample
  size, clearly labeled "Preliminary" when either side is below the same
  20-trade threshold the formal Suggestions section requires. This answers
  "what's going well/badly" even from a single small backtest, while the
  existing Suggestions section stays statistically gated and unchanged.
- **"Copy analysis for Claude" plain-text export.** A new
  `GET /upstox/analysis-summary.txt` route (linked from the Highlights card)
  renders the full deep-analysis report — overall stats, every breakdown
  bucket, variant comparison, and highlights, plus the same small-sample
  caution — as plain text meant to be copy-pasted directly into a chat.
  `format_analysis_summary()` in `upstox_analysis.py` does the rendering; it
  is a pure formatter over already-computed data, not a new analysis path.
  404s until a backtest has been run.
- **New "No stop-loss cap" variant, and why it exists.** A real month-long
  Upstox backtest (56 trades, July 2026) showed a 7.1% win rate with a
  suspiciously uniform ~-300 average loss in almost every breakdown bucket —
  the fingerprint of trades being mechanically stopped out by a fixed-rupee
  stop distance (`MAX_LOSS_PER_TRADE × stop_risk_fraction ÷ lot size`, a few
  rupees of option premium by default) rather than a genuine directional
  read. `BacktestParameters.stop_risk_fraction` is now `float | None`;
  setting it to `None` skips the price-based stop/target/trailing-stop
  block entirely in both `backtest.py` and `upstox_backtest.py`, so a trade
  only exits on a signal reversal, a max-hold cap, or the session's
  force-exit time. `STRATEGY_VARIANTS` in `validation.py` includes this as
  `"No stop-loss cap"` so it always appears in the deep-analysis variant
  comparison and the "Copy analysis for Claude" export, for diagnosing
  whether a strategy's real signal quality is being masked by an
  over-tight stop before drawing conclusions from win rate alone.
- **Capital deployed and return on capital now shown alongside P&L and
  drawdown.** `BacktestResult` gained three read-only properties —
  `capital_deployed_total` (sum of entry premium across all trades),
  `capital_deployed_average` (per-trade mean), and `return_on_capital_pct`
  (net P&L as a percentage of total capital deployed) — computed purely
  from already-recorded `trade_details`, no new stored fields or analysis
  path. Shown on both the Angel-sourced offline-backtest card and the
  Upstox-backtest card, and in the "Copy analysis for Claude" export. Added
  in response to a question about what "drawdown" means and a request to
  see total capital used, since a raw rupee P&L number is hard to judge
  without knowing how much capital produced it. Note this is *turnover*
  capital, not simultaneous margin — positions are opened one at a time,
  never overlapping, so this is the sum of money moved across the whole
  period, not a peak concurrent-exposure figure.
- **Fixed a real cross-source data-quality bug found while building the
  validation wiring below.** `MarketArchive.gap_summary()` queried
  `market_candles` across *all* sources at once, so a genuine gap in
  Angel's own archive could mark every Upstox backtest as
  `DATA QUALITY WARNING` (and vice versa) even when the source actually
  being backtested had zero gaps. `gap_summary()` now takes a `source`
  parameter (default `"angel-one"`); `build_backtest_result()` threads the
  correct source through from both `run_momentum_backtest` (`"angel-one"`)
  and `run_upstox_backtest` (`"upstox"`), so each engine's status only ever
  reflects its own data's quality.
- **"Custom parameters" card — build/test a strategy variant from the UI,
  no code change per attempt.** A new form on the Historical Backtest
  (Upstox) tab exposes every tunable `BacktestParameters` field (stop-loss
  width or no-cap, max hold minutes, profit target %, trailing stop %,
  RSI thresholds, minimum ATR, entry window, expiry-day exclusion, allowed
  weekdays) and runs `/actions/upstox-custom-backtest` immediately over the
  chosen range via `run_deep_analysis(..., variants=(custom,))`, showing
  the same stats/highlights as the main backtest card plus its own CSV and
  "Copy analysis for Claude" export. The form re-populates with the last
  submitted values so iterating (change one field, rerun) doesn't require
  retyping everything. This is deliberately a fast, ungated exploration
  loop — a promising custom combination graduates to real trust only by
  being hardcoded as a new named entry in `STRATEGY_VARIANTS` and then
  run through the stricter validation split below.
- **Strategy validation now works against Upstox data too.**
  `run_strategy_validation()` already accepted an injectable `runner`
  (added in an earlier phase) but no dashboard route ever passed
  `run_upstox_backtest` through it — the development/validation/untouched-
  test split was Angel-only in practice. A new
  `/actions/upstox-validation` route (mirroring the existing Angel
  `/actions/strategy-validation` route and UI exactly) now runs the same
  `STRATEGY_VARIANTS` list against Upstox data with
  `runner=run_upstox_backtest`, so a variant that looks good in the quick
  "Custom parameters" exploration can be confirmed the rigorous way —
  selected on a validation range it wasn't picked from, then checked once
  on an untouched test range — before it's trusted, exactly as the
  project's existing anti-overfitting discipline requires for Angel-sourced
  variants. Still 100% read-only historical replay; never touches
  live/forward-paper trading.

### ML signal-quality entry filter — backtest-only, three attempts so far

A trained, hand-rolled logistic-regression filter that decides whether to
take a signal the existing deterministic `MomentumStrategy` already
generated — never a replacement for that strategy's own direction logic,
and never wired into live/forward-paper trading in this phase.

- `src/options_bot/ml_features.py`: pure, stateless feature extraction (RSI,
  normalized ATR, normalized EMA gap, confidence, direction, minutes-since-
  open, day-of-week) from the same candle window the existing backtest
  engine already uses — no changes to `strategy.py`'s tested contract.
- `src/options_bot/ml_model.py`: `SignalQualityModel` — a dependency-free
  (no numpy/scikit-learn, `math`/`json` only) standardize-dot-product-sigmoid
  scorer, so it can eventually run on the memory-constrained Termux runtime
  with zero new dependency. Training (Windows-dev-machine-only) uses this
  exact same function, so there is one implementation of "the model," not
  two that could quietly disagree.
- `src/options_bot/upstox_ml_backtest.py`: a deliberately separate backtest
  engine (mirroring `upstox_backtest.py`'s own precedent relative to
  `backtest.py`) that applies the ML decision to the observation list
  *before* trade construction runs — filtering afterward would silently
  give a kept trade the wrong exit price whenever a rejected signal sits
  between two kept ones, since exit-by-signal-reversal boundaries are
  computed from the next *surviving* observation. A real version of this
  exact bug was caught and fixed in the training script during this work
  (see `BACKTEST_FINDINGS.md`'s 2026-08-21 entry).
- New `options-bot backtest ml-validate-split` CLI subcommand
  (`backtest_cli.py`), reusing `research_ledger.py`'s existing
  check/record/fingerprint machinery unchanged — a trained model is
  fingerprinted the same way a hand-tuned `BacktestParameters` set is.
- `research/train_signal_quality_model.py` (Windows-dev-only): trains on
  the Development range, selects a decision threshold against the
  Validation range only, exports a small diffable JSON weights file under
  `research/models/`.
- New `ML_SIGNAL_FILTER_ENABLED` config flag (default `false`, following
  the existing `UPSTOX_BACKTEST_ENABLED` pattern) — currently read nowhere,
  a stricter research-only posture than even the Upstox precedent.
- **First two attempts (v1, v2-bigdata) did not beat the existing best
  hand-tuned candidate** — see `BACKTEST_FINDINGS.md`'s 2026-08-21 entry for
  the full comparison and the likely cause (thin training data: 68 and 155
  raw signals respectively).
- **A third attempt (v3-extended, 2026-08-22), retrained on the
  historical-extension archive (~15x more raw signals: 1,012), is the best
  ML result so far** — 449/48 trades on dev/validation, 43.75% validation
  win rate, 2.17 validation profit factor. See `BACKTEST_FINDINGS.md`'s
  2026-08-22 "v3" entry for the full numbers and caveats. Still labeled
  Open, same as every candidate — no test-range attempt was made or is
  appropriate. `research/models/ml-signal-quality-v3-extended.json` is the
  saved model. Training this required fixing two more real bugs: the
  training script couldn't use derived/extended-history data at all until
  it gained `--include-derived`, and `generate_signals_from_candles` had a
  genuine O(n²) performance bug (recomputing every indicator from scratch
  per candle) that made this exact training run hang for 30+ minutes before
  being fixed — see that same entry.
- **A fourth attempt (v4-hyperparam-swept, 2026-08-22, same day) beats v3
  by finding a better threshold, not a better model.** Swept L2/learning-
  rate (learning rate made no difference; l2=0.05 was a real, modest
  improvement over v3's 0.01) and, critically, extended the threshold
  search below v3's floor of 0.30 — the true peak is at 0.25, a region v3
  never tried. Same win rate as v3 (~43%) but keeps almost twice the
  trades, giving +38,175.00 net P&L vs. v3's +15,396.00. Also surfaced the
  same win-rate-vs-total-return tradeoff already known from the hand-tuned
  filters: pushing the threshold to 0.35 instead gives 57.1% win rate and
  6.19 profit factor on a much smaller (21-trade) set. See
  `BACKTEST_FINDINGS.md`'s 2026-08-22 "v4" entry for the full curve.
  `research/models/ml-signal-quality-v4-hyperparam-swept.json` is the
  current best model. Still Open, not Confirmed.
- **v5: rolling-origin (walk-forward) validation answers the question every
  prior attempt left open — does this generalize?** Retrained from scratch
  on each of 6 rolling quarterly folds, validating on a quarter the model
  had never seen. **5 of 6 quarters net profitable.** The one loss
  (2025 Q4, -381.00) lines up exactly with the same quarter where the
  hand-tuned filter *and* plain Baseline both also went negative in the
  historical-extension check — an adverse regime for this whole strategy
  family, not an ML-specific failure. See `BACKTEST_FINDINGS.md`'s
  2026-08-22 "v5" entry for the full per-fold table.
- **v6: a new `upstox_ml_backtest_v2.py` engine adds open-interest and
  days-to-expiry features**, the last untested item from this project's
  feature-idea list. Required moving contract selection before the ML
  decision (v1's engine only selects a contract after filtering) — a real
  architecture change, validated with 5 new tests before being trusted,
  including proof it's byte-identical to v1 for a precontract-only model
  and that the OI feature genuinely drives keep/reject decisions (not just
  computed and ignored). Trained and compared against v4 head-to-head:
  **almost no improvement** (85t/43.5%/+38,383.95/PF 2.53 vs. v4's
  84t/42.9%/+38,175.00/PF 2.52) — a real, honest negative-ish result, not a
  reason to prefer the more complex model. See `BACKTEST_FINDINGS.md`'s
  2026-08-22 "v6" entry.

### Strategy research backlog — not active

The professional strategy assessment is a research roadmap, not active forward
paper logic. After the current baseline has enough complete evidence, evaluate
fresh EMA crossover/separation and slope, trend pullbacks, opening-range
breakouts, VWAP/ADX confirmation, normalized ATR regimes, and minimum sample
requirements. Bid/ask liquidity, volume/open interest, implied volatility,
Greeks, calibrated confidence, and debit spreads require additional reliable
option-chain fields and conservative multi-leg cost modelling before they can be
implemented. Do not activate these ideas together or select them from the same
period used for final testing.

**Update 2026-08-22**: opening-range breakouts and a macro-trend/multi-
timeframe-style confirmation have now been built and screened (see
`BACKTEST_FINDINGS.md`'s "Three alternative strategies" entry) — remove
them from this backlog's "not yet tried" framing; opening-range breakout
was weaker than Baseline, trend-confirmed momentum is a promising lower-
drawdown variant worth a full validation pass. Mean-reversion (not
originally on this list) was also tried and rejected outright (0% win
rate on the tested exit shell). VWAP/volume-based ideas remain blocked for
a new reason beyond what this paragraph already says: Upstox's raw candle
response includes volume (`row[5]`), but `upstox_data.py`'s
`parse_candle_row` currently discards it and no `volume` column exists in
`market_candles` — addressable, but not done.

### Paper-readiness review gate

- Evidence checklist for archive coverage/gaps/integrity, paper-trade count and
  drawdown, force exits, heartbeat/failures, backups, credential permissions,
  dashboard password, and the paper-only boundary.
- Persisted manual acknowledgements for broker restrictions, recovery drills,
  and operator acceptance, plus a downloadable review CSV.
- A completed review explicitly records `live_trading_approved=false`; it never
  changes configuration or exposes broker order submission.

## Important local files

| Purpose | Tablet path relative to repository |
| --- | --- |
| Non-secret runtime configuration | `local-bot.env` |
| Private Angel/Telegram credentials | `credentials.env` |
| Saved dashboard password | `.termux-data/web-password` |
| Paper account, orders, journal, state | `.termux-data/paper.sqlite3` |
| Market-data master archive | `.termux-data/market-data.sqlite3` |
| Automatic archive backups | `.termux-data/backups/` |
| Range-usage ledger (which date ranges were used for what — lives inside the same market-data archive, `range_usage` table) | `.termux-data/market-data.sqlite3` |
| Range-usage ledger, human-readable export | `research/range_usage_ledger.json` |
| Research pipeline role prompts | `research/prompts/` |

SQLite files are the master records. CSV downloads are exports, not databases.
Do not delete `.termux-data` during updates.

### Historical Upstox backtest data — current coverage

**2024-10-03 to 2026-08-20** — NIFTY underlying + option-chain `FIVE_MINUTE`
candles, Upstox source only, split real vs. derived (see
`BACKTEST_FINDINGS.md`'s 2026-08-22 entry for the full table and how each
piece was built):

- **Real** (fetched live from Upstox, `derived_from_timeframe IS NULL`):
  option contracts 2024-10-03 to 2026-08-18 (1,715 tokens, 753,815 rows);
  underlying index 2026-01-01 to 2026-08-20 (11,775 rows).
- **Derived** (resampled locally from already-archived real `ONE_MINUTE`
  data, tagged `derived_from_timeframe='ONE_MINUTE'`, excluded by default
  from `run_upstox_backtest`/`run_upstox_ml_backtest` unless
  `include_derived=True` is passed explicitly): option contracts
  2025-08-14 to 2025-12-30 (438 tokens, 183,819 rows); underlying index
  2024-10-03 to 2025-12-31 (23,134 rows — the underlying's `ONE_MINUTE`
  source rows for this span were themselves a mislabeled 5-minute feed, not
  genuine 1-minute data; see that entry for detail).

2024-10-03 is Upstox's verified hard platform ceiling (`get_expiries()`
returns no expiry older than this at any subscription tier) — there is no
older data to fetch. `ONE_MINUTE` coverage (genuinely 1-minute-spaced for
every instrument except the underlying's pre-2026 rows, see above) spans the
same 2024-10-03 to 2026-08-20 range, 2,154 tokens, real and untouched; it is
never read directly by any backtest engine, only used as source data for the
derived rows above. `TEN_MINUTE`/`FIFTEEN_MINUTE` also exist, fully derived
and tagged, built as a side effect of the same materialize run.

This range lives only inside `market-data.sqlite3` (the row above) —
**it is never committed to git**, so a fresh `git clone` of this repo
does NOT include it. If you're starting a new session/device and don't
already have a copy of that file, ask the user for it (they've
previously transferred it via a temporary GitHub Release asset) rather
than assuming the archive is empty or re-pulling from scratch.

Whoever ingests new data past 2026-07-31 (the dashboard's Upstox ingest
tab, or `pull_range` directly) must update the date above in the same
commit — this is the one line a new session should trust to know "how
much history do we actually have" without querying the database
directly. `options-bot backtest ...` never ingests anything; it only
reads candles already in the archive. To verify the actual candle
coverage yourself (not `options-bot backtest ledger`, which reports
research-usage history — which candidate/role touched what range — not
raw candle coverage, and doesn't update just because new candles
arrive): query `SELECT MIN(started_at), MAX(started_at) FROM market_candles
WHERE source='upstox'`.

## How to resume on the tablet

```bash
cd ~/AI-Options-Trading-Bot
git checkout main
git pull origin main
chmod +x scripts/termux_web.sh
scripts/termux_web.sh
```

Then open `http://127.0.0.1:8000` on the same tablet. Keep Termux running,
disable Android battery optimization for Termux, and keep the tablet powered
during a full-session test.

## Current validation plan

Before adding another trading feature, collect forward paper evidence:

1. Run at least 20 complete NSE sessions, preferably producing 100+ paper
   trades before drawing strategy conclusions.
2. Check archive gap count, reconnects, monitor errors, duplicate entries,
   force exits, journal completeness, fees, and drawdown each day.
3. Confirm no paper position remains open after the configured force-exit time.
4. Download periodic SQLite backups without replacing the tablet master copy.
5. Keep automatic paper entry disabled whenever behavior is being diagnosed.

## Completed implementation phases

### Phase A — operational hardening and daily Telegram report

- Persists monitor heartbeat, consecutive failure count, last successful Angel
  response, last archive write, and last paper cycle.
- Adds storage-space and stale-data lockout indicators.
- Alerts Telegram after repeated failures and again after recovery.
- Sends one force-exit-time daily paper report with trades, P&L, fees, open
  positions, archive gaps, reconnects, failures, and database integrity.
- Adds backup retention/rotation and Termux:Boot guidance.
- Includes deterministic tests for alert deduplication and restart behavior.

### Phase B — strategy validation workspace

- Compares strategy variants without changing the forward-paper strategy.
- Adds development, validation, and untouched test date ranges.
- Compares RSI thresholds, ATR filter, time-of-day filters, stop styles,
  trailing stops, targets, maximum hold, weekdays, and expiry-day behavior.
- Exports a comparison table and clearly flags insufficient/gapped datasets.
- Avoids choosing parameters solely because they maximize historical profit.

Both phases are implemented. Results remain preliminary until the archive has
enough complete, low-gap sessions in every split.

## Completed review phase

### Phase C — review gate, not automatic live deployment

Only after extended forward-paper evidence, perform a documented readiness
review covering reliability, drawdown, data quality, broker restrictions,
security, operational recovery, and user acceptance. Live trading is not an
approved phase and must require a separate explicit decision and design review.

The evidence gate is implemented, but it will remain blocked until the tablet
has collected the required forward-paper evidence and manual reviews. Passing
the gate still does not approve or enable live trading.

## Known limitations and cautions

- Termux must remain alive; Android can suspend it despite the web page working.
- `12345` is acceptable only for localhost. Use a strong password before any
  tunnel or LAN binding.
- Signals and option history are useful only when archive coverage is adequate.
- Market-closed behavior cannot validate live-session quote timing.
- SmartAPI availability, permissions, rate limits, and contract data remain
  external dependencies.
- A successful backtest or paper period does not guarantee profitability.
- Upstox's expiry-discovery endpoint only covers roughly the last 6 months;
  historical backtesting through Upstox cannot reach further back than that
  regardless of subscription tier. Upstox access tokens expire (typically
  daily) and require manual re-authorization; there is no long-lived refresh
  grant for this flow.

## Standard checks before committing

```bash
git diff --check
bash -n scripts/termux_web.sh
ruff check src tests
python -m compileall -q src tests
python -m pytest -q
python -m pip wheel --no-deps . -w /tmp/options-wheels
```

For a visible dashboard change, capture a screenshot when a browser is
available. If the environment prevents it, record the exact limitation.

## Session update checklist

At the end of every development session, update this file when applicable:

- [ ] Change **Last updated** and **Current phase**.
- [ ] Move completed work into **What is implemented**.
- [ ] Update **Next implementation phases** and known blockers.
- [ ] Record new operating commands, paths, and safety decisions.
- [ ] Ensure no credentials or personal secrets were added.
- [ ] Run the standard checks and include results in the final response.
- [ ] Commit, push, and update/create the pull request as required.

## Latest handoff

- Durable paper journal and period analytics are complete.
- Guarded automatic paper entries are complete and disabled by default.
- Operational hardening, daily Telegram reporting, backup rotation, and the
  strategy validation workspace are complete.
- The paper-readiness evidence gate and CSV review export are complete; live
  trading remains unapproved and unreachable.
- The strategy assessment backlog is documented but intentionally not active;
  the EMA/RSI ATM-option baseline remains the forward-paper strategy.
- The most valuable next activity is collecting complete forward paper sessions,
  preserving the SQLite archive, and reviewing Phase B only when every split has
  adequate low-gap option history.
- All three Upstox historical-backtesting batches (credentials/settings/
  client; storage/ingestion; backtest engine and deep analysis/suggestions;
  dashboard tab and validation-loop integration) are merged to `main`.
  `UPSTOX_BACKTEST_ENABLED` still defaults to `false`, so none of this
  changes runtime behavior for existing forward-paper operation unless
  explicitly turned on.
- Confirmed working on a real Termux device: the dashboard tab renders and
  the ingest action reaches Upstox. Two real-device-only issues were found
  and fixed, each merged separately: a Cloudflare bot-block (HTTP 403, error
  1010) caused by a missing `User-Agent` header, and a `KeyError` from
  Upstox's expired-option-contract responses using field name
  `instrument_key` where their own docs inconsistently say
  `expired_instrument_key` — the client now accepts either. The Upstox
  historical-backtesting feature is feature-complete end-to-end and its
  connection/discovery steps are confirmed live.
- Added coverage-aware ingestion (skip already-fetched date ranges unless
  explicitly forced) and plain-language "Highlights" alongside the existing
  gated Suggestions, in response to direct user feedback that repeat pulls
  wasted API calls and that the analysis view gave no visible feedback
  until 20+ trades accumulated.
- Added a "Copy analysis for Claude" plain-text export
  (`GET /upstox/analysis-summary.txt`) so the full deep-analysis breakdown
  can be pasted directly into a chat for tuning discussion, in response to
  direct user feedback that they wanted an easy way to hand over analysis
  results for parameter-change suggestions.
- **A real 7-month Upstox archive (Jan-Jul 2026) was pulled and analyzed
  end-to-end** — full development/validation/untouched-test discipline,
  not just a single-range look. New `BACKTEST_FINDINGS.md` is the detailed,
  dated log of every round; this file only carries the headline (see the
  top of this document). Started a dedicated documentation discipline:
  every future backtest round gets a dated entry there — confirmed,
  rejected, or open — so nothing gets re-tested or re-forgotten.
- **Added `src/options_bot/research_ledger.py` (a new `range_usage`
  table) and `src/options_bot/backtest_cli.py` (`options-bot backtest
  check-range/run/validate-split/ledger`), plus `research/` role prompt
  templates, as infrastructure for a planned 5-role automated backtest
  research loop.** Built in direct response to a review-caught leakage
  bug: a "confirmed" result had actually reused an already-touched date
  range. The ledger makes that mechanically impossible going forward — a
  test range must start strictly after every range ever recorded for
  that underlying/timeframe, and a candidate gets exactly one test
  attempt — enforced in code (`check_range`/`record_usage`/
  `classify_confirmation`), not by an LLM remembering correctly.
  `AGENTS.md` rule 6 now points at this module as the mechanical
  enforcement behind the existing logging requirement. Still 100%
  read-only historical backtesting; no write access to live/forward-paper
  execution paths.
