from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from options_bot.domain import Instrument
from options_bot.market_data import AngelOneMarketData, MarketDataError


INSTRUMENT = Instrument("NIFTY_TEST_CE", "123", "NFO", "NIFTY", "CE", 50, date(2026, 8, 7), 24550)


def test_market_data_adapter_uses_data_methods_only() -> None:
    class FakeApi:
        def ltpData(self, exchange, symbol, token):
            assert (exchange, symbol, token) == ("NFO", "NIFTY_TEST_CE", "123")
            return {"data": {"ltp": 101.5}}

        def getCandleData(self, payload):
            assert payload["symboltoken"] == "123"
            return {"data": [["2026-08-03T10:00:00+05:30", 1, 2, 1, 2, 10]]}

    now = datetime(2026, 8, 3, 10, tzinfo=ZoneInfo("Asia/Kolkata"))
    provider = AngelOneMarketData(FakeApi())
    assert provider.quote(INSTRUMENT, now).price == 101.5
    assert len(provider.candles(INSTRUMENT, "ONE_MINUTE", now, now)) == 1


def test_invalid_ltp_fails_closed() -> None:
    class BadApi:
        def ltpData(self, *args):
            return {"data": {"ltp": None}}

    now = datetime(2026, 8, 3, 10, tzinfo=ZoneInfo("Asia/Kolkata"))
    with pytest.raises(MarketDataError):
        AngelOneMarketData(BadApi()).quote(INSTRUMENT, now)
