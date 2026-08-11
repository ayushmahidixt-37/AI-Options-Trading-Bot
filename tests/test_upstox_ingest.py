from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from options_bot.market_archive import MarketArchive
from options_bot.upstox_data import UpstoxDataError
from options_bot.upstox_ingest import (
    ContractPlan,
    RateLimiter,
    chunk_date_range,
    plan_ingestion,
    pull_range,
)


def _archive(tmp_path: Path) -> MarketArchive:
    store = MarketArchive(tmp_path / "market-data.sqlite3")
    store.initialize()
    return store


class FakeClient:
    def __init__(self) -> None:
        self.expiries = [date(2025, 4, 24), date(2025, 5, 1)]
        self.candle_calls: list[tuple] = []

    def get_expiries(self, instrument_key: str) -> list[date]:
        return self.expiries

    def get_expired_option_contracts(self, instrument_key: str, expiry: date) -> list[dict]:
        return [
            {
                "expired_instrument_key": f"NSE_FO|1|{expiry.isoformat()}",
                "strike_price": 24500,
                "instrument_type": "CE",
                "trading_symbol": "NIFTY CE 24500",
                "lot_size": 75,
            },
            {
                "expired_instrument_key": f"NSE_FO|2|{expiry.isoformat()}",
                "strike_price": 24600,
                "instrument_type": "PE",
                "trading_symbol": "NIFTY PE 24600",
                "lot_size": 75,
            },
        ]

    def get_expired_historical_candles(self, expired_instrument_key, from_date, to_date, interval="5minute"):
        self.candle_calls.append((expired_instrument_key, from_date, to_date, interval))
        return [["2025-04-24T09:15:00+05:30", 100.0, 105.0, 99.0, 102.0, 1000, 500]]

    def get_historical_candles_v3(self, instrument_key, unit, interval, from_date, to_date):
        self.candle_calls.append((instrument_key, from_date, to_date, f"{unit}/{interval}"))
        return [["2025-04-24T09:15:00+05:30", 24500.0, 24600.0, 24400.0, 24550.0, 0, 0]]


def test_chunk_date_range_splits_non_overlapping() -> None:
    chunks = chunk_date_range(date(2025, 1, 1), date(2025, 3, 1), chunk_days=28)
    assert chunks[0] == (date(2025, 1, 1), date(2025, 1, 28))
    assert chunks[1][0] == date(2025, 1, 29)
    assert chunks[-1][1] == date(2025, 3, 1)
    # every day covered exactly once, no overlap
    covered = set()
    for start, end in chunks:
        current = start
        while current <= end:
            assert current not in covered
            covered.add(current)
            current = date.fromordinal(current.toordinal() + 1)


def test_chunk_date_range_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        chunk_date_range(date(2025, 2, 1), date(2025, 1, 1))
    with pytest.raises(ValueError):
        chunk_date_range(date(2025, 1, 1), date(2025, 1, 2), chunk_days=0)


def test_rate_limiter_paces_calls() -> None:
    times = iter([0.0, 0.0, 0.02, 0.02])
    slept: list[float] = []
    limiter = RateLimiter(
        min_interval_seconds=0.05,
        clock=lambda: next(times),
        sleeper=lambda seconds: slept.append(seconds),
    )
    limiter.wait()
    limiter.wait()
    assert slept == [pytest.approx(0.03)]


def test_plan_ingestion_selects_near_atm_strikes_within_range() -> None:
    client = FakeClient()
    plans, warnings = plan_ingestion(
        client,
        date(2025, 4, 1),
        date(2025, 4, 30),
        max_lookback_days=180,
        observed_today=date(2025, 4, 30),
    )
    assert warnings == ()
    # both April 24 and May 1 expiries fall within [start, end + 1 day]
    assert len(plans) == 4
    assert all(isinstance(plan, ContractPlan) for plan in plans)
    assert {plan.instrument.strike for plan in plans} == {24500.0, 24600.0}
    assert {plan.expiry for plan in plans} == {date(2025, 4, 24), date(2025, 5, 1)}


def test_plan_ingestion_warns_and_trims_when_start_exceeds_lookback_ceiling() -> None:
    client = FakeClient()
    plans, warnings = plan_ingestion(
        client,
        date(2024, 1, 1),
        date(2025, 4, 30),
        max_lookback_days=180,
        observed_today=date(2025, 4, 30),
    )
    assert len(warnings) == 1
    assert "180 days" in warnings[0]
    assert all(plan.pull_start >= date(2024, 11, 1) for plan in plans)


def test_pull_range_writes_candles_and_is_idempotent(tmp_path: Path) -> None:
    store = _archive(tmp_path)
    client = FakeClient()

    summary = pull_range(
        client,
        store,
        date(2025, 4, 20),
        date(2025, 4, 24),
        max_lookback_days=180,
        observed_at=datetime(2025, 4, 25, 10, 0),
        rate_limiter=RateLimiter(min_interval_seconds=0),
    )

    assert summary.contracts_planned == 2
    assert summary.contracts_pulled == 2
    assert summary.candles_saved > 0
    assert summary.instruments_saved == 2
    assert summary.warnings == ()

    rerun = pull_range(
        client,
        store,
        date(2025, 4, 20),
        date(2025, 4, 24),
        max_lookback_days=180,
        observed_at=datetime(2025, 4, 25, 10, 0),
        rate_limiter=RateLimiter(min_interval_seconds=0),
    )
    assert rerun.candles_saved == 0  # duplicate-safe rerun

    stats = store.stats()
    assert stats.candle_count > 0


def test_pull_range_records_a_collection_run(tmp_path: Path) -> None:
    store = _archive(tmp_path)
    client = FakeClient()

    pull_range(
        client,
        store,
        date(2025, 4, 20),
        date(2025, 4, 24),
        max_lookback_days=180,
        observed_at=datetime(2025, 4, 25, 10, 0),
        rate_limiter=RateLimiter(min_interval_seconds=0),
    )

    metrics = store.readiness_metrics()
    assert metrics.successful_runs == 1


def test_pull_range_records_a_partial_run_on_candle_errors(tmp_path: Path) -> None:
    store = _archive(tmp_path)

    class FailingClient(FakeClient):
        def get_expired_historical_candles(self, *args, **kwargs):
            raise UpstoxDataError("boom")

    summary = pull_range(
        FailingClient(),
        store,
        date(2025, 4, 20),
        date(2025, 4, 24),
        max_lookback_days=180,
        observed_at=datetime(2025, 4, 25, 10, 0),
        rate_limiter=RateLimiter(min_interval_seconds=0),
    )
    assert summary.warnings != ()
    assert store.readiness_metrics().failed_runs == 0  # recorded as "partial", not "failed"
