"""Train the ML signal-quality filter (Windows-dev-machine-only).

Reads the local archive, builds one training row per raw momentum signal in
the Development range (features from ``ml_features.extract_features_precontract``,
label from whether the *existing, unmodified* conservative-fill/fee/stop
simulation (``run_upstox_backtest``) turned that signal into a net-winning
trade), fits a hand-rolled logistic regression (no numpy/scikit-learn -- see
``ml_model.py``'s docstring for why), picks a decision threshold against the
Validation range only, and exports a small JSON weights file.

Never touches live/forward-paper execution paths. Records development/
validation usage in the same range-usage ledger every other candidate this
project has ever used goes through -- never a test attempt, since none is
being spent here.

Usage:
    python research/train_signal_quality_model.py \\
        --archive .termux-data/market-data.sqlite3 \\
        --candidate ml-signal-quality-v1 \\
        --base-params-json '{"stop_risk_fraction":1.6,"target_return":0.50,"trailing_stop":0.20}' \\
        --dev-start 2026-01-01 --dev-end 2026-03-31 \\
        --val-start 2026-04-01 --val-end 2026-05-31 \\
        --out research/models/ml-signal-quality-v1.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import replace
from datetime import date, datetime

from options_bot import ml_features, ml_model
from options_bot.backtest import BacktestParameters
from options_bot.backtest_cli import _params_from_json, _settings_for_archive
from options_bot.candles import Candle
from options_bot.market_archive import MarketArchive
from options_bot.research_ledger import (
    UsageRole,
    compute_params_fingerprint,
    initialize_ledger,
    record_usage,
)
from options_bot.strategy import MomentumStrategy
from options_bot.upstox_backtest import generate_signals_from_candles, run_upstox_backtest
from options_bot.upstox_ml_backtest import run_upstox_ml_backtest
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

_FILTER_FIELDS = (
    "bullish_rsi_min",
    "bearish_rsi_max",
    "minimum_atr",
    "entry_start",
    "entry_end",
    "exclude_expiry_day",
    "allowed_weekdays",
)


def _fetch_candles(
    archive: MarketArchive,
    start: date,
    end: date,
    underlying_key: str,
    timeframe: str,
    include_derived: bool = False,
) -> list[Candle]:
    """Same query ``upstox_backtest.py``/``upstox_ml_backtest.py`` use -- duplicated
    deliberately rather than importing a private helper from either, keeping
    this training script's only coupling to those modules their public API.
    ``include_derived`` mirrors those modules' parameter of the same name."""
    derived_filter = "" if include_derived else " AND derived_from_timeframe IS NULL"
    with archive.connect() as con:
        rows = con.execute(
            f"""SELECT started_at, symbol, open, high, low, close FROM market_candles
               WHERE instrument_token=? AND source='upstox' AND timeframe=?{derived_filter}
                 AND date(started_at)>=? AND date(started_at)<=?
               ORDER BY started_at""",
            (underlying_key, timeframe, start.isoformat(), end.isoformat()),
        ).fetchall()
    return [
        Candle(
            symbol=str(row[1]),
            started_at=datetime.fromisoformat(row[0]),
            open=float(row[2]),
            high=float(row[3]),
            low=float(row[4]),
            close=float(row[5]),
        )
        for row in rows
    ]


def _build_training_rows(
    candles: list[Candle],
    observations: list,
    trades: tuple,
    strategy: MomentumStrategy,
) -> tuple[list[dict[str, float]], list[float]]:
    observation_by_time = {observation.observed_at: observation for observation in observations}
    features_rows: list[dict[str, float]] = []
    labels: list[float] = []
    for trade in trades:
        observation = observation_by_time.get(trade.signal_at)
        if observation is None:
            continue
        features_rows.append(ml_features.extract_features_precontract(candles, observation, strategy))
        labels.append(1.0 if trade.net_pnl > 0 else 0.0)
    return features_rows, labels


def _standardize_stats(rows: list[dict[str, float]], feature_names: tuple[str, ...]) -> tuple[list[float], list[float]]:
    means = []
    stds = []
    for name in feature_names:
        values = [row[name] for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean)
        stds.append(math.sqrt(variance))
    return means, stds


