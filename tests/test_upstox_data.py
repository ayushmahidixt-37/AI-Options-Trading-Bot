import json
from datetime import date

import pytest

from options_bot.upstox_data import UpstoxClient, UpstoxDataError


def _transport(status: int, body: dict[str, object] | str):
    text = body if isinstance(body, str) else json.dumps(body)

    def fake(method, url, headers, timeout_seconds):
        assert method == "GET"
        assert headers["Authorization"] == "Bearer test-token"
        assert timeout_seconds == 10
        fake.last_url = url
        fake.last_headers = headers
        return status, text

    return fake


def test_requires_access_token() -> None:
    with pytest.raises(UpstoxDataError):
        UpstoxClient("")


def test_sends_a_browser_style_user_agent_to_avoid_cloudflare_bot_blocks() -> None:
    transport = _transport(200, {"status": "success", "data": []})
    client = UpstoxClient("test-token", transport=transport)
    client.search_instruments("NIFTY")
    assert "python" not in transport.last_headers["User-Agent"].lower()
    assert "Mozilla" in transport.last_headers["User-Agent"]


def test_search_instruments_returns_data() -> None:
    transport = _transport(200, {"status": "success", "data": [{"instrument_key": "NSE_INDEX|Nifty 50"}]})
    client = UpstoxClient("test-token", transport=transport)
    result = client.search_instruments("NIFTY")
    assert result == [{"instrument_key": "NSE_INDEX|Nifty 50"}]


def test_get_expiries_parses_dates() -> None:
    transport = _transport(200, {"status": "success", "data": ["2025-04-24", "2025-05-01"]})
    client = UpstoxClient("test-token", transport=transport)
    assert client.get_expiries("NSE_INDEX|Nifty 50") == [date(2025, 4, 24), date(2025, 5, 1)]


def test_get_expired_option_contracts_returns_data() -> None:
    contract = {"expired_instrument_key": "NSE_FO|53806|24-04-2025", "strike_price": 24500}
    transport = _transport(200, {"status": "success", "data": [contract]})
    client = UpstoxClient("test-token", transport=transport)
    assert client.get_expired_option_contracts("NSE_INDEX|Nifty 50", date(2025, 4, 24)) == [contract]


def test_get_expired_historical_candles_returns_candle_rows() -> None:
    candle = ["2025-04-24T09:15:00+05:30", 100.0, 105.0, 99.0, 102.0, 1000, 500]
    transport = _transport(200, {"status": "success", "data": {"candles": [candle]}})
    client = UpstoxClient("test-token", transport=transport)
    candles = client.get_expired_historical_candles(
        "NSE_FO|53806|24-04-2025", date(2025, 4, 1), date(2025, 4, 24)
    )
    assert candles == [candle]
    assert "%7C" in transport.last_url


def test_rejects_unsupported_interval() -> None:
    client = UpstoxClient("test-token", transport=_transport(200, {}))
    with pytest.raises(UpstoxDataError, match="Unsupported"):
        client.get_expired_historical_candles(
            "NSE_FO|53806|24-04-2025", date(2025, 4, 1), date(2025, 4, 24), interval="2minute"
        )


def test_get_historical_candles_v3_returns_candle_rows() -> None:
    candle = ["2025-01-01T00:00:00+05:30", 50, 55, 49, 52, 100, 0]
    transport = _transport(200, {"status": "success", "data": {"candles": [candle]}})
    client = UpstoxClient("test-token", transport=transport)
    candles = client.get_historical_candles_v3(
        "NSE_INDEX|Nifty 50", "minutes", "5", date(2025, 1, 1), date(2025, 1, 2)
    )
    assert candles == [candle]


def test_missing_data_field_raises() -> None:
    transport = _transport(200, {"status": "success"})
    client = UpstoxClient("test-token", transport=transport)
    with pytest.raises(UpstoxDataError):
        client.search_instruments("NIFTY")


def test_unauthorized_raises_clear_reauth_message() -> None:
    transport = _transport(401, {"status": "error"})
    client = UpstoxClient("test-token", transport=transport)
    with pytest.raises(UpstoxDataError, match="renew UPSTOX_ACCESS_TOKEN"):
        client.search_instruments("NIFTY")


def test_plus_subscription_required_error_is_explicit() -> None:
    transport = _transport(
        400, {"status": "error", "errors": [{"errorCode": "UDAPI1149", "message": "nope"}]}
    )
    client = UpstoxClient("test-token", transport=transport)
    with pytest.raises(UpstoxDataError, match="Upstox Plus subscription"):
        client.get_expiries("NSE_INDEX|Nifty 50")


def test_network_failure_raises_a_friendly_error_not_a_raw_exception() -> None:
    def failing_transport(method, url, headers, timeout_seconds):
        raise OSError("Tunnel connection failed: 403 Forbidden")

    client = UpstoxClient("test-token", transport=failing_transport)
    with pytest.raises(UpstoxDataError, match="Could not reach Upstox"):
        client.search_instruments("NIFTY")


def test_malformed_json_raises() -> None:
    transport = _transport(200, "not-json")
    client = UpstoxClient("test-token", transport=transport)
    with pytest.raises(UpstoxDataError, match="malformed JSON"):
        client.search_instruments("NIFTY")
