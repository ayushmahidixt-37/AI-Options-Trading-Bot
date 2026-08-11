from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.backtest import BacktestResult
from options_bot.candles import Candle
from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.strategy import Direction, Signal
from options_bot.upstox_analysis import (
    DeepAnalysisReport,
    TradeBreakdown,
    _highlight,
    generate_suggestions,
    run_deep_analysis,
)
from options_bot.upstox_data import UpstoxCandle
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY

IST = ZoneInfo("Asia/Kolkata")

EMPTY_RESULT = BacktestResult("INSUFFICIENT DATA", 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, None, "n/a")


def _breakdown(label: str, trades: int, win_rate: float | None) -> TradeBreakdown:
    return TradeBreakdown(label=label, trades=trades, win_rate=win_rate, profit_factor=None, average_net_pnl=None)


def _report(time_of_day=(), day_of_week=(), expiry_day=(), volatility_regime=()) -> DeepAnalysisReport:
    return DeepAnalysisReport(
        overall=EMPTY_RESULT,
        time_of_day=time_of_day,
        day_of_week=day_of_week,
        expiry_day=expiry_day,
        volatility_regime=volatility_regime,
        variants=(),
    )


def test_no_suggestion_below_minimum_sample() -> None:
    report = _report(
        time_of_day=(_breakdown("Morning", 15, 0.8), _breakdown("Afternoon", 15, 0.2))
    )

    assert generate_suggestions(report, minimum_sample=20) == ()


def test_no_suggestion_when_gap_is_noise_level() -> None:
    report = _report(
        time_of_day=(_breakdown("Morning", 25, 0.52), _breakdown("Afternoon", 30, 0.50))
    )

    assert generate_suggestions(report, minimum_sample=20) == ()


def test_suggestion_text_matches_manual_arithmetic() -> None:
    report = _report(
        time_of_day=(_breakdown("Morning", 25, 0.8), _breakdown("Afternoon", 30, 0.5))
    )

    suggestions = generate_suggestions(report, minimum_sample=20)

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert "time of day" in suggestion.headline
    assert "Morning" in suggestion.headline
    assert "Afternoon" in suggestion.headline
    assert "30.0 percentage" in suggestion.headline
    assert "80.0% over 25 trades" in suggestion.evidence
    assert "50.0% over 30 trades" in suggestion.evidence
    assert suggestion.supporting_trades == 55


def test_highlight_appears_from_a_tiny_sample_and_is_marked_preliminary() -> None:
    breakdown = (_breakdown("Morning", 2, 1.0), _breakdown("Afternoon", 1, 0.0))

    highlight = _highlight("time of day", breakdown, minimum_sample=20)

    assert highlight is not None
    assert highlight.dimension == "time of day"
    assert "Morning" in highlight.headline
    assert "Afternoon" in highlight.headline
    assert highlight.preliminary is True


def test_highlight_is_not_preliminary_once_both_sides_clear_the_sample_bar() -> None:
    breakdown = (_breakdown("Morning", 25, 0.8), _breakdown("Afternoon", 30, 0.5))

    highlight = _highlight("time of day", breakdown, minimum_sample=20)

    assert highlight is not None
    assert highlight.preliminary is False


def test_highlight_is_none_with_fewer_than_two_eligible_groups() -> None:
    assert _highlight("time of day", (_breakdown("Morning", 5, 0.8),), minimum_sample=20) is None
    assert _highlight("time of day", (), minimum_sample=20) is None


def test_highlight_is_none_when_only_one_group_has_trades() -> None:
    breakdown = (_breakdown("Morning", 5, 0.8), _breakdown("Afternoon", 0, None))
    assert _highlight("time of day", breakdown, minimum_sample=20) is None


def test_report_highlights_field_defaults_to_empty_tuple() -> None:
    report = _report()
    assert report.highlights == ()


def test_no_suggestion_when_only_one_bucket_is_eligible() -> None:
    report = _report(
        time_of_day=(_breakdown("Morning", 25, 0.8), _breakdown("Afternoon", 5, 0.1))
    )

    assert generate_suggestions(report, minimum_sample=20) == ()


def test_multiple_dimensions_each_contribute_independently() -> None:
    report = _report(
        time_of_day=(_breakdown("Morning", 25, 0.8), _breakdown("Afternoon", 30, 0.5)),
        day_of_week=(_breakdown("Monday", 25, 0.9), _breakdown("Tuesday", 25, 0.4)),
    )

    suggestions = generate_suggestions(report, minimum_sample=20)

    assert len(suggestions) == 2
    dimensions = {"time of day" in s.headline for s in suggestions}
    assert True in dimensions


def _seed_analysis_archive(archive: MarketArchive, start: datetime) -> None:
    underlying = [
        Candle("NIFTY", start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i)
        for i in range(4)
    ]
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


class ScriptedStrategy:
    minimum_candles = 2
    rsi_period = 14

    def __init__(self, signals):
        self._signals = signals

    def evaluate(self, history):
        return self._signals.get(len(history))


def test_run_deep_analysis_smoke_end_to_end(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_analysis_archive(archive, start)
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3")}
    )
    strategy = ScriptedStrategy({2: Signal(Direction.BULLISH, 0.6, 10.0, "test")})

    report = run_deep_analysis(archive, strategy=strategy, settings=settings, variants=())

    assert report.overall.trades == 1
    assert len(report.expiry_day) == 2
    assert sum(item.trades for item in report.expiry_day) == 1
    assert report.variants == ()
    # generate_suggestions must not crash on a tiny sample; it should just be quiet
    assert generate_suggestions(report) == ()
