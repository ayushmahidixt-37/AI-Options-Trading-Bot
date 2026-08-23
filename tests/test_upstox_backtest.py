from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from options_bot.backtest import BacktestParameters
from options_bot.candles import Candle
from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.indicators import rsi as compute_rsi
from options_bot.market_archive import MarketArchive
from options_bot.strategy import Direction, MomentumStrategy, Signal
from options_bot.upstox_backtest import generate_signals_from_candles, run_upstox_backtest
from options_bot.upstox_data import UpstoxCandle
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

IST = ZoneInfo("Asia/Kolkata")


def _underlying_candles(count: int, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    return [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(count)
    ]


class ScriptedStrategy:
    """A fake strategy that returns a fixed signal at each candle-window length."""

    minimum_candles = 2
    rsi_period = 14

    def __init__(self, signals: dict[int, Signal]) -> None:
        self._signals = signals

    def evaluate(self, history: list[Candle]) -> Signal | None:
        return self._signals.get(len(history))


def test_generate_signals_fast_path_matches_naive_full_recompute() -> None:
    """generate_signals_from_candles's precomputed-series fast path
    (2026-08-22, fixing a real O(n^2) bottleneck) must produce byte-
    identical output to the naive approach it replaced -- recomputing every
    indicator from scratch over the full growing window at each step, via
    MomentumStrategy.evaluate() directly. Not "usually agree": every
    documented backtest result in BACKTEST_FINDINGS.md depends on this."""
    strategy = MomentumStrategy()
    start = datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    candles: list[Candle] = []
    price = 100.0
    for i in range(400):
        # Deterministic oscillation (not a monotone trend) so both BULLISH
        # and BEARISH signals, and multiple direction flips, actually occur.
        price += 3 * math.sin(i / 7) + 0.4 * math.sin(i / 37)
        candles.append(
            Candle("NIFTY", start + timedelta(minutes=5 * i), price, price + 1.5, price - 1.5, price)
        )

    fast_path = generate_signals_from_candles(candles, strategy)

    naive_observations = []
    last_signal: str | None = None
    for index in range(strategy.minimum_candles, len(candles)):
        window = candles[:index]
        signal = strategy.evaluate(window)
        if signal is None:
            continue
        label = signal.direction.value.upper()
        if label == last_signal:
            continue
        last_signal = label
        closes = [item.close for item in window]
        rsi_value = compute_rsi(closes, strategy.rsi_period)[-1]
        decision_candle = window[-1]
        naive_observations.append(
            (decision_candle.started_at, decision_candle.close, label, rsi_value,
             signal.stop_distance, signal.confidence)
        )

    fast_tuples = [
        (o.observed_at, o.spot, o.signal, o.rsi, o.atr, o.confidence) for o in fast_path
    ]
    assert fast_tuples == naive_observations
    # Sanity check: the oscillation must actually exercise both directions
    # and multiple flips, or this test would pass trivially on empty output.
    assert len(fast_tuples) >= 5
    assert "BULLISH" in {o.signal for o in fast_path}
    assert "BEARISH" in {o.signal for o in fast_path}


def test_generate_signals_computes_ema_gap_normalized_matching_manual_calculation() -> None:
    """ema_gap_normalized (added 2026-08-23 for the minimum_ema_separation
    filter -- trend *strength*, not just direction) must match a manual
    abs(fast-slow)/close calculation at the decision candle, not just be
    present."""
    from options_bot.indicators import ema as compute_ema

    strategy = MomentumStrategy()
    start = datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    candles: list[Candle] = []
    price = 100.0
    for i in range(400):
        price += 3 * math.sin(i / 7) + 0.4 * math.sin(i / 37)
        candles.append(
            Candle("NIFTY", start + timedelta(minutes=5 * i), price, price + 1.5, price - 1.5, price)
        )

    observations = generate_signals_from_candles(candles, strategy)
    assert observations
    closes = [item.close for item in candles]
    for observation in observations:
        index = next(i for i, c in enumerate(candles) if c.started_at == observation.observed_at)
        window_closes = closes[: index + 1]
        fast = compute_ema(window_closes, strategy.fast_period)[-1]
        slow = compute_ema(window_closes, strategy.slow_period)[-1]
        expected = abs(fast - slow) / window_closes[-1]
        assert observation.ema_gap_normalized == pytest.approx(expected)


def test_generate_signals_dedups_and_uses_last_closed_candle() -> None:
    series = _underlying_candles(10)
    signals = {
        3: Signal(Direction.BULLISH, 0.6, 12.0, "test"),
        4: Signal(Direction.BULLISH, 0.6, 12.0, "test"),  # duplicate direction, skipped
        5: Signal(Direction.BEARISH, 0.6, 8.0, "test"),
        7: Signal(Direction.BEARISH, 0.6, 8.0, "test"),  # duplicate direction, skipped
        8: Signal(Direction.BULLISH, 0.6, 12.0, "test"),
    }

    observations = generate_signals_from_candles(series, ScriptedStrategy(signals))

    assert [item.signal for item in observations] == ["BULLISH", "BEARISH", "BULLISH"]
    assert observations[0].observed_at == series[2].started_at
    assert observations[1].observed_at == series[4].started_at
    assert observations[2].observed_at == series[7].started_at
    assert observations[0].atr == 12.0
    assert observations[0].confidence == 0.6


def test_generate_signals_never_evaluates_before_minimum_candles() -> None:
    series = _underlying_candles(5)
    strategy = ScriptedStrategy({1: Signal(Direction.BULLISH, 0.6, 1.0, "test")})
    # minimum_candles=2, so index 1 (a window of length 1) must never be reached
    assert generate_signals_from_candles(series, strategy) == []


def test_run_upstox_backtest_reports_insufficient_data_on_empty_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()

    assert run_upstox_backtest(archive).status == "INSUFFICIENT DATA"


def _seed_upstox_backtest_archive(archive: MarketArchive, start: datetime) -> None:
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [
            UpstoxCandle(candle.symbol, candle.started_at, candle.open, candle.high, candle.low, candle.close)
            for candle in underlying
        ],
        token=NIFTY_UNDERLYING_KEY,
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=start,
    )
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626600CE",
                "NSE_FO|1|13-08-2026",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                100,
            )
        ],
        start,
    )
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=10), 100, 108, 99, 106),
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=15), 106, 109, 104, 105),
        ],
        token="NSE_FO|1|13-08-2026",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=15),
    )


