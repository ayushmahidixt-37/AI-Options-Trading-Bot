from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from options_bot.config import Settings
from options_bot.domain import Instrument, PaperOrderRequest, Quote
from options_bot.risk import RiskRejected
from options_bot.runner import build_application

IST = ZoneInfo("Asia/Kolkata")


def app(tmp_path: Path, **overrides: str):
    env = {
        "DATA_DIR": str(tmp_path),
        "DATABASE_PATH": str(tmp_path / "paper.sqlite3"),
        "PAPER_FEE_PER_ORDER": "20",
        "PAPER_SLIPPAGE_BPS": "25",
        **overrides,
    }
    return build_application(Settings.from_env(env))


def request(now: datetime, *, symbol: str = "NIFTY_TEST_CE", lots: int = 1, price: float = 100, stop: float = 95):
    instrument = Instrument(symbol, "123", "NFO", "NIFTY", "CE", 50)
    return PaperOrderRequest(
        instrument=instrument,
        lots=lots,
        quote=Quote(symbol, price, now - timedelta(seconds=1)),
        stop_price=stop,
        strategy="test_v1",
        reason="unit test",
    )


def market_time() -> datetime:
    return datetime(2026, 8, 3, 10, 0, tzinfo=IST)


def test_buy_and_close_use_conservative_slippage_and_fees(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()
    order_id = application.paper_broker.buy(request(now), now)
    position = application.ledger.open_positions()[0]
    assert position["id"] == order_id
    assert position["lots"] == 1
    assert position["lot_size"] == 50
    assert position["units"] == 50
    assert position["entry_fill_price"] == 100.25

    pnl = application.paper_broker.close(
        order_id,
        Quote("NIFTY_TEST_CE", 110, now + timedelta(seconds=1)),
        now + timedelta(seconds=2),
        "target",
    )
    assert pnl == 434.0
    assert not application.ledger.open_positions()
    account = application.ledger.account()
    assert account["realized_pnl"] == 434.0
    assert account["fees_paid"] == 40.0


def test_stale_quote_is_rejected(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()
    proposed = request(now)
    stale = PaperOrderRequest(
        proposed.instrument,
        1,
        Quote(proposed.instrument.symbol, 100, now - timedelta(seconds=10)),
        95,
        "test",
        "stale",
    )
    with pytest.raises(RiskRejected, match="stale"):
        application.paper_broker.buy(stale, now)


def test_lot_risk_and_position_limits_are_enforced(tmp_path: Path) -> None:
    application = app(tmp_path, MAX_OPEN_POSITIONS="1")
    now = market_time()
    with pytest.raises(RiskRejected, match="Lot limit"):
        application.paper_broker.buy(request(now, lots=2), now)
    with pytest.raises(RiskRejected, match="Maximum loss"):
        application.paper_broker.buy(request(now, stop=90), now)
    application.paper_broker.buy(request(now), now)
    with pytest.raises(RiskRejected, match="Open-position"):
        application.paper_broker.buy(request(now, symbol="NIFTY_OTHER_CE"), now)


def test_default_profile_allows_two_distinct_open_paper_positions(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()

    application.paper_broker.buy(request(now, symbol="NIFTY_FIRST_CE"), now)
    application.paper_broker.buy(request(now, symbol="NIFTY_SECOND_CE"), now)

    assert len(application.ledger.open_positions()) == 2
    with pytest.raises(RiskRejected, match="Open-position"):
        application.paper_broker.buy(request(now, symbol="NIFTY_THIRD_CE"), now)


def test_zero_daily_trade_limit_allows_paper_collection_until_other_guard_fails(
    tmp_path: Path,
) -> None:
    application = app(tmp_path, MAX_TRADES_PER_DAY="0")
    now = market_time()
    for index in range(5):
        symbol = f"NIFTY_{index}_CE"
        order_id = application.paper_broker.buy(request(now, symbol=symbol), now)
        application.paper_broker.close(
            order_id,
            Quote(symbol, 101, now + timedelta(seconds=1)),
            now + timedelta(seconds=2),
            "collection-test",
        )

    assert application.ledger.trades_on("2026-08-03") == 5


def test_capital_summary_reports_open_premium_used_and_available(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()
    application.paper_broker.buy(request(now), now)

    summary = application.ledger.capital_summary()

    assert summary["premium_committed"] == 5012.5
    assert summary["open_entry_fees"] == 20
    assert summary["capital_used"] == 5032.5
    assert summary["capital_available"] == 94967.5
    assert summary["open_positions"] == 1


def test_risk_includes_adverse_fill_and_both_order_fees(tmp_path: Path) -> None:
    application = app(tmp_path, MAX_LOSS_PER_TRADE="300")
    now = market_time()
    # Raw stop risk is 250, but adverse fill and two ₹20 fees push it over 300.
    with pytest.raises(RiskRejected, match="Maximum loss"):
        application.paper_broker.buy(request(now), now)


def test_daily_trade_limit_persists_across_restarts(tmp_path: Path) -> None:
    application = app(tmp_path, MAX_TRADES_PER_DAY="1")
    now = market_time()
    order_id = application.paper_broker.buy(request(now), now)
    application.paper_broker.close(
        order_id,
        Quote("NIFTY_TEST_CE", 101, now + timedelta(seconds=1)),
        now + timedelta(seconds=2),
        "test",
    )
    restarted = app(tmp_path, MAX_TRADES_PER_DAY="1")
    with pytest.raises(RiskRejected, match="Daily trade limit"):
        restarted.paper_broker.buy(request(now, symbol="NIFTY_OTHER_CE"), now)


def test_database_rolls_back_failed_close(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()
    order_id = application.paper_broker.buy(request(now), now)
    with pytest.raises(RuntimeError):
        application.ledger.close(999, at=now.isoformat(), price=90, fee=20, pnl=-500, reason="bad")
    assert application.ledger.open_positions()[0]["id"] == order_id
    assert application.ledger.account()["realized_pnl"] == 0


def test_unique_open_symbol_is_database_enforced(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()
    application.paper_broker.buy(request(now), now)
    with pytest.raises(sqlite3.IntegrityError):
        values = dict(application.ledger.open_positions()[0])
        values.pop("id")
        values["client_order_id"] = "different"
        application.ledger.insert_open(values)


def test_daily_loss_circuit_breaker_resets_by_trading_date(tmp_path: Path) -> None:
    application = app(tmp_path, MAX_DAILY_NET_LOSS="100")
    monday = market_time()
    order_id = application.paper_broker.buy(request(monday), monday)
    application.paper_broker.close(
        order_id,
        Quote("NIFTY_TEST_CE", 97, monday + timedelta(seconds=1)),
        monday + timedelta(seconds=2),
        "loss",
    )
    with pytest.raises(RiskRejected, match="Daily loss"):
        application.paper_broker.buy(request(monday, symbol="NIFTY_OTHER_CE"), monday)

    tuesday = monday + timedelta(days=1)
    application.paper_broker.buy(request(tuesday, symbol="NIFTY_OTHER_CE"), tuesday)


def test_buy_records_open_order_and_fee_atomically(tmp_path: Path) -> None:
    application = app(tmp_path)
    now = market_time()
    order_id = application.paper_broker.buy(request(now), now)

    assert application.ledger.open_positions()[0]["id"] == order_id
    assert application.ledger.account()["fees_paid"] == 20.0
