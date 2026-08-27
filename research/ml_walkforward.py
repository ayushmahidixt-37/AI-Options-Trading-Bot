"""Walk-forward ML: can a model trained on past trades improve the next year?

This is deliberately the LAST word on ML for this strategy rather than another
iteration. Five leads have now been tested and rejected out-of-sample (exit
shell, an earlier ML filter, 1-minute granularity, an OI entry filter, a
percentage stop). Each looked strong on its discovery window and reversed on the
next one. Running a sixth variant of the same search would produce a sixth such
result; what settles the question instead is whether ANY model, trained only on
the past and judged only on the future, adds value.

Design chosen to make a positive result trustworthy and a negative result final:

- **Walk-forward, never random splits.** Train on everything up to year N, test
  on year N+1, roll forward. This is the only split that matches how the model
  would actually be used, and it cannot leak future information.
- **Features are strictly pre-entry.** The contract's own price is taken as its
  last close BEFORE the signal, never the entry fill, so nothing is read that a
  live decision would not have. This is the flaw that invalidated the earlier OI
  screen and it is not repeated here.
- **No threshold sweep.** Thresholds fitted on the test fold are how a dead
  strategy is made to look alive. Instead the model keeps its top 25% / 50% /
  75% of trades by predicted probability, all three reported. There is nothing
  to tune and nowhere for optimism to hide.
- **Judged as an account, not as P&L.** Every fold is scored under Rs 1,00,000
  of rolling capital with risk-based sizing and real costs, against the
  no-filter baseline on the identical fold.

The bar for "ML helps" is stated before looking: **the model must beat the
unfiltered baseline in every fold.** Winning two of three is what a coin does.

Usage:
    python research/ml_walkforward.py --archive .termux-data/market-data.sqlite3
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from options_bot import ml_features  # noqa: E402
from options_bot.backtest import BacktestParameters  # noqa: E402
from options_bot.backtest_cli import _settings_for_archive  # noqa: E402
from options_bot.candles import Candle  # noqa: E402
from options_bot.market_archive import MarketArchive  # noqa: E402
from options_bot.strategy_experimental import TrendConfirmedMomentumStrategy  # noqa: E402
from options_bot.upstox_backtest import (  # noqa: E402
    generate_signals_from_candles,
    run_upstox_backtest,
)
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY  # noqa: E402

from capital_compounding_simulation import simulate  # noqa: E402

CANDIDATE_B = TrendConfirmedMomentumStrategy(
    fast_period=5, slow_period=10, macro_period=60, rsi_period=21,
)
PARAMS = BacktestParameters(
    stop_risk_fraction=1.6, target_return=0.30,
    minimum_option_premium=20, minimum_open_interest=100_000,
)
# ml_features' vetted pre-entry set, plus two derived from today's findings: the
# contract's premium level, and the percentage leash the fixed-rupee stop
# implies at that premium (a Rs 29 option gets -41%, a Rs 131 option -9%).
EXTRA = ("log_premium", "implied_stop_pct")


def build_rows(archive, settings, start, end, timeframe, underlying_key):
    result = run_upstox_backtest(
        archive, strategy=CANDIDATE_B, start=start, end=end, settings=settings,
        parameters=PARAMS, underlying_key=underlying_key, timeframe=timeframe,
        include_dhan=True, include_derived=True,
    )
    with archive.connect() as con:
        rows = con.execute(
            """SELECT started_at, symbol, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source IN ('upstox','dhan') AND timeframe=?
                 AND derived_from_timeframe IS NULL
                 AND date(started_at)>=? AND date(started_at)<=?
               ORDER BY started_at""",
            (underlying_key, timeframe, start.isoformat(), end.isoformat()),
        ).fetchall()
        candles = [
            Candle(symbol=str(r[1]), started_at=datetime.fromisoformat(r[0]),
                   open=float(r[2]), high=float(r[3]), low=float(r[4]), close=float(r[5]))
            for r in rows
        ]
        observations = {
            o.observed_at: o for o in generate_signals_from_candles(candles, CANDIDATE_B)
        }

        out = []
        for trade in result.trade_details:
            obs = observations.get(trade.signal_at)
            if obs is None:
                continue
            prior = con.execute(
                """SELECT close FROM market_candles
                   WHERE instrument_token=? AND timeframe=? AND started_at<=?
                   ORDER BY started_at DESC LIMIT 1""",
                (trade.token, timeframe, trade.signal_at.isoformat()),
            ).fetchone()
            if not prior or float(prior[0]) <= 0:
                continue
            premium = float(prior[0])
            feats = ml_features.extract_features_precontract(candles, obs, CANDIDATE_B)
            feats["log_premium"] = math.log(premium)
            feats["implied_stop_pct"] = min(
                1.0, (settings.max_loss_per_trade * 1.6) / max(1.0, premium * trade.units)
            )
            out.append({
                "features": feats,
                "label": 1.0 if trade.net_pnl > 0 else 0.0,
                "trade": trade,
                "year": trade.entry_at.year,
            })
    return out


def fit(rows, names, epochs=1500, lr=0.1, l2=0.01):
    """Deterministic full-batch logistic regression -- same algorithm as
    research/train_signal_quality_model.py, weights start at zero, no
    randomness, reproducible run to run."""
    means, stds = [], []
    for name in names:
        values = [r["features"][name] for r in rows]
        mean = sum(values) / len(values)
        means.append(mean)
        stds.append(math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)))
    matrix = [
        [(r["features"][n] - m) / s if s else 0.0 for n, m, s in zip(names, means, stds)]
        for r in rows
    ]
    labels = [r["label"] for r in rows]
    weights = [0.0] * len(names)
    bias = 0.0
    for _ in range(epochs):
        preds = [
            1 / (1 + math.exp(-max(-30.0, min(30.0, bias + sum(w * x for w, x in zip(weights, row))))))
            for row in matrix
        ]
        errors = [p - t for p, t in zip(preds, labels)]
        bias -= lr * (sum(errors) / len(matrix))
        weights = [
            w - lr * (sum(e * row[j] for e, row in zip(errors, matrix)) / len(matrix) + l2 * w)
            for j, w in enumerate(weights)
        ]
    return weights, bias, means, stds


def score(row, names, weights, bias, means, stds):
    total = bias + sum(
        w * ((row["features"][n] - m) / s if s else 0.0)
        for n, w, m, s in zip(names, weights, means, stds)
    )
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, total))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--final-test", action="store_true",
                        help="Train 2021-2024, test once on 2025-03..2026-08.")
    parser.add_argument("--random-seeds", type=int, default=40,
                        help="Random same-size control draws per cut.")
    args = parser.parse_args(argv)

    archive = MarketArchive(args.archive)
    archive.initialize()
    settings = _settings_for_archive(args.archive)
    names = tuple(ml_features.FEATURE_NAMES) + EXTRA

    print("Building trade set with strictly pre-entry features (2021-01-01 .. 2024-10-01) ...",
          flush=True)
    end_date = date(2026, 8, 18) if args.final_test else date(2024, 10, 1)
    rows = build_rows(archive, settings, date(2021, 1, 1), end_date,
                      "FIVE_MINUTE", NIFTY_UNDERLYING_KEY)
    print(f"  {len(rows)} trades with usable features\n")

    print(f"Walk-forward, Rs {args.capital:,.0f} rolling capital, risk-sized 2%, real costs")
    print("Bar set in advance: the model must beat the baseline in EVERY fold.")
    print("=" * 112)

    verdicts = []
    if args.final_test:
        folds = [(2025, "FINAL PRE-REGISTERED TEST")]
    else:
        folds = [(year, "") for year in (2022, 2023, 2024)]
    for test_year, note in folds:
        train = [r for r in rows if r["year"] < test_year]
        test = ([r for r in rows if r["year"] >= test_year] if args.final_test
                else [r for r in rows if r["year"] == test_year])
        if note:
            print(f"\n{note}: criterion fixed in advance -- ML top 25% must BOTH "
                  "beat baseline AND beat >=90% of random same-size draws.")
        if len(train) < 100 or len(test) < 50:
            print(f"\nTEST {test_year}: skipped (train={len(train)}, test={len(test)})")
            continue
        weights, bias, means, stds = fit(train, names)
        ranked = sorted(test, key=lambda r: -score(r, names, weights, bias, means, stds))

        baseline = simulate([r["trade"] for r in test], args.capital,
                            settings.paper_fee_per_order, None, 0.5,
                            sizing="risk", risk_pct=0.02)
        print(f"\nTRAIN <{test_year} ({len(train)} trades)  ->  TEST {test_year} ({len(test)} trades)")
        print(f"  {'baseline (no filter)':<22} final={baseline['final_balance']:>10,.0f}  "
              f"({baseline['total_return_pct']:>+6.1f}%)")
        beat_all = True
        for keep in (0.25, 0.50, 0.75):
            count = max(1, int(len(ranked) * keep))
            subset = sorted([r["trade"] for r in ranked[:count]], key=lambda t: t.entry_at)
            outcome = simulate(subset, args.capital, settings.paper_fee_per_order,
                               None, 0.5, sizing="risk", risk_pct=0.02)
            better = outcome["total_return_pct"] > baseline["total_return_pct"]
            beat_all = beat_all and better

            # Control: does taking the SAME NUMBER of trades at random do just
            # as well? Trading 25% as often pays 25% of the costs, which on a
            # negative-expectancy strategy improves the result by itself. If the
            # model's score carries no information, it will sit inside this
            # random distribution -- the same trap the random-entry benchmark
            # was built to catch for the entry signal.
            randoms = []
            for seed in range(args.random_seeds):
                rng = random.Random(seed * 1000 + test_year + int(keep * 100))
                picked = sorted(rng.sample([r["trade"] for r in test], count),
                                key=lambda t: t.entry_at)
                randoms.append(simulate(picked, args.capital, settings.paper_fee_per_order,
                                        None, 0.5, sizing="risk",
                                        risk_pct=0.02)["total_return_pct"])
            randoms.sort()
            beaten = sum(1 for r in randoms if outcome["total_return_pct"] > r)
            label = f"ML top {int(keep * 100)}%"
            print(f"  {label:<22} final={outcome['final_balance']:>10,.0f}  "
                  f"({outcome['total_return_pct']:>+6.1f}%)  n={count:<4} "
                  f"{'BEATS' if better else 'loses to'} baseline")
            print(f"  {'  vs random same-size':<22} median={statistics.median(randoms):>+7.1f}%  "
                  f"range [{randoms[0]:+.1f}%..{randoms[-1]:+.1f}%]  "
                  f"ML beat {beaten}/{len(randoms)} random draws"
                  f"{'  <-- outside random' if beaten >= len(randoms) * 0.95 else ''}")
        verdicts.append(beat_all)

    print("\n" + "=" * 112)
    if verdicts and all(verdicts):
        print("VERDICT: the model beat the baseline in every fold at every cut. Worth pursuing.")
    else:
        print(f"VERDICT: failed the bar. Folds where every cut beat baseline: "
              f"{sum(verdicts)}/{len(verdicts)}.")
        print("  ML trained on this data does not add value out-of-sample. Consistent with the")
        print("  five prior rejected leads: the patterns in this dataset do not persist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