def test_run_upstox_backtest_replays_a_signal_without_strategy_observations(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(archive, strategy=strategy, settings=settings)

    assert result.trades == 1
    trade = result.trade_details[0]
    assert trade.direction == "BULLISH"
    assert trade.token == "NSE_FO|1|13-08-2026"
    assert trade.exit_reason == "force-exit"
    assert trade.entry_price < trade.exit_price  # closing candle rallied vs. entry

    with archive.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM strategy_observations").fetchone()[0] == 0


def test_run_upstox_backtest_minimum_option_premium_skips_cheap_contracts(tmp_path) -> None:
    """Found 2026-08-22 in a per-trade loss analysis: a cheap option's
    entry price can be smaller than the points-based stop distance, so the
    stop can never fire before the option is worthless. minimum_option_premium
    lets a candidate skip these entirely."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)  # entry candle opens at 100
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    skipped = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_option_premium=150),
    )
    assert skipped.trades == 0

    kept = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_option_premium=50),
    )
    assert kept.trades == 1


def test_run_upstox_backtest_minimum_signal_confidence_skips_low_confidence_signals(tmp_path) -> None:
    """minimum_signal_confidence lets a candidate skip trades whose
    originating signal scored below a chosen confidence -- data every
    strategy already computes and previously discarded after generating
    the trade (listed as an untested breakdown dimension in
    BACKTEST_FINDINGS.md before being wired up 2026-08-22)."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    skipped = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_signal_confidence=0.7),
    )
    assert skipped.trades == 0

    kept = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_signal_confidence=0.5),
    )
    assert kept.trades == 1


