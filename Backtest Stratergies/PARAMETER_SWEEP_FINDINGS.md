> **Correction (see PHASE2_REPORT.md section 1):** every sweep below for #7, #8, #10, #12 was
> run before a sizing bug was found and fixed (`size_or_floor()` was being passed risk **per
> lot** instead of **per unit**, causing it to floor to 1 lot far more often than the real 1-2%
> budget required). **Every "Floored: 100%" figure and every "sizing-floor check" paragraph
> below for #8, #10, and #12 is now WRONG** — bug-fixed, their *default/recommended* configs
> floor at 93.3%, 76.2%, and just 5.9% respectively, not 100% (#12's real per-lot risk is
> routinely *under* the Rs2,000 budget, the opposite of what its section below claims). The net
> P&L / win-rate / PF / Sharpe **numbers themselves are unaffected for every config shown here
> except #11** (not swept in this file) **and are still correct** — #7's, #8's, #10's, and
> #12's default configs happen to still land at exactly 1 lot even under correct math, so their
> P&L didn't move; only the floor-percentage diagnostic and the surrounding "still stuck at
> 100%, needs more capital" narrative were wrong for #8/#10/#12. **#7 and #13 are the only
> strategies where "genuinely too wide for this capital" still holds after the fix.** This file
> is left otherwise unedited as a record of what was actually tested; treat every "Floored"
> column and "Sizing-floor check" paragraph as superseded.

# Parameter Sweep Findings — six kept strategies, 2022 (in-sample) vs 2023 (out-of-sample)

Methodology: every config below was run with `--risk 0.02` from the six strategy
scripts in `strategies/`, each now exposing its tunable constants as CLI
argparse flags with the CURRENT (original REPORT.md) value as the default, so
nothing changes unless a flag is passed. Every run's full trade log and
`by_year` breakdown is saved under `results/sweeps/<strategy>_<label>.json` via
`E.save_results(f"sweeps/{name}_{label}", ...)`. **No file under `results/*.json`
(the originals) was modified** — the six scripts were verified byte-for-byte
against REPORT.md's numbers before any sweep config was run (see verification
note at the end of each section). A new config is only recommended over the
current default if it wins on **2023 specifically** (out-of-sample); a config
that only wins pooled or only wins 2022 is treated as curve-fit and rejected.

---

## #3 s03_orb.py — Opening Range Breakout

**Grid tested:** timeframe (5-min current vs 15-min), then target-mult (1.5,
1.75 current, 2.0) on the winning timeframe. New flags: `--timeframe`,
`--min-range`, `--target-mult`.

| Config | Trades | Win% | Net (both yrs) | PF | Sharpe | 2022 net | 2023 net |
|---|---:|---:|---:|---:|---:|---:|---:|
| **5-min (current)** | 345 | 45.2% | Rs80,964 | 1.417 | 2.67 | Rs42,525 | **Rs38,440** |
| 15-min | 249 | 47.4% | Rs64,730 | 1.691 | 3.66 | Rs35,562 | Rs29,168 |
| target-mult 1.5 (on 5-min) | 343 | 46.1% | Rs60,716 | 1.317 | 2.17 | Rs34,572 | Rs26,144 |
| target-mult 2.0 (on 5-min) | 346 | 43.9% | Rs78,436 | 1.393 | 2.48 | Rs39,270 | Rs39,166 |

**Recommendation: keep current default (5-min timeframe, target-mult 1.75).**
5-min beats 15-min in both years outright. Target-mult 2.0 edges out 1.75 in
2023 by only Rs726 (1.9%, noise-level) while losing Rs3,254 of 2022's edge —
not a real out-of-sample win, just a wash with a worse in-sample number
attached, so it doesn't clear the bar. No sizing-floor issue applies to #3
(single-leg option buy, not one of the four flagged strategies).

---

## #4 s04_supertrend_vwap.py — Supertrend + VWAP

**Grid tested:** (period, mult) in {(7,2), (10,2), (10,3) current, (14,3),
(14,4)}. New flags: `--period`, `--mult`.