def _train_logistic_regression(
    rows: list[dict[str, float]],
    labels: list[float],
    feature_names: tuple[str, ...],
    means: list[float],
    stds: list[float],
    epochs: int,
    learning_rate: float,
    l2: float,
) -> tuple[list[float], float]:
    """Deterministic full-batch gradient descent (weights start at zero, no
    randomness) -- reproducible run-to-run given the same training rows."""
    n = len(rows)
    standardized = [
        [(row[name] - mean) / std if std else 0.0 for name, mean, std in zip(feature_names, means, stds)]
        for row in rows
    ]
    weights = [0.0] * len(feature_names)
    bias = 0.0
    for _ in range(epochs):
        predictions = [
            1.0 / (1.0 + math.exp(-(bias + sum(w * x for w, x in zip(weights, row)))))
            for row in standardized
        ]
        errors = [prediction - label for prediction, label in zip(predictions, labels)]
        gradient_bias = sum(errors) / n
        gradient_weights = [
            sum(error * row[j] for error, row in zip(errors, standardized)) / n + l2 * weights[j]
            for j in range(len(weights))
        ]
        bias -= learning_rate * gradient_bias
        weights = [w - learning_rate * g for w, g in zip(weights, gradient_weights)]
    return weights, bias


def _result_summary(result) -> dict:
    return {
        "trades": result.trades,
        "win_rate": result.win_rate,
        "net_pnl": result.net_pnl,
        "max_drawdown": result.max_drawdown,
        "profit_factor": result.profit_factor,
    }


