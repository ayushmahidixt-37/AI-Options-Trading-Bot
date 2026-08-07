from datetime import date

from options_bot.instruments import nearest_expiry, normalize_instruments, select_near_atm


def master_rows():
    rows = []
    for strike in (2450000, 2455000, 2460000):
        for side in ("CE", "PE"):
            rows.append(
                {
                    "exch_seg": "NFO",
                    "instrumenttype": "OPTIDX",
                    "symbol": f"NIFTY{strike}{side}",
                    "token": f"{strike}{side}",
                    "name": "NIFTY",
                    "optiontype": side,
                    "strike": strike,
                    "lotsize": 50,
                    "expiry": "07-AUG-2026",
                }
            )
    rows.append({"exch_seg": "NSE", "instrumenttype": "AMXIDX", "symbol": "NIFTY"})
    return rows


def test_normalize_and_select_near_atm() -> None:
    instruments = normalize_instruments(master_rows())
    assert len(instruments) == 6
    assert instruments[0].strike == 24500
    assert nearest_expiry(instruments, date(2026, 8, 3)) == date(2026, 8, 7)
    selected = select_near_atm(
        instruments, underlying="NIFTY", spot=24560, today=date(2026, 8, 3), strike_band=0
    )
    assert {item.option_type for item in selected} == {"CE", "PE"}
    assert {item.strike for item in selected} == {24550}


def test_normalize_skips_bad_strikes_and_handles_datetime_expiry() -> None:
    rows = [
        {
            "exch_seg": "NFO",
            "instrumenttype": "OPTSTK",
            "symbol": "ABC750CE",
            "token": "abc",
            "name": "ABC",
            "optiontype": "CE",
            "strike": "bad",
            "lotsize": 1,
            "expiry": "07-AUG-2026",
        },
        {
            "exch_seg": "NFO",
            "instrumenttype": "OPTSTK",
            "symbol": "ABC750PE",
            "token": "abc-pe",
            "name": "ABC",
            "optiontype": "PE",
            "strike": 75000,
            "lotsize": 1,
            "expiry": __import__("datetime").datetime(2026, 8, 7, 15, 30),
        },
    ]

    instruments = normalize_instruments(rows)

    assert len(instruments) == 1
    assert instruments[0].strike == 750
    assert instruments[0].expiry == date(2026, 8, 7)


def test_select_near_atm_fails_closed_when_no_valid_expiry() -> None:
    instrument = normalize_instruments(
        [
            {
                "exch_seg": "NFO",
                "instrumenttype": "OPTSTK",
                "symbol": "ABC750CE",
                "token": "abc",
                "name": "ABC",
                "optiontype": "CE",
                "strike": 75000,
                "lotsize": 1,
                "expiry": "not-a-date",
            }
        ]
    )

    assert select_near_atm(instrument, underlying="ABC", spot=750, today=date(2026, 8, 3)) == []