def test_run_upstox_backtest_minimum_open_interest_skips_low_or_unknown_oi(tmp_path) -> None:
    """minimum_open_interest requires the selected contract's most recent
    known open interest as of signal time to clear a floor -- an unknown
    (never-recorded) OI is treated as failing the filter, not passing it."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)  # no open_interest recorded on these candles
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    unknown_oi = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_open_interest=100),
    )
    assert unknown_oi.trades == 0

    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG2626600CE", start, 100, 100, 100, 100, open_interest=500)],
        token="NSE_FO|1|13-08-2026",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start,
    )

    below_floor = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_open_interest=1000),
    )
    assert below_floor.trades == 0

    kept = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_open_interest=200),
    )
    assert kept.trades == 1


def test_run_upstox_backtest_minimum_ema_separation_skips_weak_trend_signals(tmp_path) -> None:
    """minimum_ema_separation filters on trend *strength* (the normalized
    fast/slow EMA gap), not just direction -- a bare crossover doesn't
    distinguish a 0.1-point flip from a 5-point one. Only meaningful for
    strategies whose fast path computes ema_gap_normalized (ScriptedStrategy
    doesn't, so its observations carry ema_gap_normalized=None -- confirming
    the filter fails closed rather than silently passing everything)."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    skipped = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_ema_separation=0.01),
    )
    assert skipped.trades == 0

    kept = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_ema_separation=None),
    )
    assert kept.trades == 1


def test_run_upstox_backtest_trailing_activation_return_lets_early_noise_survive(tmp_path) -> None:
    """Added 2026-08-23 per explicit user request: 'follow-up instead of a
    hard sell' -- trailing_stop alone starts ratcheting from the very first
    candle (peak_price begins at the entry fill), which can stop a winner
    out on ordinary early noise before it has proven itself.
    trailing_activation_return delays trailing until the position has
    actually reached that unrealized return; before that, only the fixed
    initial stop applies. Same exact price path, same trailing_stop=5%:
    without activation the position is stopped out at a small LOSS during
    routine early movement; with a 10% activation threshold it survives
    that same movement, later clears activation, and exits at a locked-in
    PROFIT instead."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close) for c in underlying],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=start,
    )
    archive.save_instruments(
        [Instrument("NIFTY13AUG2626600CE", "NSE_FO|1|13-08-2026", "NFO", "NIFTY", "CE", 80, date(2026, 8, 13), 100)],
        start,
    )
    # path[0] is the entry candle itself (query is started_at>=entry time).
    option_candles = [
        UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=10), 100, 100, 100, 100),
        UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=15), 100, 104, 99, 101),
        # Without activation, trailing would already be at 104*0.95=98.8 here -- this
        # candle's low (97) would stop it out at a loss. With activation still
        # pending (peak 104 < 110 threshold), only the wide initial stop (96) applies.
        UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=20), 101, 101, 97, 98),
        # Clears the 10% activation threshold (peak 112 >= 110) -- trailing now arms.
        UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=25), 98, 112, 98, 110),
        # Pulls back into the now-armed trailing stop (112*0.95=106.4) -- locked-in profit.
        UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=30), 110, 111, 105, 106),
    ]
    archive.save_upstox_candles(
        option_candles, token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=30),
    )
    settings = Settings.from_env({
        "DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        "PAPER_SLIPPAGE_BPS": "0", "PAPER_FEE_PER_ORDER": "0",
    })
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    without_activation = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(stop_risk_fraction=0.8, target_return=None, trailing_stop=0.05),
    )
    assert without_activation.trades == 1
    stopped_early = without_activation.trade_details[0]
    assert stopped_early.exit_reason == "stop"
    assert stopped_early.entry_price == pytest.approx(100.0)
    assert stopped_early.exit_price == pytest.approx(98.8)  # 104 * 0.95, stopped out at a loss
    assert stopped_early.net_pnl < 0

    with_activation = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(
            stop_risk_fraction=0.8, target_return=None, trailing_stop=0.05,
            trailing_activation_return=0.10,
        ),
    )
    assert with_activation.trades == 1
    locked_in_profit = with_activation.trade_details[0]
    assert locked_in_profit.exit_reason == "stop"
    assert locked_in_profit.exit_price == pytest.approx(106.4)  # 112 * 0.95, survived the same dip
    assert locked_in_profit.net_pnl > 0
    assert locked_in_profit.exit_at > stopped_early.exit_at  # rode the move further


def test_run_upstox_backtest_minimum_opening_range_pct_requires_a_wide_open_and_no_lookahead(tmp_path) -> None:
    """The flip side of the short-strangle engine's opening-range filter:
    there, a NARROW range selects calm days; here, a WIDE one is tested as
    a signal for trend/breakout-worthy days. Also confirms the no-lookahead
    guard: a signal observed before the opening range has actually closed
    must be skipped, not evaluated against an as-yet-incomplete range."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    # First 6 candles (the opening range) span 85-115 around a 100 base --
    # a wide ~35% range. Candle 6 (index 6, 0-based) is where the signal
    # fires -- strictly after the range has closed.
    underlying = [
        Candle("NIFTY", start, 100, 100, 100, 100),
        Candle("NIFTY", start + timedelta(minutes=5), 100, 115, 100, 110),
        Candle("NIFTY", start + timedelta(minutes=10), 110, 110, 85, 90),
        Candle("NIFTY", start + timedelta(minutes=15), 100, 100, 100, 100),
        Candle("NIFTY", start + timedelta(minutes=20), 100, 100, 100, 100),
        Candle("NIFTY", start + timedelta(minutes=25), 100, 100, 100, 100),
        Candle("NIFTY", start + timedelta(minutes=30), 100, 100, 100, 100),
        Candle("NIFTY", start + timedelta(minutes=35), 100, 100, 100, 100),
    ]
    archive.save_upstox_candles(
        [UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close) for c in underlying],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=start,
    )
    archive.save_instruments(
        [Instrument("NIFTY13AUG2626600CE", "NSE_FO|1|13-08-2026", "NFO", "NIFTY", "CE", 75, date(2026, 8, 13), 100)],
        start,
    )
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=35 + 5 * i), 100, 108, 99, 106)
            for i in range(3)
        ],
        token="NSE_FO|1|13-08-2026", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=start + timedelta(minutes=45),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    # Signal at len(history)==7 -> decision candle = underlying[6], strictly
    # after the opening range (underlying[0:6]) has closed.
    strategy = ScriptedStrategy({7: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    kept = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_opening_range_pct=0.05),
    )
    assert kept.trades == 1

    skipped_too_narrow = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        parameters=BacktestParameters(minimum_opening_range_pct=0.50),  # wider than the real ~35% range
    )
    assert skipped_too_narrow.trades == 0

    # No-lookahead guard: a signal before the range closes must be skipped
    # even though the (not-yet-known) eventual range is plenty wide.
    early_strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})
    skipped_too_early = run_upstox_backtest(
        archive, strategy=early_strategy, settings=settings,
        parameters=BacktestParameters(minimum_opening_range_pct=0.05),
    )
    assert skipped_too_early.trades == 0


