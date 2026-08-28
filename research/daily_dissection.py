"""Full day-by-day dissection of the frozen strategy over one calendar year.

Built for the volume-enriched dataset in data_ingest/data/ -- read-only, never
writes to it. For every trading day in the requested year, records:

- every signal the strategy's underlying rule fired that day (bullish or
  bearish, whether or not it cleared the RSI 60/40 band)
- for a signal that cleared the band and became a trade: entry, exit, reason,
  P&L, MAE/MFE, and -- new, because this dataset finally has it -- volume and
  open interest at the moment of entry
- for a signal that did NOT clear the band: what it would have needed to pass,
  so the RSI filter's activity is visible even on days it blocked something
- a day-level verdict: no signal / filtered / winning trade / losing trade,
  with the concrete reason

Everything is computed from data strictly before or at the entry timestamp.
Output is one JSON file per year -- built for a report generator to consume,
not for printing 250 days of prose directly to a terminal.

STRATEGY.md is the source of truth for every parameter here. Nothing in this
script is tunable from the command line beyond which year to run, by design --
matching clean_room/evaluate.py's own rule that an analysis script must not
quietly become a search.

Usage:
    python research/daily_dissection.py --dataset data_ingest/data/nifty_forward.sqlite3 \\
        --year 2025 --out research/dissections/2025.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.candles import Candle  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import generate_signals_from_candles, run_upstox_backtest  # noqa: E402
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

# --- FROZEN. Mirrors clean_room/STRATEGY.md exactly. -------------------------
STRATEGY = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMETERS = BacktestParameters(
    bullish_rsi_min=60, bearish_rsi_max=40,
    minimum_option_premium=20, minimum_open_interest=100_000,
    stop_risk_fraction=1.6, target_return=0.30,
)
TIMEFRAME = "FIVE_MINUTE"
# --- end frozen block ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    args = parser.parse_args(argv)

    year_start = date(args.year, 1, 1)
    year_end = date(args.year, 12, 31)
    warmup_start = year_start - timedelta(days=30)

    archive = MarketArchive(args.dataset)
    archive.initialize()
    settings = _settings_for_archive(args.dataset)

    print(f"Reading (read-only) {args.dataset}", flush=True)
    print(f"Year {args.year}, warm-up from {warmup_start}", flush=True)

    with archive.connect() as con:
        rows = con.execute(
            """SELECT started_at, symbol, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source IN ('upstox','dhan') AND timeframe=?
                 AND started_at>=? AND started_at<?
               ORDER BY started_at""",
            (args.underlying_key, TIMEFRAME, warmup_start.isoformat(),
             (year_end + timedelta(days=1)).isoformat()),
        ).fetchall()
    candles = [
        Candle(symbol=str(r[1]), started_at=datetime.fromisoformat(r[0]),
               open=float(r[2]), high=float(r[3]), low=float(r[4]), close=float(r[5]))
        for r in rows
    ]
    if not candles:
        print(f"No {TIMEFRAME} underlying candles in this range. Nothing to analyse.")
        return 1
    print(f"  {len(candles):,} underlying candles loaded", flush=True)

    all_signals = generate_signals_from_candles(candles, STRATEGY)
    year_signals = [s for s in all_signals if year_start <= s.observed_at.date() <= year_end]
    print(f"  {len(year_signals)} raw signals in {args.year} "
          f"(before the RSI band; matches BULLISH/BEARISH EMA-trend agreement)", flush=True)

    result = run_upstox_backtest(
        archive, strategy=STRATEGY, start=year_start, end=year_end, settings=settings,
        parameters=PARAMETERS, underlying_key=args.underlying_key,
        timeframe=TIMEFRAME, include_dhan=True, include_derived=True,
    )
    trades_by_signal = {t.signal_at: t for t in result.trade_details}
    print(f"  {result.trades} trades survived the RSI 60/40 band, "
          f"net P&L {result.net_pnl:+,.2f}", flush=True)

    day_records: dict[str, dict] = {}
    with archive.connect() as con:
        for signal in year_signals:
            day_key = signal.observed_at.date().isoformat()
            bucket = day_records.setdefault(day_key, {"date": day_key, "signals": []})
            trade = trades_by_signal.get(signal.observed_at)
            rsi = signal.rsi if signal.rsi is not None else None
            band_ok = (
                rsi is not None and (
                    (signal.signal == "BULLISH" and rsi >= PARAMETERS.bullish_rsi_min)
                    or (signal.signal == "BEARISH" and rsi <= PARAMETERS.bearish_rsi_max)
                )
            )
            entry = {
                "time": signal.observed_at.strftime("%H:%M"),
                "direction": signal.signal,
                "rsi": round(rsi, 1) if rsi is not None else None,
                "atr": round(signal.atr, 2) if signal.atr else None,
                "confidence": round(signal.confidence, 3),
                "spot": round(signal.spot, 2),
                "band_cleared": band_ok,
                "traded": trade is not None,
            }
            if trade is not None:
                session_end = datetime.combine(trade.entry_at.date(), settings.force_exit,
                                               tzinfo=trade.entry_at.tzinfo).isoformat()
                path = con.execute(
                    """SELECT started_at, high, low, close, open_interest, volume
                       FROM market_candles WHERE instrument_token=? AND timeframe=?
                         AND started_at>=? AND started_at<=? ORDER BY started_at""",
                    (trade.token, TIMEFRAME, trade.entry_at.isoformat(), session_end),
                ).fetchall()
                entry_row = con.execute(
                    """SELECT open_interest, volume FROM market_candles
                       WHERE instrument_token=? AND timeframe=? AND started_at=?""",
                    (trade.token, TIMEFRAME, trade.entry_at.isoformat()),
                ).fetchone()
                # Read-only fallback: FIVE_MINUTE.volume can be unpopulated on a
                # dataset resampled before 2026-08-28's fix (dhan_ingest.py's
                # resample never carried volume through). Never re-resample or
                # write to the dataset to fix this -- sum the real ONE_MINUTE
                # rows for this bucket instead, which needs no write at all.
                entry_volume = float(entry_row[1]) if entry_row and entry_row[1] else None
                if entry_volume is None:
                    bucket_end = (trade.entry_at + timedelta(minutes=5)).isoformat()
                    minute_sum = con.execute(
                        """SELECT SUM(volume) FROM market_candles
                           WHERE instrument_token=? AND timeframe='ONE_MINUTE'
                             AND started_at>=? AND started_at<?""",
                        (trade.token, trade.entry_at.isoformat(), bucket_end),
                    ).fetchone()
                    entry_volume = float(minute_sum[0]) if minute_sum and minute_sum[0] else None
                during = [p for p in path if p[0] <= trade.exit_at.isoformat()]
                mae = min(float(p[2]) for p in during) / trade.entry_price - 1 if during else 0.0
                mfe = max(float(p[1]) for p in during) / trade.entry_price - 1 if during else 0.0
                entry.update({
                    "symbol": trade.symbol,
                    "entry_price": trade.entry_price,
                    "stop_price": trade.stop_price,
                    "exit_price": trade.exit_price,
                    "exit_reason": trade.exit_reason,
                    "net_pnl": trade.net_pnl,
                    "won": trade.net_pnl > 0,
                    "mae_pct": round(mae * 100, 2),
                    "mfe_pct": round(mfe * 100, 2),
                    "entry_open_interest": float(entry_row[0]) if entry_row and entry_row[0] else None,
                    "entry_volume": entry_volume,
                })
            bucket["signals"].append(entry)

    # Verdict + one-line lesson per day, mechanical rather than free-text --
    # a human/LLM report pass adds prose on top of this, but the classification
    # itself must be reproducible, not narrated differently each time.
    for bucket in day_records.values():
        traded = [s for s in bucket["signals"] if s["traded"]]
        filtered = [s for s in bucket["signals"] if not s["band_cleared"]]
        if traded:
            pnl = sum(s["net_pnl"] for s in traded)
            wins = sum(1 for s in traded if s["won"])
            if pnl > 0:
                verdict = "WIN"
                lesson = (f"{wins}/{len(traded)} winning; kept as taken."
                          if wins == len(traded) else
                          f"net positive ({wins}/{len(traded)} won) -- mixed but the RSI-cleared side worked.")
            else:
                shaken = [s for s in traded if s["exit_reason"] in ("stop", "stop-gap")
                         and s["mfe_pct"] > 5]
                if shaken:
                    lesson = (f"stopped at MAE {shaken[0]['mae_pct']}% then reached "
                              f"+{shaken[0]['mfe_pct']}% before session end -- stop distance, not the signal.")
                else:
                    lesson = "stopped and the option never recovered -- the signal itself was wrong here."
                verdict = "LOSS"
        elif filtered:
            worst = min(filtered, key=lambda s: abs((s["rsi"] or 50) - 50))
            verdict = "FILTERED"
            gap = 60 - worst["rsi"] if worst["direction"] == "BULLISH" else worst["rsi"] - 40
            lesson = f"RSI {worst['rsi']} was {abs(gap):.1f} points short of the {worst['direction']} band -- correctly skipped."
        else:
            verdict = "NO_SIGNAL"
            lesson = "no EMA-trend agreement all day."
        bucket["verdict"] = verdict
        bucket["lesson"] = lesson
        bucket["day_pnl"] = round(sum(s.get("net_pnl", 0.0) for s in traded), 2)

    ordered = [day_records[k] for k in sorted(day_records)]
    weekly: dict[str, dict] = defaultdict(lambda: {"days": [], "pnl": 0.0, "trades": 0, "wins": 0})
    monthly: dict[str, dict] = defaultdict(lambda: {"days": [], "pnl": 0.0, "trades": 0, "wins": 0})
    for day in ordered:
        d = date.fromisoformat(day["date"])
        week_key = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        month_key = d.strftime("%Y-%m")
        traded = [s for s in day["signals"] if s["traded"]]
        for bucket_map, key in ((weekly, week_key), (monthly, month_key)):
            b = bucket_map[key]
            b["days"].append(day["date"])
            b["pnl"] = round(b["pnl"] + day["day_pnl"], 2)
            b["trades"] += len(traded)
            b["wins"] += sum(1 for s in traded if s["won"])

    payload = {
        "year": args.year,
        "strategy": "RSI 60/40 (clean_room/STRATEGY.md)",
        "dataset": args.dataset,
        "raw_signals": len(year_signals),
        "trades": result.trades,
        "net_pnl": result.net_pnl,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "days": ordered,
        "weekly": dict(sorted(weekly.items())),
        "monthly": dict(sorted(monthly.items())),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}  ({len(ordered)} days with at least one signal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
