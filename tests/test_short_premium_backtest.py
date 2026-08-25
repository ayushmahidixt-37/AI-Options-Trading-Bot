from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from options_bot.config import Settings
from options_bot.domain import Instrument
from options_bot.market_archive import MarketArchive
from options_bot.ml_model import SignalQualityModel
from options_bot.short_premium_backtest import (
    ShortStrangleParameters,
    run_short_strangle_backtest,
)
from options_bot.short_strangle_ml_features import FEATURE_NAMES
from options_bot.upstox_data import UpstoxCandle
from options_bot.upstox_ingest import NIFTY_UNDERLYING_KEY


def _always_model(*, threshold: float, bias: float) -> SignalQualityModel:
    """A model whose score ignores every feature -- bias alone decides,
    letting a test assert the gating mechanics without a real fit."""
    return SignalQualityModel(
        feature_names=FEATURE_NAMES,
        means=tuple(0.0 for _ in FEATURE_NAMES),
        stds=tuple(1.0 for _ in FEATURE_NAMES),
        weights=tuple(0.0 for _ in FEATURE_NAMES),
        bias=bias,
        threshold=threshold,
        metadata={},
    )

IST = ZoneInfo("Asia/Kolkata")


def _seed_underlying(archive: MarketArchive, day_start: datetime, spot: float, count: int = 20) -> None:
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY", day_start + timedelta(minutes=5 * i), spot, spot, spot, spot)
            for i in range(count)
        ],
        token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=day_start,
    )


def _seed_underlying_with_opening_range(
    archive: MarketArchive, day_start: datetime, spot: float, opening_range_pct: float, count: int = 20,
) -> None:
    """First 6 candles span opening_range_pct around spot (widening the
    high/low symmetrically); the rest are flat at spot."""
    half_range = spot * opening_range_pct / 2
    candles = []
    for i in range(count):
        if i < 6:
            candles.append(UpstoxCandle(
                "NIFTY", day_start + timedelta(minutes=5 * i),
                spot, spot + half_range, spot - half_range, spot,
            ))
        else:
            candles.append(UpstoxCandle("NIFTY", day_start + timedelta(minutes=5 * i), spot, spot, spot, spot))
    archive.save_upstox_candles(
        candles, token=NIFTY_UNDERLYING_KEY, exchange="NSE_INDEX", timeframe="FIVE_MINUTE", collected_at=day_start,
    )


def _seed_legs(
    archive: MarketArchive, day_start: datetime, expiry: date,
    call_strike: float, call_prices: list[float], put_strike: float, put_prices: list[float],
) -> None:
    archive.save_instruments(
        [
            Instrument("NIFTY13AUG26CE", "CE_TOKEN", "NFO", "NIFTY", "CE", 75, expiry, call_strike),
            Instrument("NIFTY13AUG26PE", "PE_TOKEN", "NFO", "NIFTY", "PE", 75, expiry, put_strike),
        ],
        day_start,
    )
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG26CE", day_start + timedelta(minutes=5 * i), price, price, price, price)
            for i, price in enumerate(call_prices)
        ],
        token="CE_TOKEN", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=day_start,
    )
    archive.save_upstox_candles(
        [
            UpstoxCandle("NIFTY13AUG26PE", day_start + timedelta(minutes=5 * i), price, price, price, price)
            for i, price in enumerate(put_prices)
        ],
        token="PE_TOKEN", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=day_start,
    )


def _settings(tmp_path) -> Settings:
    return Settings.from_env({
        "DATA_DIR": str(tmp_path), "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        "PAPER_SLIPPAGE_BPS": "0", "PAPER_FEE_PER_ORDER": "0",
    })


def test_run_short_strangle_backtest_reports_insufficient_data_on_empty_archive(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    assert run_short_strangle_backtest(archive).status == "INSUFFICIENT DATA"


def test_run_short_strangle_backtest_selects_nearest_otm_strikes_each_side(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_underlying(archive, day_start, spot=25000.0)
    # strike_distance_pct=0.01 -> call target 25250, put target 24750.
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 27),
        call_strike=25300.0, call_prices=[100.0] * 10,
        put_strike=24700.0, put_prices=[90.0] * 10,
    )
    settings = _settings(tmp_path)

    result = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01),
    )

    assert result.trades == 1
    trade = result.trade_details[0]
    assert trade.call_symbol == "NIFTY13AUG26CE"
    assert trade.put_symbol == "NIFTY13AUG26PE"


