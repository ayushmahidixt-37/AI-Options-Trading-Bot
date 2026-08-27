"""Why did the strategy take those trades? A minute-level anatomy of one day.

`day_comparison.py` showed 2021-03-02 losing six times in a row on alternating
directions. That says *what* happened. This asks *why*: what the underlying was
doing, what each signal saw at the moment it fired, and what the option did
after each stop. A rule derived from a guess about chop would be another blind
filter; a rule derived from the actual indicator values at the moment of each
mistake is at least aimed at the real failure.

Prints, for the chosen day:

- The underlying's 5-minute path, marked where each signal fired, so the shape
  of the day is visible rather than assumed.
- Each signal's own indicator readings (RSI, ATR, EMA gap, confidence) as the
  strategy saw them, plus the gap in minutes since the previous signal and
  whether it reversed direction.
- What the traded option did after its stop -- whether the stop was the right
  call or the position was shaken out of a move that continued.
- The day's range against a trailing 20-day baseline, which is the difference
  between "the market went nowhere" and "the market moved and we were wrong".

Measurement only. Nothing here changes a strategy.

Usage:
    python research/day_anatomy.py --archive .termux-data/market-data.sqlite3 --day 2021-03-02
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _settings_for_archive
from options_bot.candles import Candle
from options_bot.market_archive import MarketArchive
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy
from options_bot.upstox_backtest import generate_signals_from_candles, run_upstox_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--day", default="2021-03-02")
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.day)
    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    # Warm-up window: the macro EMA needs real history before the day itself.
    warm_start = day - timedelta(days=30)
    with archive.connect() as con:
        rows = con.execute(
            """SELECT started_at, symbol, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source IN ('upstox','dhan') AND timeframe=?
                 AND derived_from_timeframe IS NULL
                 AND date(started_at)>=? AND date(started_at)<=?
               ORDER BY started_at""",
            (NIFTY_UNDERLYING_KEY, args.timeframe, warm_start.isoformat(), day.isoformat()),
        ).fetchall()
    candles = [
        Candle(symbol=str(r[1]), started_at=datetime.fromisoformat(r[0]),
               open=float(r[2]), high=float(r[3]), low=float(r[4]), close=float(r[5]))
        for r in rows
    ]
    today = [c for c in candles if c.started_at.date() == day]
    if not today:
        print(f"No underlying candles for {day}.")
        return 1

    hi = max(c.high for c in today)
    lo = min(c.low for c in today)
    op, cl = today[0].open, today[-1].close
    day_range = (hi - lo) / lo * 100
    net_move = (cl - op) / op * 100

    prior_days: dict[date, list[Candle]] = {}
    for c in candles:
        if c.started_at.date() < day:
            prior_days.setdefault(c.started_at.date(), []).append(c)
    ranges = [
        (max(x.high for x in cs) - min(x.low for x in cs)) / min(x.low for x in cs) * 100
        for cs in prior_days.values() if cs
    ][-20:]
    baseline = sum(ranges) / len(ranges) if ranges else float("nan")

    print(f"ANATOMY OF {day}  ({args.timeframe})")
    print("=" * 112)
    print(f"  NIFTY open {op:,.2f}  high {hi:,.2f}  low {lo:,.2f}  close {cl:,.2f}")
    print(f"  day range {day_range:.2f}%   net move {net_move:+.2f}%   "
          f"trailing 20-day mean range {baseline:.2f}%")
    if abs(net_move) < day_range / 3:
        print("  -> the market travelled a lot and finished near where it started: CHOP, not trend.")
    else:
        print("  -> the market made real directional progress; losses are not simply chop.")

    signals = [s for s in generate_signals_from_candles(candles, CANDIDATE_B)
               if s.observed_at.date() == day]
    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B, start=day, end=day, settings=settings,
        parameters=PARAMS, underlying_key=NIFTY_UNDERLYING_KEY,
        timeframe=args.timeframe, include_dhan=True, include_derived=True,
    )
    traded = {t.signal_at: t for t in result.trade_details}

    print(f"\n  {len(signals)} signals fired, {result.trades} became trades, "
          f"day P&L {result.net_pnl:+,.2f}")
    print("\nEVERY SIGNAL, AND WHAT IT SAW AT THE TIME")
    print("-" * 112)
    print(f"{'time':<8}{'signal':<9}{'spot':>10}{'rsi':>7}{'atr':>8}{'conf':>7}"
          f"{'gap_min':>9}{'reversed':>10}{'traded':>8}{'P&L':>11}{'exit':>14}")
    prev = None
    for s in signals:
        gap = "-" if prev is None else f"{(s.observed_at - prev.observed_at).total_seconds() / 60:.0f}"
        rev = "-" if prev is None else ("YES" if prev.signal != s.signal else "no")
        t = traded.get(s.observed_at)
        print(f"{s.observed_at.strftime('%H:%M'):<8}{s.signal:<9}{s.spot:>10,.2f}"
              f"{(s.rsi or 0):>7.1f}{s.atr:>8.2f}{s.confidence:>7.2f}{gap:>9}{rev:>10}"
              f"{('yes' if t else 'no'):>8}"
              f"{(f'{t.net_pnl:+,.2f}' if t else '-'):>11}"
              f"{(t.exit_reason if t else '-'):>14}")
        prev = s

    print("\nWAS EACH STOP THE RIGHT CALL? (option's own path after we were stopped)")
    print("-" * 112)
    print(f"{'time':<8}{'symbol':<24}{'entry':>8}{'stop':>8}{'best after stop':>17}{'close':>9}{'verdict':>22}")
    force_exit = settings.force_exit
    with archive.connect() as con:
        for t in sorted(result.trade_details, key=lambda x: x.entry_at):
            session_end = datetime.combine(t.entry_at.date(), force_exit,
                                           tzinfo=t.entry_at.tzinfo).isoformat()
            after = con.execute(
                """SELECT MAX(high), (SELECT close FROM market_candles
                       WHERE instrument_token=? AND timeframe=? AND started_at>? AND started_at<=?
                       ORDER BY started_at DESC LIMIT 1)
                   FROM market_candles
                   WHERE instrument_token=? AND timeframe=? AND started_at>? AND started_at<=?""",
                (t.token, args.timeframe, t.exit_at.isoformat(), session_end,
                 t.token, args.timeframe, t.exit_at.isoformat(), session_end),
            ).fetchone()
            if not after or after[0] is None:
                verdict, best, close_px = "no data after exit", float("nan"), float("nan")
            else:
                best, close_px = float(after[0]), float(after[1] or 0)
                best_pct = (best / t.entry_price - 1) * 100
                verdict = ("shaken out (recovered)" if best_pct > 5
                           else "stop was correct" if best_pct < 0
                           else "marginal")
                best = best_pct
            print(f"{t.entry_at.strftime('%H:%M'):<8}{t.symbol:<24}{t.entry_price:>8.2f}"
                  f"{t.stop_price:>8.2f}{best:>16.1f}%{close_px:>9.2f}{verdict:>22}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
