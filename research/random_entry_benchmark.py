"""Does Candidate B's entry signal beat random entries? The null-hypothesis test.

After Candidate B was rejected at two independent granularities (2026-08-26),
one question decides whether *any* further indicator tuning is worth doing: is
the signal contributing anything at all? If entries chosen at random, run
through the identical exit shell, filters, contract selection and costs,
perform the same as Candidate B, then the EMA/RSI/macro-trend logic adds
nothing and no amount of parameter work on it will ever help.

Method. Two null models, each run over many seeds so Candidate B is compared
against a *distribution* rather than one lucky or unlucky draw:

- **shuffled-direction**: signals fire at a matched rate but the BULLISH /
  BEARISH choice is a coin flip. Isolates directional skill specifically.
- **random-entry**: the same, which under this engine is the same null (the
  engine only records a signal when direction *changes*, so timing and
  direction are not separable here without rewriting the engine). Kept as a
  second independent seed set rather than pretending it tests something else.

Everything downstream of the signal is held identical: contract selection,
`minimum_option_premium`, `minimum_open_interest`, the stop/target shell, lot
size, fees and slippage. Only the source of the direction changes.

Comparison metric is **return on capital deployed**, not absolute P&L: random
runs will not produce exactly the same trade count, and return-on-capital
normalises for both trade count and position size. Absolute P&L and trade
counts are printed too so any mismatch is visible rather than hidden.

Reading the result: if Candidate B's return-on-capital sits *inside* the
random distribution, the signal has no demonstrable skill. If it sits clearly
outside (better), the signal is doing something real even if too weak to pay
its costs.

Usage:
    python research/random_entry_benchmark.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import random
import statistics
from datetime import date

from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.strategy import Direction, Signal
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy
from options_bot.upstox_backtest import run_upstox_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)
START, END = date(2021, 1, 1), date(2021, 12, 31)


class RandomStrategy:
    """Emits a random direction with probability ``rate``, otherwise nothing.

    Deliberately exposes ``signal_from_indicators`` so
    ``generate_signals_from_candles`` takes its fast vectorised path -- the
    fallback path recomputes RSI from scratch on every candle, which is
    quadratic and would make a multi-seed study impractical. The indicator
    values handed in are ignored on purpose: that is the whole point.

    Attribute names and ``minimum_candles`` mirror
    TrendConfirmedMomentumStrategy so the engine treats both identically,
    including how many leading candles are skipped for warm-up.
    """

    fast_period = 5
    slow_period = 10
    rsi_period = 21
    atr_period = 14
    minimum_candles = 70

    def __init__(self, seed: int, rate: float) -> None:
        self._rng = random.Random(seed)
        self._rate = rate

    def signal_from_indicators(self, fast, slow, momentum, volatility) -> Signal | None:
        if self._rng.random() >= self._rate:
            return None
        direction = self._rng.choice((Direction.BULLISH, Direction.BEARISH))
        return Signal(direction, 0.6, volatility or 0.0, "random")


def run(archive, settings, strategy, underlying_key, timeframe) -> dict:
    result = run_upstox_backtest(
        archive, strategy=strategy, start=START, end=END, settings=settings,
        parameters=PARAMS, underlying_key=underlying_key, timeframe=timeframe,
        include_dhan=True, include_derived=True,
    )
    return {
        "trades": result.trades,
        "net_pnl": result.net_pnl,
        "roc": result.return_on_capital_pct,
        "profit_factor": result.profit_factor,
        "win_rate": result.win_rate * 100 if result.trades else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    parser.add_argument("--seeds", type=int, default=12)
    parser.add_argument(
        "--rate", type=float, default=0.074,
        help="Per-candle probability of emitting a random signal. Default targets "
             "roughly Candidate B's own trade count on 5-minute 2021 (~696).",
    )
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    ctx = (args.underlying_key, args.timeframe)

    print(f"NULL-HYPOTHESIS TEST -- {START}..{END} on {args.timeframe}")
    print("Everything after the signal is identical; only the direction source changes.")
    print("=" * 96)

    real = run(archive, settings, CANDIDATE_B, *ctx)
    print(f"\nCANDIDATE B      trades={real['trades']:>5} win={real['win_rate']:>5.1f}% "
          f"net={real['net_pnl']:>12,.2f} ROC={real['roc']:>7.2f}% PF={real['profit_factor']:.3f}")

    print(f"\nRANDOM ENTRIES ({args.seeds} seeds, rate={args.rate})")
    randoms = []
    for seed in range(1, args.seeds + 1):
        metrics = run(archive, settings, RandomStrategy(seed, args.rate), *ctx)
        randoms.append(metrics)
        print(f"  seed {seed:>2}        trades={metrics['trades']:>5} win={metrics['win_rate']:>5.1f}% "
              f"net={metrics['net_pnl']:>12,.2f} ROC={metrics['roc']:>7.2f}% "
              f"PF={metrics['profit_factor']:.3f}", flush=True)

    rocs = sorted(m["roc"] for m in randoms if m["roc"] is not None)
    if not rocs:
        print("\nNo random run produced trades -- lower --rate or widen the range.")
        return 1

    mean_roc = statistics.fmean(rocs)
    stdev_roc = statistics.stdev(rocs) if len(rocs) > 1 else 0.0
    better = sum(1 for r in rocs if r >= real["roc"])

    print("\n" + "=" * 96)
    print(f"Random return-on-capital: min {rocs[0]:.2f}%  mean {mean_roc:.2f}%  max {rocs[-1]:.2f}%"
          f"  stdev {stdev_roc:.2f}")
    print(f"Candidate B:              {real['roc']:.2f}%")
    print(f"Random seeds matching or beating Candidate B: {better}/{len(rocs)}")
    if stdev_roc:
        print(f"Candidate B sits {(real['roc'] - mean_roc) / stdev_roc:+.2f} standard deviations "
              f"from the random mean.")

    inside = rocs[0] <= real["roc"] <= rocs[-1]
    print()
    if inside:
        print("VERDICT: Candidate B falls INSIDE the random distribution.")
        print("  The entry signal shows no demonstrable skill. Further indicator tuning on it")
        print("  is not justified -- the problem is the approach, not the parameters.")
    else:
        side = "above" if real["roc"] > rocs[-1] else "below"
        print(f"VERDICT: Candidate B falls OUTSIDE the random distribution ({side} every seed).")
        print("  The signal is doing something real. Whether it is large enough to pay the")
        print("  ~0.95% round-trip cost floor is a separate question -- see the 2026-08-26 entry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
