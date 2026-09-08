"""Drive portfolio.py over the six 'keep' strategies with shared rolling
capital and a concurrency cap. Run with no args for the default manifest
(baseline configs, max 5 concurrent, max 2/strategy); pass --manifest to
point at a different json manifest (e.g. once better-tuned configs exist
under results/sweeps/).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio as P  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

# Production manifest -- adopted configs after the parameter sweep + sizing-
# bug fix (see PHASE2_REPORT.md): #4 and #8 use their improved parameters,
# the rest keep their original (already out-of-sample-confirmed) defaults.
SWEEPS = RESULTS / "sweeps"
DEFAULT_MANIFEST = [
    {"strategy": "s03_orb", "path": str(RESULTS / "s03_orb.json"), "allow_floor": False},
    {"strategy": "s04_supertrend_vwap_v2", "path": str(SWEEPS / "s04_supertrend_vwap_v2_period7_mult2.json"), "allow_floor": False},
    {"strategy": "s07_iron_condor", "path": str(RESULTS / "s07_iron_condor.json"), "allow_floor": True},
    {"strategy": "s08_banknifty_strangle_v2", "path": str(SWEEPS / "s08_banknifty_strangle_v2_legstop75.json"), "allow_floor": True},
    {"strategy": "s10_short_straddle_920", "path": str(RESULTS / "s10_short_straddle_920.json"), "allow_floor": True},
    {"strategy": "s12_straddle_breakeven_banknifty", "path": str(RESULTS / "s12_straddle_breakeven_banknifty.json"), "allow_floor": True},
]

# Full-history manifest (2020-08-03..2026-08-28) -- #8 dropped: its edge did
# not hold over the full history (PF 1.01, 49.6% max drawdown -- the 2022-2023
# window was not representative for it). See PHASE3_REPORT.md.
FULL_HISTORY_MANIFEST = [
    {"strategy": "s03_orb", "path": str(SWEEPS / "s03_orb_full_history.json"), "allow_floor": False},
    {"strategy": "s04_supertrend_vwap", "path": str(SWEEPS / "s04_supertrend_vwap_full_history.json"), "allow_floor": False},
    {"strategy": "s07_iron_condor", "path": str(SWEEPS / "s07_iron_condor_full_history.json"), "allow_floor": True},
    {"strategy": "s10_short_straddle_920", "path": str(SWEEPS / "s10_short_straddle_920_full_history.json"), "allow_floor": True},
    {"strategy": "s12_straddle_breakeven_banknifty", "path": str(SWEEPS / "s12_straddle_breakeven_banknifty_full_history.json"), "allow_floor": True},
]

# Mix-and-match: dedicate the first ~45 minutes to ORB (its own natural
# window per the spec: signals fire off the 9:15-9:30 opening range, taken
# through ~10:00), hand the rest of the session to Supertrend (a
# trend-following strategy that showed no losing hour in the original
# report and was strongest from midday on) instead of letting both compete
# for the same slots all day.
import datetime as _dt
TIME_GATED_MANIFEST = [
    {**DEFAULT_MANIFEST[0], "time_filter": (_dt.time(9, 0), _dt.time(10, 0))},
    {**DEFAULT_MANIFEST[1], "time_filter": (_dt.time(10, 0), _dt.time(15, 30))},
    *DEFAULT_MANIFEST[2:],
]


def run(manifest, capital, max_concurrent, max_per_strategy, label, max_qty_lots_per_trade=None,
        compounding=True):
    candidates = P.load_candidates(manifest)
    result = P.simulate(candidates, capital, max_concurrent, max_per_strategy,
                         max_qty_lots_per_trade, compounding)
    m = P.portfolio_metrics(result, capital)
    print(f"\n=== {label} (max_concurrent={max_concurrent}, max/strategy={max_per_strategy}) ===")
    print(f"  candidates: {len(candidates)} | fills: {m['n_fills']} | "
          f"skipped (no slot): {m['n_skipped_no_slot']} | skipped (qty=0): {m['n_skipped_zero_qty']}")
    print(f"  win {m['win_rate_pct']}% | net Rs{m['net_total_rupees']:,.0f} | "
          f"final equity Rs{m['final_equity']:,.0f} | PF {m['profit_factor']} | "
          f"maxDD {m['max_drawdown_pct_of_peak']}% | CAGR {m['cagr_pct']}% | Sharpe {m['sharpe']}")
    print(f"  missed-due-to-no-slot, at original sizing: Rs{m['missed_due_to_no_slot_original_sizing_net_rupees']:,.0f} "
          f"over {m['missed_due_to_no_slot_count']} candidates")
    for strat, s in sorted(m["by_strategy"].items()):
        print(f"    {strat:<32} {s['n']:>5} fills | win {s['win_rate_pct']:>5.1f}% | net Rs{s['net_rupees']:>10,.0f}")

    # monthly / yearly running-equity series, for charting -- equity as of
    # the end of each calendar month/year (last fill-close within it).
    by_month, by_year = {}, {}
    for ts, eq in result["equity_curve"]:
        by_month[ts.strftime("%Y-%m")] = eq
        by_year[ts.year] = eq
    monthly_series = [{"month": k, "equity": round(v, 2)} for k, v in sorted(by_month.items())]
    yearly_series = [{"year": k, "equity": round(v, 2)} for k, v in sorted(by_year.items())]

    out_path = RESULTS / f"portfolio_{label}.json"
    payload = {
        "label": label, "capital": capital, "max_concurrent": max_concurrent,
        "max_per_strategy": max_per_strategy, "metrics": m,
        "monthly_equity": monthly_series, "yearly_equity": yearly_series,
        "fills": [{"strategy": f.strategy, "entry_at": f.entry_at.isoformat(),
                    "exit_at": f.exit_at.isoformat(), "qty_lots": f.qty_lots,
                    "net_rupees": round(f.net_rupees, 2)} for f in result["fills"]],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  -> {out_path}")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=P.E.CAPITAL)
    ap.add_argument("--max-concurrent", type=int, default=5)
    ap.add_argument("--max-per-strategy", type=int, default=2)
    ap.add_argument("--which", choices=["two_year", "full_history", "both"], default="full_history")
    args = ap.parse_args()

    if args.which in ("two_year", "both"):
        run(DEFAULT_MANIFEST, args.capital, args.max_concurrent, args.max_per_strategy,
            "pooled_fixed_capital_no_compounding", compounding=False)
        run(DEFAULT_MANIFEST, args.capital, args.max_concurrent, args.max_per_strategy,
            "pooled_realistic_20lot_cap", max_qty_lots_per_trade=20)
        run(DEFAULT_MANIFEST, args.capital, args.max_concurrent, args.max_per_strategy,
            "pooled_uncapped_compounding")
        run(DEFAULT_MANIFEST, args.capital, 1, 1, "single_position_baseline", max_qty_lots_per_trade=20)
        run(TIME_GATED_MANIFEST, args.capital, args.max_concurrent, args.max_per_strategy,
            "time_gated_orb_am_supertrend_pm", max_qty_lots_per_trade=20)
        run(TIME_GATED_MANIFEST, args.capital, args.max_concurrent, args.max_per_strategy,
            "time_gated_fixed_capital_no_compounding", compounding=False)

    if args.which in ("full_history", "both"):
        # Rolling capital, NOT reinvested -- fixed (non-compounding) sizing
        # against the same Rs 1L pool throughout, per the brief. This is the
        # primary full-history deliverable.
        run(FULL_HISTORY_MANIFEST, args.capital, args.max_concurrent, args.max_per_strategy,
            "full_history_fixed_capital", compounding=False)
        run(FULL_HISTORY_MANIFEST, args.capital, 1, 1,
            "full_history_single_position", compounding=False)


if __name__ == "__main__":
    main()
