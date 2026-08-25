"""Train the short strangle's daily ML entry filter, then run a genuine,
never-touched fresh-data confirmation comparing it against both the
unconditional baseline and the existing hand-tuned opening-range filter.

Mirrors ``research/train_signal_quality_model.py``'s methodology exactly
(a development range trains the logistic regression, a validation range
picks the decision threshold, and a disjoint fresh range -- never used
for either -- is the only number that gets trusted) -- see that script's
docstring for the full rationale. What differs is the decision being
learned: Candidate B's model scores individual intrabar signals; this one
scores whether to sell today's strangle at all, once per trading day,
using ``short_strangle_ml_features``'s day-level features.
``opening_range_pct`` -- the existing hand-tuned filter's entire basis --
is itself one of those features, so this model is a strict generalization
of the manual cutoff, not an unrelated second filter stacked on top of it.

Labels come from the real ``run_short_strangle_backtest`` engine run
unconditionally (``maximum_opening_range_pct=None``, ``ml_model=None``) --
never a re-implementation of its P&L/exit logic. Features are computed
independently by re-walking the same archived daily candles (duplicated
deliberately, matching ``train_signal_quality_model.py``'s own precedent
of talking to the archive directly rather than reaching into a backtest
engine's private internals), then matched to each labeled trade by
calendar day.

Usage:
    python research/train_short_strangle_ml_model.py \\
        --archive .termux-data/market-data.sqlite3 \\
        --candidate short-strangle-ml-v1 \\
        --dev-start 2020-08-03 --dev-end 2023-04-30 \\
        --val-start 2023-05-01 --val-end 2024-10-01 \\
        --fresh-start 2025-03-01 --fresh-end 2026-08-18 \\
        --dev-val-include-dhan \\
        --out research/models/short-strangle-ml-v1.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import replace
from datetime import date, datetime

from options_bot import ml_model as ml_model_module
from options_bot import short_strangle_ml_features
from options_bot.backtest_cli import _settings_for_archive
from options_bot.market_archive import MarketArchive
from options_bot.research_ledger import (
    UsageRole,
    compute_params_fingerprint,
    initialize_ledger,
    record_usage,
)
from options_bot.short_premium_backtest import (
    DEFAULT_ENTRY_TIME,
    ShortStrangleParameters,
    run_short_strangle_backtest,
)
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY


def _fetch_daily_rows(
    archive: MarketArchive,
    start: date,
    end: date,
    underlying_key: str,
    timeframe: str,
    include_dhan: bool,
    include_derived: bool = False,
) -> dict[str, list[tuple[str, float, float, float]]]:
    """Same underlying query ``run_short_strangle_backtest`` uses, grouped
    by day -- duplicated deliberately rather than importing a private
    helper, keeping this script's only coupling to that module its public
    ``run_short_strangle_backtest`` API (matches
    ``train_signal_quality_model.py``'s own precedent)."""
    derived_filter = "" if include_derived else " AND derived_from_timeframe IS NULL"
    source_clause = "source IN ('upstox','dhan')" if include_dhan else "source='upstox'"
    with archive.connect() as con:
        rows = con.execute(
            f"""SELECT started_at, close, high, low FROM market_candles
               WHERE instrument_token=? AND {source_clause} AND timeframe=?{derived_filter}
                 AND date(started_at)>=? AND date(started_at)<=?
               ORDER BY started_at""",
            (underlying_key, timeframe, start.isoformat(), end.isoformat()),
        ).fetchall()
    by_day: dict[str, list[tuple[str, float, float, float]]] = {}
    for started_at, close, high, low in rows:
        by_day.setdefault(started_at[:10], []).append((started_at, float(close), float(high), float(low)))
    return by_day


def _build_training_rows(
    archive: MarketArchive,
    by_day: dict[str, list[tuple[str, float, float, float]]],
    variant: ShortStrangleParameters,
    label_by_day: dict[str, float],
) -> tuple[list[dict[str, float]], list[float]]:
    features_rows: list[dict[str, float]] = []
    labels: list[float] = []
    prior_close: float | None = None
    trailing_daily_returns: list[float] = []
    with archive.connect() as con:
        for day, day_rows in sorted(by_day.items()):
            day_rows.sort()
            day_close = day_rows[-1][1]
            previous_close = prior_close
            if previous_close is not None:
                trailing_daily_returns.append((day_close - previous_close) / previous_close)
            prior_close = day_close

            if day not in label_by_day:
                continue
            entry_row = next(
                (row for row in day_rows if datetime.fromisoformat(row[0]).time() >= variant.entry_time),
                None,
            )
            if entry_row is None:
                continue
            spot = entry_row[1]
            opening_bars = day_rows[: variant.opening_range_bars]
            if len(opening_bars) < variant.opening_range_bars:
                continue
            range_high = max(r[2] for r in opening_bars)
            range_low = min(r[3] for r in opening_bars)
            if range_low <= 0:
                continue
            expiry_row = con.execute(
                "SELECT MIN(expiry) FROM instruments WHERE underlying='NIFTY' AND expiry>=date(?)",
                (day,),
            ).fetchone()
            if not expiry_row or not expiry_row[0]:
                continue
            days_to_expiry = (date.fromisoformat(expiry_row[0]) - date.fromisoformat(day)).days
            features = short_strangle_ml_features.extract_features(
                entry_day=date.fromisoformat(day),
                range_high=range_high,
                range_low=range_low,
                days_to_expiry=days_to_expiry,
                prior_close=previous_close,
                entry_spot=spot,
                trailing_daily_returns=trailing_daily_returns,
            )
            features_rows.append(features)
            labels.append(label_by_day[day])
    return features_rows, labels


def _standardize_stats(rows: list[dict[str, float]], feature_names: tuple[str, ...]) -> tuple[list[float], list[float]]:
    means, stds = [], []
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
    """Deterministic full-batch gradient descent -- identical algorithm to
    ``train_signal_quality_model.py``'s (weights start at zero, no
    randomness, reproducible run-to-run)."""
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
        "premium_collected_total": result.premium_collected_total,
        "return_on_premium_pct": result.return_on_premium_pct,
    }


