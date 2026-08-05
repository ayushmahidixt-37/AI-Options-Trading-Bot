"""Angel One instrument-master normalization and option-universe selection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime

from .domain import Instrument


def normalize_instruments(rows: Iterable[Mapping[str, object]]) -> list[Instrument]:
    """Convert Angel instrument-master rows into validated option instruments."""
    instruments: list[Instrument] = []
    for row in rows:
        exchange = str(row.get("exch_seg") or row.get("exchange") or "").upper()
        instrument_type = str(row.get("instrumenttype") or row.get("instrument") or "").upper()
        if exchange not in {"NFO", "BFO"} or "OPT" not in instrument_type:
            continue
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").upper()
        option_type = str(row.get("optiontype") or symbol[-2:]).upper()
        if option_type not in {"CE", "PE"}:
            continue
        try:
            strike = float(row.get("strike") or 0)
            if strike > 1_000:
                strike /= 100
            expiry = _parse_expiry(row.get("expiry"))
            instruments.append(
                Instrument(
                    symbol=symbol,
                    token=str(row.get("token") or row.get("symboltoken") or ""),
                    exchange=exchange,
                    underlying=str(row.get("name") or row.get("underlying") or "").upper(),
                    option_type=option_type,
                    lot_size=int(row.get("lotsize") or row.get("lotSize") or 0),
                    expiry=expiry,
                    strike=strike,
                )
            )
        except (TypeError, ValueError):
            continue
    return instruments


def _parse_expiry(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def nearest_expiry(instruments: Iterable[Instrument], today: date) -> date | None:
    expiries = sorted({item.expiry for item in instruments if item.expiry and item.expiry >= today})
    return expiries[0] if expiries else None


def select_near_atm(
    instruments: Iterable[Instrument], *, underlying: str, spot: float, today: date, strike_band: int = 2
) -> list[Instrument]:
    """Select CE and PE contracts around the closest strike for the nearest expiry."""
    if spot <= 0 or strike_band < 0:
        raise ValueError("spot must be positive and strike_band non-negative")
    candidates = [item for item in instruments if item.underlying == underlying.upper()]
    expiry = nearest_expiry(candidates, today)
    if expiry is None:
        return []
    chain = [item for item in candidates if item.expiry == expiry and item.strike is not None]
    strikes = sorted({float(item.strike) for item in chain})
    if not strikes:
        return []
    atm_index = min(range(len(strikes)), key=lambda index: abs(strikes[index] - spot))
    selected_strikes = set(
        strikes[max(0, atm_index - strike_band) : atm_index + strike_band + 1]
    )
    return sorted(
        (item for item in chain if item.strike in selected_strikes),
        key=lambda item: (float(item.strike or 0), item.option_type),
    )