def _select_threshold(
    archive: MarketArchive,
    model_shell: ml_model.SignalQualityModel,
    strategy: MomentumStrategy,
    settings,
    base_params: BacktestParameters,
    val_start: date,
    val_end: date,
    underlying_key: str,
    timeframe: str,
    threshold_min: float,
    threshold_max: float,
    threshold_step: float,
    min_trades: int,
    include_derived: bool = False,
    dev_rows: list[dict[str, float]] | None = None,
    min_dev_trades: int | None = None,
) -> tuple[float, dict]:
    """Sweep a fixed, coarse grid against the validation range only, picking
    the threshold that maximizes validation net P&L subject to a minimum
    trade-count floor (mirrors ``upstox_analysis.py``'s own 20-trade floor).

    Runs the real ``run_upstox_ml_backtest`` engine once per candidate
    threshold rather than post-hoc filtering an already-built unfiltered
    trade list -- the latter would reuse exit boundaries computed against
    the *unfiltered* observation sequence, hitting exactly the correctness
    trap ``upstox_ml_backtest.py``'s own docstring warns about (a
    rejected-at-a-different-threshold signal would still silently act as
    another trade's premature exit trigger).

    If ``dev_rows``/``min_dev_trades`` are given, a threshold is only
    eligible when it *also* keeps at least ``min_dev_trades`` of the
    development rows -- closing the gap v2-bigdata's failure exposed
    (2026-08-21 entry): a threshold chosen purely on validation trade count
    can still collapse development down to a handful of trades, the same
    "strong validation, thin development" shape this project's own
    discipline flags as unreliable everywhere else. This check only counts
    how many already-labeled development rows would score above the
    threshold -- cheap (no backtest replay), and safe precisely because it
    only counts, never recomputes P&L/exit boundaries (the trap the
    docstring above warns about is specific to recomputing trade outcomes,
    not to counting which rows a threshold keeps).
    """
    best_threshold = 0.5
    best_result = None
    threshold = threshold_min
    total_steps = int(round((threshold_max - threshold_min) / threshold_step)) + 1
    step = 0
    sweep_start = time.time()
    while threshold <= threshold_max + 1e-9:
        step += 1
        candidate_model = replace(model_shell, threshold=threshold)
        dev_kept = (
            sum(1 for row in dev_rows if candidate_model.score(row) >= threshold)
            if dev_rows is not None
            else None
        )
        dev_floor_met = min_dev_trades is None or (dev_kept is not None and dev_kept >= min_dev_trades)
        result = run_upstox_ml_backtest(
            archive,
            model=candidate_model,
            strategy=strategy,
            start=val_start,
            end=val_end,
            settings=settings,
            parameters=base_params,
            underlying_key=underlying_key,
            timeframe=timeframe,
            include_derived=include_derived,
        )
        if (
            result.trades >= min_trades
            and dev_floor_met
            and (best_result is None or result.net_pnl > best_result.net_pnl)
        ):
            best_result = result
            best_threshold = threshold
        elapsed = time.time() - sweep_start
        eta = (elapsed / step) * (total_steps - step)
        dev_note = f" dev_kept={dev_kept}" if dev_kept is not None else ""
        print(
            f"  threshold sweep: {threshold:.2f} -> trades={result.trades} net_pnl={result.net_pnl:.2f}{dev_note} "
            f"[{100 * step / total_steps:5.1f}% done, step {step}/{total_steps}, ETA {eta:.0f}s]",
            flush=True,
        )
        threshold = round(threshold + threshold_step, 10)
    metadata = (
        _result_summary(best_result)
        if best_result is not None
        else {
            "reason": f"no threshold in [{threshold_min},{threshold_max}] met the {min_trades}-trade "
            f"validation floor and (if set) the {min_dev_trades}-trade development floor"
        }
    )
    return best_threshold, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--base-params-json", required=True)
    parser.add_argument("--dev-start", required=True)
    parser.add_argument("--dev-end", required=True)
    parser.add_argument("--val-start", required=True)
    parser.add_argument("--val-end", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    parser.add_argument("--epochs", type=int, default=2000)
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument("--l2", type=float, default=0.01)
    parser.add_argument(
        "--threshold-min", type=float, default=0.05,
        help="v3's default of 0.30 turned out to cut off the actual optimum (found at 0.25 "
             "on the extended-history archive, 2026-08-22 'v4' entry in BACKTEST_FINDINGS.md) "
             "-- default lowered so a future run doesn't repeat that mistake.",
    )
    parser.add_argument("--threshold-max", type=float, default=0.70)
    parser.add_argument("--threshold-step", type=float, default=0.02)
    parser.add_argument("--min-validation-trades", type=int, default=20)
    parser.add_argument(
        "--min-development-trades", type=int, default=None,
        help="If set, a threshold is only eligible when it also keeps at least this many "
             "development-range rows -- closes the 'strong validation, thin development' gap "
             "v2-bigdata's failure exposed (2026-08-21 entry).",
    )
    parser.add_argument(
        "--include-derived", action="store_true",
        help="Include candles resampled from 1-minute data (derived_from_timeframe set) "
             "in addition to real Upstox data -- needed for any range touching "
             "2024-10-03 to 2025-12-31, where the underlying index has no real "
             "coverage at all. See BACKTEST_FINDINGS.md's 2026-08-22 entry.",
    )
    args = parser.parse_args(argv)

    base_params = _params_from_json(args.base_params_json)
    set_filter_fields = [f for f in _FILTER_FIELDS if getattr(base_params, f) not in (None, False)]
    if set_filter_fields:
        print(
            json.dumps(
                {
                    "error": "invalid_base_params",
                    "reason": "--base-params-json must be an unfiltered exit-shell -- filter "
                    f"fields must stay unset for correct label generation: {set_filter_fields!r}",
                },
                indent=2,
            )
        )
        return 1

    archive = MarketArchive(args.archive)
    archive.initialize()
    initialize_ledger(archive)
    settings = _settings_for_archive(args.archive)
    strategy = MomentumStrategy()

    dev_start, dev_end = date.fromisoformat(args.dev_start), date.fromisoformat(args.dev_end)
    val_start, val_end = date.fromisoformat(args.val_start), date.fromisoformat(args.val_end)
    if not (dev_start <= dev_end < val_start <= val_end):
        print(json.dumps({"error": "invalid_ranges"}, indent=2))
        return 1

    print(f"fetching development candles {dev_start} to {dev_end} ...", flush=True)
    dev_candles = _fetch_candles(
        archive, dev_start, dev_end, args.underlying_key, args.timeframe, args.include_derived
    )
    dev_observations = generate_signals_from_candles(dev_candles, strategy)
    print(f"running unfiltered development backtest ({len(dev_candles)} candles) ...", flush=True)
    t0 = time.time()
    dev_result = run_upstox_backtest(
        archive, strategy, dev_start, dev_end, settings, base_params, args.underlying_key, args.timeframe,
        include_derived=args.include_derived,
    )
    print(f"  done in {time.time() - t0:.0f}s: {dev_result.trades} trades, net_pnl={dev_result.net_pnl:.2f}", flush=True)
    dev_rows, dev_labels = _build_training_rows(dev_candles, dev_observations, dev_result.trade_details, strategy)
    if len(dev_rows) < args.min_validation_trades:
        print(
            json.dumps(
                {"error": "insufficient_training_data", "reason": f"only {len(dev_rows)} labeled development trades"},
                indent=2,
            )
        )
        return 1

    means, stds = _standardize_stats(dev_rows, ml_features.FEATURE_NAMES)
    weights, bias = _train_logistic_regression(
        dev_rows, dev_labels, ml_features.FEATURE_NAMES, means, stds, args.epochs, args.learning_rate, args.l2
    )

    model_shell = ml_model.SignalQualityModel(
        feature_names=ml_features.FEATURE_NAMES,
        means=tuple(means),
        stds=tuple(stds),
        weights=tuple(weights),
        bias=bias,
        threshold=0.5,
        metadata={},
    )
    print(f"sweeping thresholds [{args.threshold_min}, {args.threshold_max}] against validation range ...", flush=True)
    threshold, threshold_metadata = _select_threshold(
        archive,
        model_shell,
        strategy,
        settings,
        base_params,
        val_start,
        val_end,
        args.underlying_key,
        args.timeframe,
        args.threshold_min,
        args.threshold_max,
        args.threshold_step,
        args.min_validation_trades,
        args.include_derived,
        dev_rows,
        args.min_development_trades,
    )

    print(f"recomputing development metrics at chosen threshold {threshold} ...", flush=True)
    dev_metrics_at_chosen_threshold = run_upstox_ml_backtest(
        archive,
        model=replace(model_shell, threshold=threshold),
        strategy=strategy,
        start=dev_start,
        end=dev_end,
        settings=settings,
        parameters=base_params,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        include_derived=args.include_derived,
    )

    positive_rate = sum(dev_labels) / len(dev_labels)
    metadata = {
        "candidate_name": args.candidate,
        "trained_at": datetime.now().isoformat(),
        "base_params_json": args.base_params_json,
        "dev_range": [args.dev_start, args.dev_end],
        "val_range": [args.val_start, args.val_end],
        "include_derived": args.include_derived,
        "training_row_count": len(dev_rows),
        "positive_rate": positive_rate,
        "hyperparameters": {
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
            "l2": args.l2,
        },
        "dev_metrics_unfiltered_baseline": {
            "trades": dev_result.trades,
            "win_rate": dev_result.win_rate,
            "net_pnl": dev_result.net_pnl,
            "max_drawdown": dev_result.max_drawdown,
            "profit_factor": dev_result.profit_factor,
        },
        "dev_metrics_at_chosen_threshold": _result_summary(dev_metrics_at_chosen_threshold),
        "val_metrics_at_chosen_threshold": threshold_metadata,
    }
    model = ml_model.SignalQualityModel(
        feature_names=ml_features.FEATURE_NAMES,
        means=tuple(means),
        stds=tuple(stds),
        weights=tuple(weights),
        bias=bias,
        threshold=threshold,
        metadata=metadata,
    )
    ml_model.save(model, args.out)

    model_bytes = open(args.out, "rb").read()
    fingerprint_source = json.dumps(
        {"base_params": json.loads(args.base_params_json), "model_sha256": hashlib.sha256(model_bytes).hexdigest()},
        sort_keys=True,
    )
    fingerprint = compute_params_fingerprint(fingerprint_source)
    record_usage(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.DEVELOPMENT,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=dev_start,
        end=dev_end,
        params_fingerprint=fingerprint,
        notes=f"ML training: {len(dev_rows)} rows, positive_rate={positive_rate:.3f}",
    )
    record_usage(
        archive,
        candidate_name=args.candidate,
        role=UsageRole.VALIDATION,
        underlying_key=args.underlying_key,
        timeframe=args.timeframe,
        start=val_start,
        end=val_end,
        params_fingerprint=fingerprint,
        notes=f"ML threshold selection: threshold={threshold}, {json.dumps(threshold_metadata)}",
    )

    print(json.dumps({"model_path": args.out, "threshold": threshold, "metadata": metadata}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
