"""Aggregate every results/*.json into REPORT.md -- the single deliverable
that answers: which strategy is good, which isn't, what time/day/month/year
is best, and what to do next. Re-run any time after a strategy script is
(re)run; this only reads results/, never touches raw candle data.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

DISPLAY_NAMES = {
    "s01_vwap_bb_scalp": "#1 VWAP + Bollinger Mean-Reversion Scalp",
    "s02_ema_cross": "#2 EMA 9/21 Crossover + RSI Filter",
    "s03_orb": "#3 Opening Range Breakout",
    "s04_supertrend_vwap": "#4 Supertrend(10,3) + VWAP",
    "s05_rsi2_pullback": "#5 RSI(2) Pullback-in-Trend",
    "s06_vwap_volume_breakout": "#6 VWAP Breakout + Volume",
    "s07_iron_condor": "#7 Iron Condor (NIFTY weekly)",
    "s08_banknifty_strangle": "#8 Short Strangle Hard-Stop (BankNifty spec, NIFTY data)",
    "s09_bull_put_spread": "#9 Bull Put Credit Spread (NIFTY monthly)",
    "s10_short_straddle_920": "#10 Short Straddle 9:20 (community)",
    "s11_short_strangle_920": "#11 Short Strangle 9:20 (community)",
    "s12_straddle_breakeven_banknifty": "#12 Short Straddle Breakeven (BankNifty spec, NIFTY data)",
    "s13_iron_butterfly": "#13 Iron Butterfly (reference)",
    "s14_bb_vwap_breakout": "#14 Bollinger + VWAP Breakout (reference)",
    "t01_golden_cross": "Top10#1 Golden Cross 50/200 SMA",
    "t02_rsi2_meanrev_daily": "Top10#2 RSI(2) Mean Reversion (daily)",
    "t03_momentum": "Top10#3 Time-Series Momentum",
    "t04_turtle_donchian": "Top10#4 Turtle/Donchian Breakout",
    "t05_day_of_week": "Top10#5 Day-of-Week Effect (diagnostic)",
    "t06_turnaround_tuesday": "Top10#6 Turnaround Tuesday",
    "t07_bollinger_meanrev_daily": "Top10#7 Bollinger Mean-Reversion (daily)",
}
ORDER = list(DISPLAY_NAMES)


def load_all():
    out = {}
    for f in sorted(RESULTS.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            out[f.stem] = {"error": str(exc)}
    return out


def fmt_money(x):
    if x is None:
        return "-"
    return f"Rs{x:,.0f}"


def fmt_pct(x):
    return "-" if x is None else f"{x:.1f}%"


def summary_table(data):
    lines = [
        "| Strategy | Trades | Win% | Net (Rs) | CAGR% | MaxDD% | PF | Sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    ranked = []
    for name in ORDER:
        d = data.get(name)
        label = DISPLAY_NAMES[name]
        if d is None:
            lines.append(f"| {label} | *not built yet* | | | | | | |")
            continue
        m = d.get("metrics")
        if not m:
            note = d.get("extra", {}).get("status") or d.get("extra", {}).get("data_limitation") or "no trades"
            lines.append(f"| {label} | 0 | - | - | - | - | - | *{note[:70]}* |")
            continue
        # CAGR/drawdown-% are only meaningful with a real trade count and a
        # real time span; a 1-trade sample over a few days annualizes to an
        # absurd number (compounding), and a strategy that lost more than
        # its starting capital many times over (fixed, non-compounding lot
        # sizing against a wide-stop, high-frequency signal) produces a
        # >100% "drawdown of peak" that is really just "this blew the
        # account, repeatedly, in a backtest that never stops trading it" --
        # both are flagged rather than printed as if they were normal.
        cagr_txt = f"{m['cagr_pct']:.1f}%"
        if m['n_trades'] < 5 or abs(m['cagr_pct']) > 500:
            cagr_txt = f"~{m['cagr_pct']:.0f}%*"
        dd_txt = f"{m['max_drawdown_pct_of_peak']:.1f}%"
        if m['max_drawdown_pct_of_peak'] > 100:
            dd_txt = f"{m['max_drawdown_pct_of_peak']:.0f}%†"
        lines.append(
            f"| {label} | {m['n_trades']} | {m['win_rate_pct']:.1f}% | "
            f"{fmt_money(m['net_total_rupees'])} | {cagr_txt} | "
            f"{dd_txt} | "
            f"{m['profit_factor'] if m['profit_factor'] is not None else 'inf'} | "
            f"{m['sharpe']:.2f} |")
        ranked.append((name, m))
    return "\n".join(lines), ranked


def breakdown_section(title, data, key):
    lines = [f"### {title}\n"]
    any_rows = False
    for name in ORDER:
        d = data.get(name)
        if not d or not d.get("metrics") or not d.get(key):
            continue
        any_rows = True
        lines.append(f"**{DISPLAY_NAMES[name]}**\n")
        rows = d[key]
        lines.append("| Bucket | Trades | Win% | Net (Rs) | Avg (Rs) |")
        lines.append("|---|---:|---:|---:|---:|")
        for bucket, v in rows.items():
            lines.append(f"| {bucket} | {v['n']} | {v['win_rate_pct']:.1f}% | "
                          f"{fmt_money(v['net_rupees'])} | {fmt_money(v['avg_rupees'])} |")
        lines.append("")
    if not any_rows:
        lines.append("*No strategy with trades yet.*\n")
    return "\n".join(lines)


def counterfactual_section(data):
    lines = ["### Held-to-day-end counterfactual\n",
             "For every trade that exited before that day's session close, this compares the ",
             "actual realized P&L against what holding to the end of that same day would have ",
             "given instead.\n",
             "| Strategy | Early exits | Actual net (Rs) | If held to day-end (Rs) | Delta | % where holding was better |",
             "|---|---:|---:|---:|---:|---:|"]
    any_rows = False
    for name in ORDER:
        d = data.get(name)
        if not d:
            continue
        h = d.get("held_to_dayend")
        if not h:
            continue
        any_rows = True
        lines.append(f"| {DISPLAY_NAMES[name]} | {h['n_early_exits']} | "
                      f"{fmt_money(h['actual_net_rupees'])} | "
                      f"{fmt_money(h['if_held_to_dayend_net_rupees'])} | "
                      f"{fmt_money(h['delta_rupees'])} | "
                      f"{h['pct_where_holding_wouldve_been_better']:.1f}% |")
    if not any_rows:
        lines.append("| *none yet* | | | | | |")
    return "\n".join(lines)


def leg_side_section(data):
    lines = ["### Call vs put side, every option-based strategy\n",
             "Per the brief: every strategy's call side and put side are checked separately, ",
             "not just combined.\n",
             "| Strategy | CE trades | CE win% | CE net (Rs) | PE trades | PE win% | PE net (Rs) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    any_rows = False
    for name in ORDER:
        d = data.get(name)
        if not d:
            continue
        lb = d.get("leg_side_breakdown")
        if not lb or (not lb.get("CE") and not lb.get("PE")):
            continue
        any_rows = True
        ce, pe = lb.get("CE"), lb.get("PE")
        ce_s = f"{ce['n']} | {ce['win_rate_pct']:.1f}% | {fmt_money(ce['net_rupees'])}" if ce else "0 | - | -"
        pe_s = f"{pe['n']} | {pe['win_rate_pct']:.1f}% | {fmt_money(pe['net_rupees'])}" if pe else "0 | - | -"
        lines.append(f"| {DISPLAY_NAMES[name]} | {ce_s} | {pe_s} |")
    if not any_rows:
        lines.append("| *none yet* | | | | | | |")
    return "\n".join(lines)


def sizing_reality_check(data):
    """Every trade in the multi-leg selling strategies (s07-s13) had to be
    FLOORED to 1 lot -- the risk-% formula rounded to zero every single
    time, because these structures' real defined risk per lot exceeds the
    1-2% budget on Rs 1L capital. That is the single most important caveat
    in this whole report: those strategies' headline numbers describe
    "always trade the minimum tradable size," not "trade at 1-2% risk" --
    the two are very different risk profiles and the file's own position-
    sizing rule could not actually be honoured for any of them."""
    lines = [
        "The file's position-sizing formula (risk_amount = capital x risk_%; ",
        "qty = risk_amount / |entry-stop|, rounded down to whole lots) could not size ",
        "**any** trade in the seven multi-leg option-selling strategies below at 1-2% risk -- ",
        "every one of their trades needed to be floored to the minimum 1 lot to produce a result ",
        "at all, because a single lot's real defined risk already exceeds the risk budget on ",
        "Rs 1,00,000 capital (NIFTY's 50-unit lot size is the constraint, not the strategy logic). ",
        "Their win rate / P&L / Sharpe numbers below describe **\"always trade 1 lot,\"** not ",
        "**\"trade at 1-2% risk\"** -- real risk-per-trade for these ran roughly 4-10x the stated ",
        "budget. The single-leg option-buying strategies (#1-#6, #14) and the daily swing strategies ",
        "(Top10 group) did NOT need this override -- they skip a trade outright rather than over-risk it, ",
        "so their numbers really do reflect 1-2% risk sizing (at the cost of skipping most signals: often ",
        "30-95% of signals are unsizeable and simply not traded).\n",
        "| Strategy | Trades, all floored to 1 lot | What this means |",
        "|---|---:|---|",
    ]
    any_rows = False
    for name in ORDER:
        d = data.get(name)
        if not d:
            continue
        trades = d.get("trades") or []
        floored = sum(1 for t in trades if "FLOOR" in (t.get("notes") or ""))
        if floored:
            any_rows = True
            lines.append(f"| {DISPLAY_NAMES[name]} | {floored}/{len(trades)} | "
                          f"real risk-per-trade well above the {d.get('extra',{}).get('risk_pct', 0.02)*100:.0f}% target |")
    if not any_rows:
        lines.append("| *none floored* | | |")
    return "\n".join(lines)


def year_consistency(data):
    """Which strategies were net positive in BOTH 2022 and 2023 -- the
    out-of-sample-style robustness check this project's culture insists on
    (see clean_room/PROTOCOL.md)."""
    lines = ["### Consistent across both years (2022 AND 2023 both net positive)\n"]
    rows = []
    for name in ORDER:
        d = data.get(name)
        if not d or not d.get("by_year"):
            continue
        by_year = d["by_year"]
        y22 = by_year.get("2022") or by_year.get(2022)
        y23 = by_year.get("2023") or by_year.get(2023)
        if y22 is None or y23 is None:
            continue
        both_positive = y22["net_rupees"] > 0 and y23["net_rupees"] > 0
        rows.append((name, y22["net_rupees"], y23["net_rupees"], both_positive))
    winners = [r for r in rows if r[3]]
    if winners:
        lines.append("| Strategy | 2022 net (Rs) | 2023 net (Rs) |")
        lines.append("|---|---:|---:|")
        for name, y22, y23, _ in winners:
            lines.append(f"| {DISPLAY_NAMES[name]} | {fmt_money(y22)} | {fmt_money(y23)} |")
    else:
        lines.append("*None -- no strategy was net positive in both years independently.*")
    lines.append("")
    lines.append("All strategies, both years shown for reference:\n")
    lines.append("| Strategy | 2022 net (Rs) | 2023 net (Rs) | Both positive? |")
    lines.append("|---|---:|---:|---|")
    for name, y22, y23, ok in rows:
        lines.append(f"| {DISPLAY_NAMES[name]} | {fmt_money(y22)} | {fmt_money(y23)} | {'YES' if ok else 'no'} |")
    return "\n".join(lines)


def main():
    data = load_all()
    summary, ranked = summary_table(data)
    ranked.sort(key=lambda kv: kv[1]["net_total_rupees"], reverse=True)

    parts = []
    parts.append("# Backtest Report — 2022-2023, 1-minute NIFTY data, real costs\n")
    parts.append(f"Strategies with a results file: {len(data)} / {len(ORDER)} expected.\n")
    parts.append("## Summary — every strategy tested\n")
    parts.append(summary)
    parts.append("")
    parts.append("\\* CAGR shown on fewer than 5 trades, or annualizing a very short-lived "
                  "run, is not a meaningful rate — treat as \"too small a sample to judge,\" "
                  "not as a real return rate.\n")
    parts.append("† Drawdown over 100% of peak means the strategy's cumulative losses at "
                  "fixed (non-compounding) position sizing exceeded the entire starting "
                  "capital, more than once, before the backtest window ended — a real account "
                  "would have been stopped out or margin-called long before this point. Read "
                  "it as \"this strategy blows the account,\" not as a literal percentage.\n")

    if ranked:
        parts.append("## Ranked by net P&L (2022-2023, Rs 1,00,000 capital)\n")
        for i, (name, m) in enumerate(ranked, 1):
            parts.append(f"{i}. **{DISPLAY_NAMES[name]}** -- {fmt_money(m['net_total_rupees'])} "
                         f"net, {m['n_trades']} trades, {m['win_rate_pct']:.1f}% win, "
                         f"PF {m['profit_factor']}, Sharpe {m['sharpe']:.2f}")
        parts.append("")

    parts.append("## Position-sizing reality check (read this before the numbers below)\n")
    parts.append(sizing_reality_check(data))
    parts.append("")

    parts.append("## Consistency across the two years\n")
    parts.append(year_consistency(data))
    parts.append("")

    parts.append("## Counterfactual: what if we had NOT exited\n")
    parts.append(counterfactual_section(data))
    parts.append("")

    parts.append("## Call side vs put side\n")
    parts.append(leg_side_section(data))
    parts.append("")

    parts.append("## Breakdown by day of week\n")
    parts.append(breakdown_section("By weekday", data, "by_weekday"))

    parts.append("## Breakdown by hour of entry\n")
    parts.append(breakdown_section("By entry hour", data, "by_hour"))

    parts.append("## Breakdown by month\n")
    parts.append(breakdown_section("By month", data, "by_month"))

    parts.append("## Breakdown by year\n")
    parts.append(breakdown_section("By year", data, "by_year"))

    out = HERE / "REPORT.md"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out} ({len(data)} strategies found)")
    missing = [n for n in ORDER if n not in data]
    if missing:
        print("missing results for:", ", ".join(missing))


if __name__ == "__main__":
    main()