def test_run_upstox_backtest_exclude_macro_event_days_skips_known_event_dates(tmp_path) -> None:
    """A signal on (or the day after) a known scheduled macro event --
    RBI MPC / FOMC / Union Budget -- must be skippable via
    exclude_macro_event_days, while an ordinary day is unaffected."""
    settings = Settings.from_env({"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")})
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})
    params = BacktestParameters(exclude_macro_event_days=True)

    event_archive = MarketArchive(tmp_path / "event.sqlite3")
    event_archive.initialize()
    # 2026-02-06 is a known RBI MPC announcement date.
    event_day = datetime(2026, 2, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(event_archive, event_day)
    skipped = run_upstox_backtest(event_archive, strategy=strategy, settings=settings, parameters=params)
    assert skipped.trades == 0

    ordinary_archive = MarketArchive(tmp_path / "ordinary.sqlite3")
    ordinary_archive.initialize()
    ordinary_day = datetime(2026, 3, 3, 9, 15, tzinfo=IST)  # not near any known event
    _seed_upstox_backtest_archive(ordinary_archive, ordinary_day)
    kept = run_upstox_backtest(ordinary_archive, strategy=strategy, settings=settings, parameters=params)
    assert kept.trades == 1


def test_run_upstox_backtest_status_ignores_angel_sourced_gaps(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    # A real gap, but in Angel-sourced data -- must never affect Upstox status.
    archive.save_candles(
        [
            Candle("NIFTY", start, 100, 103, 99, 102),
            Candle("NIFTY", start + timedelta(minutes=20), 102, 104, 101, 103),
        ],
        token="99926000",
        exchange="NSE",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=20),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(archive, strategy=strategy, settings=settings)

    assert result.data_gaps == 0
    assert result.status != "DATA QUALITY WARNING"


def test_run_upstox_backtest_status_ignores_gaps_outside_the_requested_range(tmp_path) -> None:
    """gap_summary() must be scoped to the backtest's own [start, end], not
    the whole archive -- found 2026-08-21 while investigating why a chunked
    multi-year backtest reported an identical gap count for every chunk
    regardless of date range. See gap_summary's docstring."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    # A real Upstox gap, but on a date far outside the requested backtest range.
    old_day = datetime(2025, 1, 6, 9, 15, tzinfo=IST)
    archive.save_upstox_candles(
        [
            UpstoxCandle("Nifty 50", old_day, 100, 103, 99, 102),
            UpstoxCandle("Nifty 50", old_day + timedelta(minutes=20), 102, 104, 101, 103),
        ],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE", collected_at=old_day + timedelta(minutes=20),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(
        archive, strategy=strategy, settings=settings,
        start=start.date(), end=start.date(),
    )

    assert result.data_gaps == 0
    assert result.status != "DATA QUALITY WARNING"


def test_run_upstox_backtest_never_selects_a_derived_contract(tmp_path) -> None:
    """A candle resampled from a finer timeframe (research/materialize_resampled_candles.py)
    must never be picked up by the real backtest engine -- see the module's
    docstring and BACKTEST_FINDINGS.md's 2026-08-21 data-integrity entry,
    where a materialize run silently changed every documented finding."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)

    # A derived contract with a strike CLOSER to spot than the real Upstox one.
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626101CE",
                "NSE_FO|derived|13-08-2026",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                101,
            )
        ],
        start,
    )
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG2626101CE", start + timedelta(minutes=10), 50, 55, 49, 52)],
        token="NSE_FO|derived|13-08-2026",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
        derived_from_timeframe="ONE_MINUTE",
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(archive, strategy=strategy, settings=settings)

    assert result.trades == 1
    assert result.trade_details[0].token == "NSE_FO|1|13-08-2026"