def test_run_short_strangle_backtest_exits_at_target_when_premium_decays(tmp_path) -> None:
    """Premium collected = 100+90=190. target_fraction=0.5 -> exits once the
    combined cost to close decays to <= 95."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 45, tzinfo=IST)
    _seed_underlying(archive, day_start, spot=25000.0)
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 27),
        call_strike=25300.0, call_prices=[100.0, 80.0, 50.0],
        put_strike=24700.0, put_prices=[90.0, 70.0, 40.0],
    )
    settings = _settings(tmp_path)

    result = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, stop_multiple=1.5, target_fraction=0.5),
    )

    assert result.trades == 1
    trade = result.trade_details[0]
    assert trade.exit_reason == "target"
    assert trade.call_exit_price == 50.0
    assert trade.put_exit_price == 40.0
    assert trade.premium_collected == 190.0 * 75  # (100+90) * lot_size
    assert trade.net_pnl == (190.0 - 90.0) * 75  # sold for 190, bought back for 90, per unit
    assert trade.net_pnl > 0


def test_run_short_strangle_backtest_exits_at_stop_when_premium_spikes(tmp_path) -> None:
    """Premium collected = 100+90=190. stop_multiple=1.5 -> exits once the
    combined cost to close reaches >= 285 (a loss -- the underlying moved
    hard against a short strangle)."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 45, tzinfo=IST)
    _seed_underlying(archive, day_start, spot=25000.0)
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 27),
        call_strike=25300.0, call_prices=[100.0, 200.0, 260.0],
        put_strike=24700.0, put_prices=[90.0, 60.0, 30.0],
    )
    settings = _settings(tmp_path)

    result = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, stop_multiple=1.5, target_fraction=0.5),
    )

    assert result.trades == 1
    trade = result.trade_details[0]
    assert trade.exit_reason == "stop"
    assert trade.net_pnl < 0


def test_run_short_strangle_backtest_excludes_same_day_expiry_by_default(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 45, tzinfo=IST)
    _seed_underlying(archive, day_start, spot=25000.0)
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 6),  # expires THIS day
        call_strike=25300.0, call_prices=[100.0] * 3,
        put_strike=24700.0, put_prices=[90.0] * 3,
    )
    settings = _settings(tmp_path)

    result = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, exclude_expiry_day=True),
    )

    assert result.trades == 0

    kept = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, exclude_expiry_day=False),
    )
    assert kept.trades == 1