| Config | Trades | Win% | Net (both yrs) | PF | Sharpe | 2022 net | 2023 net |
|---|---:|---:|---:|---:|---:|---:|---:|
| **(7,2)** | 871 | 53.8% | Rs576,789 | 3.199 | 7.28 | **Rs267,298** | **Rs309,491** |
| (10,2) | 860 | 53.6% | Rs521,644 | 2.927 | 6.62 | Rs255,187 | Rs266,457 |
| (10,3) current | 459 | 46.2% | Rs166,529 | 1.997 | 4.18 | Rs79,599 | Rs86,930 |
| (14,3) | 478 | 49.2% | Rs271,137 | 2.662 | 5.08 | Rs153,352 | Rs117,785 |
| (14,4) | 263 | 42.2% | Rs87,134 | 1.837 | 2.97 | Rs33,656 | Rs53,478 |

**Recommendation: switch to Supertrend(7,2).** This is the clearest result in
the whole sweep — (7,2) is not just marginally better, it more than triples
2023's net (Rs309,491 vs Rs86,930) and roughly triples 2022's too, with a
higher win rate (53.8% vs 46.2%), higher PF (3.2 vs 2.0) and higher Sharpe
(7.28 vs 4.18) than the current default, consistent across both years. The
faster (7,2) parameterization catches more of this window's trend flips
without the false-signal cost outweighing it. No sizing-floor issue applies
to #4 either.

---

## #7 s07_iron_condor.py — Iron Condor

**Grid tested:** fixed-200 TP-70 (current), fixed-200 TP-50, straddle-width(0.5)
TP-70, straddle-width(0.5) TP-50, straddle-width(0.75) TP-70. New flags:
`--wing-mode {fixed,straddle-width}`, `--sw-multiplier`, `--take-profit`.
Straddle-width mode: wing = short strike ± (ATM straddle premium × multiplier),
rounded to the nearest 50-pt strike step.

| Config | Trades | Win% | Net (both yrs) | PF | Sharpe | 2022 net | 2023 net | Floored |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **fixed-200 TP-70 (current)** | 63 | 84.1% | Rs30,489 | 8.983 | 16.41 | Rs9,521 | **Rs20,968** | 100.0% |
| fixed-200 TP-50 | 63 | 84.1% | Rs21,744 | 6.693 | 13.32 | Rs7,894 | Rs13,850 | 100.0% |
| straddle-width(0.5) TP-70 | 75 | 73.3% | Rs12,330 | 2.161 | 5.53 | Rs6,542 | Rs5,788 | 100.0% |
| straddle-width(0.5) TP-50 | 75 | 76.0% | Rs5,560 | 1.622 | 3.16 | Rs4,128 | Rs1,431 | 100.0% |
| straddle-width(0.75) TP-70 | 66 | 77.3% | Rs18,610 | 3.135 | 8.16 | Rs8,270 | Rs10,341 | 100.0% |

**Recommendation: keep current default (fixed-200 wing, 70% take-profit).**
None of the four alternatives beat it on 2023. Loosening the take-profit to
50% roughly halves the result in both modes — the report's earlier
"loosen the TP" hypothesis (from the held-to-day-end counterfactual) does
NOT hold up when actually tested; the 70% target is better, not worse.

**Sizing-floor check — the whole point of this variant:** straddle-width mode
DID fix the "wing not found" skip problem partially (41→29 skipped expiries,
63→75 tradeable cycles), because a straddle-premium-scaled wing is almost
always priced even when the fixed 200-pt strike isn't. But it did **NOT**
escape the sizing floor: still 100% of trades floored to 1 lot in every
config tested, current and alternatives alike. Digging into why: the
straddle-width wings actually landed narrower on average (mostly 100pt,
sometimes 50pt, vs the fixed 200pt) — but a narrower wing also means the long
leg sits closer to the short strike, so its premium is higher and it eats
proportionally more of the credit collected. Average real risk-per-lot came
out Rs8,719 (fixed-200) vs Rs9,116 (straddle-width 0.5) vs Rs8,945
(straddle-width 0.75) — all similar, all far above the Rs2,000 budget (2% of
Rs1,00,000). Narrowing the wing doesn't shrink max-loss/credit fast enough to
close a ~4-4.5x gap. Original config floored: 63/63 (100%). Recommended
config floored: 63/63 (100%) — unchanged, because the recommendation is to
keep the original config.