def test_run_upstox_backtest_never_selects_an_angel_sourced_contract(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)

    # An Angel-sourced instrument with a strike CLOSER to spot than the Upstox one.
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626101CE",
                "999",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                101,
            )
        ],
        start,
    )
    archive.save_candles(
        [Candle("NIFTY13AUG2626101CE", start + timedelta(minutes=10), 50, 55, 49, 52)],
        token="999",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    result = run_upstox_backtest(archive, strategy=strategy, settings=settings)

    assert result.trades == 1
    assert result.trade_details[0].token == "NSE_FO|1|13-08-2026"


def test_run_upstox_backtest_stop_risk_fraction_none_disables_the_stop(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    underlying = _underlying_candles(4, start=start)
    archive.save_upstox_candles(
        [
            UpstoxCandle(c.symbol, c.started_at, c.open, c.high, c.low, c.close)
            for c in underlying
        ],
        token=NIFTY_UNDERLYING_KEY,
        exchange="NSE_INDEX",
        timeframe="FIVE_MINUTE",
        collected_at=start,
    )
    archive.save_instruments(
        [
            Instrument(
                "NIFTY13AUG2626600CE",
                "NSE_FO|1|13-08-2026",
                "NFO",
                "NIFTY",
                "CE",
                75,
                date(2026, 8, 13),
                100,
            )
        ],
        start,
    )
    # A deep dip (low=50) that would normally trigger the stop immediately.
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG2626600CE", start + timedelta(minutes=10), 100, 101, 50, 92)],
        token="NSE_FO|1|13-08-2026",
        exchange="NFO",
        timeframe="FIVE_MINUTE",
        collected_at=start + timedelta(minutes=10),
    )
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    from options_bot.backtest import BacktestParameters

    result = run_upstox_backtest(
        archive,
        strategy=strategy,
        settings=settings,
        parameters=BacktestParameters(stop_risk_fraction=None),
    )

    trade = result.trade_details[0]
    assert trade.exit_reason not in ("stop", "stop-gap")
    assert trade.stop_price == 0.0


def test_run_upstox_backtest_respects_parameter_filters(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_upstox_backtest_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    from options_bot.backtest import BacktestParameters

    result = run_upstox_backtest(
        archive,
        strategy=strategy,
        settings=settings,
        parameters=BacktestParameters(name="ATR floor", minimum_atr=15),
    )

    assert result.status == "INSUFFICIENT DATA"