def _select_threshold(
    archive: MarketArchive,
    model_shell: ml_model_module.SignalQualityModel,
    settings,
    variant: ShortStrangleParameters,
    val_start: date,
    val_end: date,
    underlying_key: str,
    timeframe: str,
    include_dhan: bool,
    include_derived: bool,
    threshold_min: float,
    threshold_max: float,
    threshold_step: float,
    min_trades: int,
    dev_rows: list[dict[str, float]],
    min_dev_trades: int | None,
) -> tuple[float, dict]:
    """Sweep a fixed grid against the validation range only, maximizing net
    P&L subject to a minimum trade-count floor on both validation and (if
    set) development -- mirrors ``train_signal_quality_model.py``'s
    ``_select_threshold`` exactly, including the same "strong validation,
    thin development" guard."""
    best_threshold = 0.5
    best_result = None
    threshold = threshold_min
    total_steps = int(round((threshold_max - threshold_min) / threshold_step)) + 1
    step = 0
    sweep_start = time.time()
    while threshold <= threshold_max + 1e-9:
        step += 1
        candidate_model = replace(model_shell, threshold=threshold)
        dev_kept = sum(1 for row in dev_rows if candidate_model.score(row) >= threshold)
        dev_floor_met = min_dev_trades is None or dev_kept >= min_dev_trades
        result = run_short_strangle_backtest(
            archive, start=val_start, end=val_end, settings=settings, parameters=variant,
            underlying_key=underlying_key, timeframe=timeframe, include_dhan=include_dhan,
            include_derived=include_derived, ml_model=candidate_model,
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
        print(
            f"  threshold sweep: {threshold:.2f} -> trades={result.trades} net_pnl={result.net_pnl:.2f} "
            f"dev_kept={dev_kept} [{100 * step / total_steps:5.1f}% done, step {step}/{total_steps}, ETA {eta:.0f}s]",
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
    parser.add_argument("--dev-start", required=True)
    parser.add_argument("--dev-end", required=True)
    parser.add_argument("--val-start", required=True)
    parser.add_argument("--val-end", required=True)
    parser.add_argument("--fresh-start", required=True)
    parser.add_argument("--fresh-end", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--underlying-key", default=NIFTY_UNDERLYING_KEY)
    parser.add_argument("--timeframe", default="FIVE_MINUTE")
    parser.add_argument(
        "--dev-val-include-dhan", action="store_true",
        help="Set when --dev-start/--val-start fall in the DhanHQ-only 2020-2024 range.",
    )
    parser.add_argument(
        "--fresh-include-dhan", action="store_true",
        help="Only set if the fresh range predates real Upstox option coverage; the "
             "default (real Upstox only) is preferred whenever it exists.",
    )
    parser.add_argument(
        "--include-derived", action="store_true",
        help="Include FIVE_MINUTE candles resampled from ONE_MINUTE data. Required for "
             "any range touching 2024-10-03 to 2025-12-31, where the underlying index "
             "has no real native FIVE_MINUTE Upstox coverage at all -- see "
             "BACKTEST_FINDINGS.md's 2026-08-22 entry.",
    )
    parser.add_argument("--strike-distance-pct", type=float, default=0.002)
    parser.add_argument("--stop-multiple", type=float, default=2.0)
    parser.add_argument("--target-fraction", type=float, default=0.5)
    parser.add_argument("--opening-range-bars", type=int, default=6)
    parser.add_argument("--epochs", type=int, default=2000)
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument("--l2", type=float, default=0.01)
    parser.add_argument("--threshold-min", type=float, default=0.05)
    parser.add_argument("--threshold-max", type=float, default=0.80)
    parser.add_argument("--threshold-step", type=float, default=0.02)
    parser.add_argument("--min-validation-trades", type=int, default=20)
    parser.add_argument("--min-development-trades", type=int, default=None)
    args = parser.parse_args(argv)

    dev_start, dev_end = date.fromisoformat(args.dev_start), date.fromisoformat(args.dev_end)
    val_start, val_end = date.fromisoformat(args.val_start), date.fromisoformat(args.val_end)
    fresh_start, fresh_end = date.fromisoformat(args.fresh_start), date.fromisoformat(args.fresh_end)
    if not (dev_start <= dev_end < val_start <= val_end < fresh_start <= fresh_end):
        print(json.dumps({"error": "invalid_ranges", "reason": "expected dev < val < fresh, no overlap"}, indent=2))
        return 1

    archive = MarketArchive(args.archive)
    archive.initialize()
    initialize_ledger(archive)
    settings = _settings_for_archive(args.archive)
    variant = ShortStrangleParameters(
        strike_distance_pct=args.strike_distance_pct,
        entry_time=DEFAULT_ENTRY_TIME,
        stop_multiple=args.stop_multiple,
        target_fraction=args.target_fraction,
        exclude_expiry_day=True,
        maximum_opening_range_pct=None,  # unfiltered exit-shell -- ML replaces this, not stacks with it
        opening_range_bars=args.opening_range_bars,
    )

    print(f"running unfiltered development baseline {dev_start} to {dev_end} for labels ...", flush=True)
    dev_baseline = run_short_strangle_backtest(
        archive, start=dev_start, end=dev_end, settings=settings, parameters=variant,
        underlying_key=args.underlying_key, timeframe=args.timeframe, include_dhan=args.dev_val_include_dhan,
        include_derived=args.include_derived,
    )
    print(f"  {dev_baseline.trades} candidate trades, net_pnl={dev_baseline.net_pnl:.2f}", flush=True)
    label_by_day = {
        trade.entry_at.date().isoformat(): 1.0 if trade.net_pnl > 0 else 0.0
        for trade in dev_baseline.trade_details
    }

    print("re-walking development days to extract features ...", flush=True)
    dev_by_day = _fetch_daily_rows(
        archive, dev_start, dev_end, args.underlying_key, args.timeframe, args.dev_val_include_dhan,
        args.include_derived,
    )
    dev_rows, dev_labels = _build_training_rows(archive, dev_by_day, variant, label_by_day)
    if len(dev_rows) < args.min_validation_trades:
        print(json.dumps({"error": "insufficient_training_data", "reason": f"only {len(dev_rows)} labeled development days"}, indent=2))
        return 1
    print(f"  {len(dev_rows)} labeled development days, positive_rate={sum(dev_labels)/len(dev_labels):.3f}", flush=True)

    means, stds = _standardize_stats(dev_rows, short_strangle_ml_features.FEATURE_NAMES)
    weights, bias = _train_logistic_regression(
        dev_rows, dev_labels, short_strangle_ml_features.FEATURE_NAMES, means, stds,
        args.epochs, args.learning_rate, args.l2,
    )
    model_shell = ml_model_module.SignalQualityModel(
        feature_names=short_strangle_ml_features.FEATURE_NAMES,
        means=tuple(means), stds=tuple(stds), weights=tuple(weights), bias=bias,
        threshold=0.5, metadata={},
    )

    print(f"sweeping thresholds [{args.threshold_min}, {args.threshold_max}] against validation range ...", flush=True)
    threshold, threshold_metadata = _select_threshold(
        archive, model_shell, settings, variant, val_start, val_end, args.underlying_key, args.timeframe,
        args.dev_val_include_dhan, args.include_derived, args.threshold_min, args.threshold_max,
        args.threshold_step, args.min_validation_trades, dev_rows, args.min_development_trades,
    )
    chosen_model = replace(model_shell, threshold=threshold)

    print(f"recomputing development metrics at chosen threshold {threshold} ...", flush=True)
    dev_at_threshold = run_short_strangle_backtest(
        archive, start=dev_start, end=dev_end, settings=settings, parameters=variant,
        underlying_key=args.underlying_key, timeframe=args.timeframe, include_dhan=args.dev_val_include_dhan,
        include_derived=args.include_derived, ml_model=chosen_model,
    )

    print(f"running genuine FRESH confirmation {fresh_start} to {fresh_end} -- baseline vs manual filter vs ML ...", flush=True)
    fresh_unconditional = run_short_strangle_backtest(
        archive, start=fresh_start, end=fresh_end, settings=settings,
        parameters=replace(variant, maximum_opening_range_pct=None),
        underlying_key=args.underlying_key, timeframe=args.timeframe, include_dhan=args.fresh_include_dhan,
        include_derived=args.include_derived,
    )
    fresh_manual_filter = run_short_strangle_backtest(
        archive, start=fresh_start, end=fresh_end, settings=settings,
        parameters=replace(variant, maximum_opening_range_pct=0.005),
        underlying_key=args.underlying_key, timeframe=args.timeframe, include_dhan=args.fresh_include_dhan,
        include_derived=args.include_derived,
    )
    fresh_ml = run_short_strangle_backtest(
        archive, start=fresh_start, end=fresh_end, settings=settings, parameters=variant,
        underlying_key=args.underlying_key, timeframe=args.timeframe, include_dhan=args.fresh_include_dhan,
        include_derived=args.include_derived, ml_model=chosen_model,
    )

    positive_rate = sum(dev_labels) / len(dev_labels)
    metadata = {
        "candidate_name": args.candidate,
        "trained_at": datetime.now().isoformat(),
        "base_variant": {
            "strike_distance_pct": variant.strike_distance_pct,
            "stop_multiple": variant.stop_multiple,
            "target_fraction": variant.target_fraction,
            "opening_range_bars": variant.opening_range_bars,
        },
        "dev_range": [args.dev_start, args.dev_end],
        "val_range": [args.val_start, args.val_end],
        "fresh_range": [args.fresh_start, args.fresh_end],
        "training_row_count": len(dev_rows),
        "positive_rate": positive_rate,
        "hyperparameters": {"epochs": args.epochs, "learning_rate": args.learning_rate, "l2": args.l2},
        "dev_metrics_unfiltered_baseline": _result_summary(dev_baseline),
        "dev_metrics_at_chosen_threshold": _result_summary(dev_at_threshold),
        "val_metrics_at_chosen_threshold": threshold_metadata,
        "fresh_comparison": {
            "unconditional_baseline": _result_summary(fresh_unconditional),
            "existing_manual_opening_range_filter": _result_summary(fresh_manual_filter),
            "ml_filter": _result_summary(fresh_ml),
        },
    }
    model = replace(chosen_model, metadata=metadata)
    ml_model_module.save(model, args.out)

    model_bytes = open(args.out, "rb").read()
    fingerprint_source = json.dumps(
        {"variant": metadata["base_variant"], "model_sha256": hashlib.sha256(model_bytes).hexdigest()},
        sort_keys=True,
    )
    fingerprint = compute_params_fingerprint(fingerprint_source)
    record_usage(
        archive, candidate_name=args.candidate, role=UsageRole.DEVELOPMENT,
        underlying_key=args.underlying_key, timeframe=args.timeframe, start=dev_start, end=dev_end,
        params_fingerprint=fingerprint, notes=f"ML training: {len(dev_rows)} rows, positive_rate={positive_rate:.3f}",
    )
    record_usage(
        archive, candidate_name=args.candidate, role=UsageRole.VALIDATION,
        underlying_key=args.underlying_key, timeframe=args.timeframe, start=val_start, end=val_end,
        params_fingerprint=fingerprint, notes=f"ML threshold selection: threshold={threshold}, {json.dumps(threshold_metadata)}",
    )
    # Deliberately NOT registered via reserve_test_range/record_usage(role=TEST):
    # the ledger's one existing TEST row (candidate r5-best-rsi55-atr20,
    # 2026-08-01..2026-08-20) blocks any new TEST reservation on this
    # underlying_key/timeframe that doesn't start strictly after 2026-08-20 --
    # there is no room left for a mechanically-reserved test range until more
    # data accrues. This matches every prior fresh-range confirmation in this
    # project (Candidate B, ORB, the manual short-strangle filter, the IV
    # filter) -- none of them are in this ledger's TEST rows either; all were
    # done informally and documented directly in BACKTEST_FINDINGS.md. Real
    # freshness here rests on 2025-03..2026-08 never having been used for
    # *this* strategy (short strangle) before, exactly like ORB and the
    # manual strangle filter both legitimately reused the 2020-2024 range as
    # each one's own independent fresh check -- not on this ledger call.

    print(json.dumps({"model_path": args.out, "threshold": threshold, "metadata": metadata}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