def test_run_short_strangle_backtest_ignores_other_timeframes_for_option_legs(tmp_path) -> None:
    """Found 2026-08-23 via a suspiciously uniform holding duration across
    every trade in a real backtest: the option-leg candle queries never
    filtered by timeframe, so a contract with both ONE_MINUTE and
    FIVE_MINUTE candles archived (common -- ONE_MINUTE data exists to fill
    historical gaps) had them silently mixed together. Seeds deliberately
    different prices on the two timeframes so a leftover bug would produce
    a visibly wrong entry/exit price, not just a silently-wrong one."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 45, tzinfo=IST)
    _seed_underlying(archive, day_start, spot=25000.0)
    archive.save_instruments(
        [
            Instrument("NIFTY13AUG26CE", "CE_TOKEN", "NFO", "NIFTY", "CE", 75, date(2026, 8, 27), 25300.0),
            Instrument("NIFTY13AUG26PE", "PE_TOKEN", "NFO", "NIFTY", "PE", 75, date(2026, 8, 27), 24700.0),
        ],
        day_start,
    )
    # FIVE_MINUTE: the real series this backtest must use.
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG26CE", day_start + timedelta(minutes=5 * i), 100.0, 100.0, 100.0, 100.0) for i in range(3)],
        token="CE_TOKEN", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=day_start,
    )
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG26PE", day_start + timedelta(minutes=5 * i), 90.0, 90.0, 90.0, 90.0) for i in range(3)],
        token="PE_TOKEN", exchange="NFO", timeframe="FIVE_MINUTE", collected_at=day_start,
    )
    # ONE_MINUTE: deliberately wildly different prices -- must be ignored entirely.
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG26CE", day_start + timedelta(minutes=i), 999.0, 999.0, 999.0, 999.0) for i in range(15)],
        token="CE_TOKEN", exchange="NFO", timeframe="ONE_MINUTE", collected_at=day_start,
    )
    archive.save_upstox_candles(
        [UpstoxCandle("NIFTY13AUG26PE", day_start + timedelta(minutes=i), 999.0, 999.0, 999.0, 999.0) for i in range(15)],
        token="PE_TOKEN", exchange="NFO", timeframe="ONE_MINUTE", collected_at=day_start,
    )
    settings = _settings(tmp_path)

    result = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01), timeframe="FIVE_MINUTE",
    )

    assert result.trades == 1
    trade = result.trade_details[0]
    assert trade.call_entry_price == 100.0
    assert trade.put_entry_price == 90.0
    assert trade.premium_collected == 190.0 * 75


def test_run_short_strangle_backtest_maximum_opening_range_pct_skips_wide_days(tmp_path) -> None:
    """Added 2026-08-23: the 7-quarter check found no stable edge running
    this strategy unconditionally every day. maximum_opening_range_pct lets
    it be deployed selectively -- only on days whose opening range (same-
    day, no lookahead, entry itself is after the range closes) looks calm
    enough to be worth selling premium on."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_underlying_with_opening_range(archive, day_start, spot=25000.0, opening_range_pct=0.02)  # a wide 2% open
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 27),
        call_strike=25300.0, call_prices=[100.0] * 20,
        put_strike=24700.0, put_prices=[90.0] * 20,
    )
    settings = _settings(tmp_path)

    skipped = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, maximum_opening_range_pct=0.01),
    )
    assert skipped.trades == 0

    kept = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, maximum_opening_range_pct=0.03),
    )
    assert kept.trades == 1

    unfiltered = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01, maximum_opening_range_pct=None),
    )
    assert unfiltered.trades == 1


def test_run_short_strangle_backtest_ml_model_gates_entries_and_overrides_opening_range_pct(tmp_path) -> None:
    """ml_model, when given, must decide the day on its own -- ignoring
    variant.maximum_opening_range_pct entirely, not stacking with it."""
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 15, tzinfo=IST)
    _seed_underlying_with_opening_range(archive, day_start, spot=25000.0, opening_range_pct=0.02)
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 27),
        call_strike=25300.0, call_prices=[100.0] * 20,
        put_strike=24700.0, put_prices=[90.0] * 20,
    )
    settings = _settings(tmp_path)
    # A tight maximum_opening_range_pct that would normally reject this
    # (2%-wide) day on its own -- the ML model must be the thing deciding.
    params = ShortStrangleParameters(strike_distance_pct=0.01, maximum_opening_range_pct=0.01)

    rejecting = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=params, ml_model=_always_model(threshold=0.5, bias=-10.0),
    )
    assert rejecting.trades == 0

    approving = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=params, ml_model=_always_model(threshold=0.5, bias=10.0),
    )
    assert approving.trades == 1


def test_short_strangle_result_return_on_premium_is_distinct_from_win_rate(tmp_path) -> None:
    archive = MarketArchive(tmp_path / "market.sqlite3")
    archive.initialize()
    day_start = datetime(2026, 8, 6, 9, 45, tzinfo=IST)
    _seed_underlying(archive, day_start, spot=25000.0)
    _seed_legs(
        archive, day_start, expiry=date(2026, 8, 27),
        call_strike=25300.0, call_prices=[100.0, 80.0, 50.0],
        put_strike=24700.0, put_prices=[90.0, 70.0, 40.0],
    )
    settings = _settings(tmp_path)

    result = run_short_strangle_backtest(
        archive, start=date(2026, 8, 6), end=date(2026, 8, 6), settings=settings,
        parameters=ShortStrangleParameters(strike_distance_pct=0.01),
    )

    assert result.premium_collected_total == 190.0 * 75
    assert result.return_on_premium_pct == round(result.net_pnl / result.premium_collected_total * 100, 2)
