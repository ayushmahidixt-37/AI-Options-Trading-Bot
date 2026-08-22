"""Durable, appendable record of every backtest run's capital/ROI figures.

Separate concern from ``research_ledger.py`` (src/options_bot): that module
enforces which date ranges have been spent, to prevent overfitting. This
module just remembers what every run actually made or lost in rupees, so
``build_roi_ledger_html.py`` can regenerate the full run-history table
without anyone having to hand-reconstruct it after the fact (as happened
once, 2026-08-22 -- the first ~160 rows here were rebuilt from scratch
scripts after the fact because nothing had been recording this as it ran).

Usage from any backtest script:

    import sys
    sys.path.insert(0, "research")
    from roi_ledger import record_run

    result = run_upstox_backtest(archive, strategy=strategy, start=start, end=end, ...)
    record_run("My experiment group", "some-label", start, end, result)

Call this right after every ``run_upstox_backtest`` / ``run_upstox_ml_backtest``
/ ``run_upstox_ml_backtest_v2`` call whose result is worth keeping -- not
required for pure exploration you intend to throw away.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

LEDGER_PATH = Path(__file__).parent / "roi_all_runs.json"


def record_run(group: str, label: str, start: date, end: date, result, *, path: Path = LEDGER_PATH) -> None:
    """Append one backtest run's summary metrics to the durable ROI ledger.

    ``result`` is any ``BacktestResult`` -- from ``run_upstox_backtest``,
    ``run_upstox_ml_backtest``, or ``run_upstox_ml_backtest_v2``, all of
    which return the same type. Appends, never overwrites; safe to call
    from many separate script runs over time.
    """
    rows = []
    if path.exists():
        rows = json.loads(path.read_text())
    rows.append({
        "group": group,
        "label": label,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "trades": result.trades,
        "win_rate": result.win_rate,
        "net_pnl": result.net_pnl,
        "drawdown": result.max_drawdown,
        "profit_factor": result.profit_factor,
        "invested": result.capital_deployed_total,
        "roi_pct": result.return_on_capital_pct,
        "trading_days": result.trading_days,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    })
    path.write_text(json.dumps(rows, indent=2))
