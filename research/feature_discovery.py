"""What in this data actually predicts a profitable trade? Every derivable feature, ranked.

Earlier work tested filters one at a time, mostly on hunches about which
variable might matter. This inventories every feature derivable from the archive
that is knowable at entry, computes all of them for a large trade sample, and
ranks them by whether a threshold on that feature could actually separate
winners from losers.

Ranking uses **quintile P&L**, not a comparison of group medians. Comparing
medians is what made open interest look like a strong signal (winners 6.57M
against losers 4.20M) when a threshold on it turned out to be worthless -- the
distributions overlapped so heavily that any cut removed winners and losers in
similar proportion. Quintiles answer the question that actually matters: if the
trades are sorted by this feature, does P&L vary systematically across the
range, and is there a region worth trading? A monotonic gradient across quintiles
is evidence; a single good quintile surrounded by noise is not.

Every feature is causal -- computed only from bars strictly before the entry
timestamp, or from contract metadata known in advance. The sequence features
(trades so far today, running P&L today, minutes since last signal) use only
trades that had already closed.

Features covered:
  underlying   rsi, atr, atr_normalized, ema_gap, distance from macro EMA,
               day range so far, gap from previous close, minutes since open,
               day of week, realized volatility 5d/20d
  contract     entry premium, days to expiry, moneyness, open interest,
               OI change pre-entry, implied volatility, IV change pre-entry
  sequence     trades already taken today, realised P&L today, minutes since
               the previous signal, whether the previous trade was stopped

Measurement only -- this proposes nothing and changes nothing.

Usage:
    python research/feature_discovery.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.candles import Candle  # noqa: E402
from options_bot.indicators import ema  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import (  # noqa: E402
    generate_signals_from_candles,
    run_upstox_backtest,
)
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
BASE = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--start", default="2020-08-03")
    parser.add_argument("--end", default="2024-10-01")
    parser.add_argument("--lookback", type=int, default=12, help="Bars before entry for OI/IV trend.")
    args = parser.parse_args(argv)

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)

    print(f"Feature discovery {start}..{end}", flush=True)
    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
        parameters=BASE, underlying_key=NIFTY_UNDERLYING_KEY,
        timeframe="FIVE_MINUTE", include_dhan=True, include_derived=True,
    )
    print(f"{result.trades} trades, net {result.net_pnl:+,.0f}", flush=True)

    with archive.connect() as con:
        rows = con.execute(
            """SELECT started_at, symbol, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source IN ('upstox','dhan') AND timeframe='FIVE_MINUTE'
                 AND derived_from_timeframe IS NULL AND date(started_at)>=? AND date(started_at)<=?
               ORDER BY started_at""",
            (NIFTY_UNDERLYING_KEY, start.isoformat(), end.isoformat()),
        ).fetchall()
        candles = [Candle(symbol=str(r[1]), started_at=datetime.fromisoformat(r[0]),
                          open=float(r[2]), high=float(r[3]), low=float(r[4]), close=float(r[5]))
                   for r in rows]
        observations = {o.observed_at: o for o in generate_signals_from_candles(candles, CANDIDATE_B)}
        closes = [c.close for c in candles]
        ema_macro = ema(closes, CANDIDATE_B.macro_period)
        macro_at = {c.started_at: ema_macro[i] for i, c in enumerate(candles)}

        by_day: dict[date, list[Candle]] = defaultdict(list)
        for c in candles:
            by_day[c.started_at.date()].append(c)
        day_list = sorted(by_day)
        prev_close = {}
        daily_ret = []
        rvol = {}
        for i, d in enumerate(day_list):
            if i:
                p = by_day[day_list[i - 1]][-1].close
                prev_close[d] = p
                daily_ret.append((by_day[d][-1].close - p) / p)
            window5, window20 = daily_ret[-5:], daily_ret[-20:]
            rvol[d] = (statistics.pstdev(window5) if len(window5) > 1 else 0.0,
                       statistics.pstdev(window20) if len(window20) > 1 else 0.0)

        contracts = {}
        for token, strike, expiry in con.execute(
                "SELECT token, strike, expiry FROM instruments WHERE underlying='NIFTY'"):
            contracts[token] = (float(strike), date.fromisoformat(expiry))

        records = []
        seq_day: dict[date, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "last": None, "stopped": 0})
        for trade in sorted(result.trade_details, key=lambda t: t.entry_at):
            obs = observations.get(trade.signal_at)
            day = trade.entry_at.date()
            st = seq_day[day]
            prior = con.execute(
                """SELECT open_interest, implied_volatility, close FROM market_candles
                   WHERE instrument_token=? AND timeframe='FIVE_MINUTE' AND started_at<?
                   ORDER BY started_at DESC LIMIT ?""",
                (trade.token, trade.entry_at.isoformat(), args.lookback),
            ).fetchall()
            oi = [float(p[0]) for p in prior if p[0] is not None]
            iv = [float(p[1]) for p in prior if p[1] is not None]
            strike, expiry = contracts.get(trade.token, (None, None))
            spot = obs.spot if obs else None
            today_bars = [c for c in by_day[day] if c.started_at <= trade.entry_at]
            f = {
                "rsi": obs.rsi if obs and obs.rsi else None,
                "atr": obs.atr if obs else None,
                "atr_norm": (obs.atr / obs.spot) if obs and obs.spot else None,
                "confidence": obs.confidence if obs else None,
                "dist_from_macro": ((obs.spot - macro_at[trade.signal_at]) / obs.spot
                                    if obs and trade.signal_at in macro_at and obs.spot else None),
                "day_range_so_far": ((max(c.high for c in today_bars) - min(c.low for c in today_bars))
                                     / min(c.low for c in today_bars) if today_bars else None),
                "gap_prev_close": ((today_bars[0].open - prev_close[day]) / prev_close[day]
                                   if today_bars and day in prev_close else None),
                "minutes_since_open": (trade.entry_at.hour * 60 + trade.entry_at.minute) - 555,
                "day_of_week": float(trade.entry_at.weekday()),
                "rvol_5d": rvol.get(day, (None, None))[0],
                "rvol_20d": rvol.get(day, (None, None))[1],
                "entry_premium": trade.entry_price,
                "log_premium": math.log(trade.entry_price) if trade.entry_price > 0 else None,
                "days_to_expiry": float((expiry - day).days) if expiry else None,
                "moneyness": ((strike - spot) / spot if strike and spot else None),
                "abs_moneyness": (abs(strike - spot) / spot if strike and spot else None),
                "open_interest": oi[0] if oi else None,
                "oi_change": ((oi[0] - oi[-1]) / oi[-1] if len(oi) >= 2 and oi[-1] else None),
                "implied_vol": iv[0] if iv else None,
                "iv_change": ((iv[0] - iv[-1]) / iv[-1] if len(iv) >= 2 and iv[-1] else None),
                "trades_today_before": float(st["n"]),
                "pnl_today_before": st["pnl"],
                "mins_since_last_signal": ((trade.entry_at - st["last"]).total_seconds() / 60
                                           if st["last"] else None),
                "prev_was_stop": float(st["stopped"]),
            }
            records.append({"f": f, "pnl": trade.net_pnl, "won": trade.net_pnl > 0})
            st["n"] += 1
            st["pnl"] += trade.net_pnl
            st["last"] = trade.entry_at
            st["stopped"] = 1.0 if trade.exit_reason in ("stop", "stop-gap") else 0.0

    print(f"\nComputed {len(records[0]['f'])} features for {len(records)} trades")
    print("=" * 128)
    print("\nQUINTILE P&L PER FEATURE  (sorted low->high; a monotonic gradient is real signal)")
    print(f"\n{'feature':<24}{'Q1':>13}{'Q2':>13}{'Q3':>13}{'Q4':>13}{'Q5':>13}"
          f"{'spread':>13}{'mono':>6}")
    print("-" * 128)

    scored = []
    for name in records[0]["f"]:
        usable = [r for r in records if r["f"][name] is not None]
        if len(usable) < 250:
            continue
        usable.sort(key=lambda r: r["f"][name])
        size = len(usable) // 5
        quints = [usable[i * size:(i + 1) * size] if i < 4 else usable[4 * size:] for i in range(5)]
        pnls = [sum(r["pnl"] for r in q) for q in quints]
        spread = max(pnls) - min(pnls)
        deltas = [pnls[i + 1] - pnls[i] for i in range(4)]
        mono = "yes" if all(d > 0 for d in deltas) or all(d < 0 for d in deltas) else "no"
        scored.append((spread, name, pnls, mono, quints))

    for spread, name, pnls, mono, _q in sorted(scored, key=lambda x: -x[0]):
        cells = "".join(f"{p:>+13,.0f}" for p in pnls)
        print(f"{name:<24}{cells}{spread:>13,.0f}{mono:>6}")

    print("\n\nBEST QUINTILE PER FEATURE (what a filter on that feature could capture)")
    print(f"\n{'feature':<24}{'best Q':>8}{'range':>34}{'n':>7}{'P&L':>13}{'win%':>8}")
    print("-" * 128)
    for spread, name, pnls, mono, quints in sorted(scored, key=lambda x: -max(x[2])):
        best = max(range(5), key=lambda i: pnls[i])
        q = quints[best]
        lo = q[0]["f"][name]
        hi = q[-1]["f"][name]
        wins = sum(1 for r in q if r["won"])
        rng = f"[{lo:,.4g} .. {hi:,.4g}]"
        print(f"{name:<24}{'Q' + str(best + 1):>8}{rng:>34}{len(q):>7}"
              f"{pnls[best]:>+13,.0f}{wins / len(q) * 100:>7.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