---

## #8 s08_banknifty_strangle.py — Short Strangle Hard-Stop

**Grid tested:** per-leg SL% in {50%, 75%, 100% current}. Combined stop cap
(Rs15,000/lot) held fixed per the brief. New flags: `--leg-stop-pct`,
`--combined-stop-per-lot`.

| Config | Trades | Win% | Net (both yrs) | PF | Sharpe | 2022 net | 2023 net | Floored |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| leg-stop 50% | 104 | 78.8% | Rs37,436 | 1.979 | 4.05 | Rs26,634 | Rs10,801 | 100.0% |
| **leg-stop 75%** | 104 | 74.0% | Rs53,395 | 3.070 | 6.14 | **Rs35,733** | **Rs17,662** | 100.0% |
| leg-stop 100% (current) | 104 | 56.7% | Rs46,600 | 2.243 | 4.66 | Rs31,471 | Rs15,128 | 100.0% |

**Recommendation: switch to 75% per-leg stop.** Wins both years cleanly
(2022: Rs35,733 vs Rs31,471; 2023: Rs17,662 vs Rs15,128), higher PF (3.07 vs
2.24) and higher Sharpe (6.14 vs 4.66) than the current 100%(doubles) stop —
a genuinely tighter, better risk/reward config, not a curve-fit artifact
(50% overshoots and gives back the 2023 edge).

**Sizing-floor check:** still 100% floored under BOTH the original and the
recommended config — no improvement. Investigated why: #8's risk-per-unit for
sizing is `min(Rs15,000/lot, total_credit_collected × lot_size)`, which does
**not** depend on the leg-stop trigger distance at all — it's the credit
collected at entry, not the stop's premium distance. Tightening the per-leg
SL changes when/how a trade exits (hence the P&L improvement above) but has
zero effect on the sizing formula's risk estimate, so the floor is
untouched by this lever. Original floored: 104/104 (100%). Recommended
floored: 104/104 (100%) — unchanged.

---

## #10 s10_short_straddle_920.py — Short Straddle 9:20

**Grid tested:** SL% in {15%, 20%, 25% current}, each still with the +5pt
buffer. New flags: `--sl-pct`, `--sl-buffer`.

| Config | Trades | Win% | Net (both yrs) | PF | Sharpe | 2022 net | 2023 net | Floored |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SL 15%+5pt | 491 | 57.6% | Rs50,955 | 1.148 | 0.87 | Rs52,020 | Rs-1,065 | 100.0% |
| SL 20%+5pt | 491 | 60.7% | Rs86,615 | 1.298 | 1.63 | Rs80,049 | Rs6,566 | 100.0% |
| **SL 25%+5pt (current)** | 491 | 60.7% | Rs68,098 | 1.239 | 1.31 | Rs60,956 | **Rs7,142** | 100.0% |

**Recommendation: keep current default (25%+5pt).** This is a case where
the pooled/in-sample picture is misleading: SL 20% looks dramatically better
overall (net Rs86,615 vs Rs68,098, driven by a much stronger 2022:
Rs80,049 vs Rs60,956) — but on 2023 specifically, the year that matters for
this decision, SL 20% is actually slightly *worse* (Rs6,566 vs Rs7,142).
Tightening further to 15% is worse in both the pool and OOS (2023 goes
negative). Per this project's evaluation discipline, a config that only wins
in-sample is not a real finding — 25%+5pt stays the pick.

**Sizing-floor check:** still 100% floored under every SL% tested, current
included — tightening the stop from 25% to 15% or 20% still leaves real
per-lot risk (roughly Rs1,900-2,900/lot across the grid — see risk_rupees in
each JSON) above the Rs2,000 (2%) budget for most weeks; it gets close at the
tightest setting (15%, avg Rs1,911/lot) but never crosses under consistently
enough to change any individual trade's outcome — every trade in the archive
still floors to 1 lot regardless. Original floored: 491/491 (100%).
Recommended (= original) floored: 491/491 (100%) — unchanged.

---

## #12 s12_straddle_breakeven_banknifty.py — Short Straddle Breakeven

**Grid tested:** uniform SL% in {15%, 20%} vs the current asymmetric 20%/23%
(CE/PE). New flags: `--ce-sl-pct`, `--pe-sl-pct`.

| Config | Trades | Win% | Net (both yrs) | PF | Sharpe | 2022 net | 2023 net | Floored |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| uniform 15%/15% | 491 | 51.3% | Rs35,412 | 1.123 | 0.76 | Rs10,699 | Rs24,714 | 100.0% |
| uniform 20%/20% | 491 | 55.4% | Rs37,056 | 1.133 | 0.79 | Rs10,144 | Rs26,912 | 100.0% |
| **asymmetric 20%/23% (current)** | 491 | 55.2% | Rs29,772 | 1.108 | 0.64 | Rs1,698 | **Rs28,075** | 100.0% |

**Recommendation: keep current default (asymmetric CE 20% / PE 23%).**
Same pattern as #10: uniform 20%/20% looks better pooled (Rs37,056 vs
Rs29,772) purely on a much stronger 2022 (Rs10,144 vs Rs1,698) — but on 2023
specifically, the current asymmetric split still wins narrowly (Rs28,075 vs
Rs26,912). The call-side/put-side asymmetry this strategy already uses (call
stop tighter than put, matching the report's own CE-loses/PE-wins pattern
across every seller in this file) is doing real, if modest, work on the
out-of-sample year — don't flatten it to a uniform stop.

**Sizing-floor check:** still 100% floored under every config tested. #12's
risk-per-unit is `max(ce_entry×CE_SL%, pe_entry×PE_SL%) × lot_size` — the
worse of the two legs' stop distance, which stays well above the Rs2,000
budget at every SL% in the grid (15-23%) since ATM NIFTY weekly straddle legs
routinely price Rs100+ each. Original floored: 491/491 (100%). Recommended
(= original) floored: 491/491 (100%) — unchanged.

---

## Summary — recommended configs

| # | Strategy | Recommendation | 2023 OOS net (recommended) | 2023 OOS net (original) | Floored (rec. vs orig.) |
|---|---|---|---:|---:|---:|
| 3 | ORB | keep current (5-min, TM 1.75) | Rs38,440 | Rs38,440 | n/a (not a sizing-floor strategy) |
| 4 | Supertrend+VWAP | **switch to (7,2)** | Rs309,491 | Rs86,930 | n/a (not a sizing-floor strategy) |
| 7 | Iron Condor | keep current (fixed-200, TP-70) | Rs20,968 | Rs20,968 | 63/63 (100%) vs 63/63 (100%) |
| 8 | Short Strangle | **switch to leg-stop 75%** | Rs17,662 | Rs15,128 | 104/104 (100%) vs 104/104 (100%) |
| 10 | Straddle 9:20 | keep current (25%+5pt) | Rs7,142 | Rs7,142 | 491/491 (100%) vs 491/491 (100%) |
| 12 | Straddle Breakeven | keep current (asym 20/23) | Rs28,075 | Rs28,075 | 491/491 (100%) vs 491/491 (100%) |

**On the sizing floor generally:** none of the twelve sweep grids across
#7/#8/#10/#12 escaped 100% flooring for even one config. The straddle-width
wing-adaptive method for #7 was the most promising lever conceptually (and
it did materially reduce the "no priced wing" skip rate, 41→29), but real
risk-per-lot for these structures on NIFTY's 50-unit lot at current premium
levels sits roughly 4-10x the Rs2,000 (2% of Rs1L) budget regardless of SL
tightness or wing width tested — consistent with REPORT.md's original
"Capital scale check" note (#4 in its Mix-and-match ideas): closing this gap
needs materially more capital (~Rs3-5L) or materially different structures,
not tighter parameters within the structures already tested.
