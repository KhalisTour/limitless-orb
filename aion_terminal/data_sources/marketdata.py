from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from aion_terminal.utils.math_utils import as_float, as_int
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


def next_monthly_expiries(n: int = 3) -> list[str]:
    """Generate the next n third-Friday monthly expiries in YYYY-MM-DD format."""
    expiries: list[str] = []
    today = datetime.now(timezone.utc).date()
    year, month = today.year, today.month

    while len(expiries) < n:
        first_day = datetime(year, month, 1)
        first_friday = (4 - first_day.weekday()) % 7 + 1
        third_friday = first_friday + 14
        expiry = datetime(year, month, third_friday).date()
        if expiry > today:
            expiries.append(expiry.strftime("%Y-%m-%d"))
        month += 1
        if month > 12:
            month = 1
            year += 1

    return expiries


def fetch_and_normalize(
    ticker: str,
    token: str,
    api_url_template: str,
    dte_max: int = 60,
) -> tuple[list[dict[str, Any]], float]:
    """Fetch MarketData chains and normalize into a row-oriented list."""
    contracts_all: list[dict[str, Any]] = []
    spot = 0.0

    for expiry_date in next_monthly_expiries(3):
        headers = {"Authorization": f"Bearer {token}"}
        params = {"expiration": expiry_date}
        response = requests.get(
            api_url_template.format(ticker=ticker),
            headers=headers,
            params=params,
            timeout=30,
        )
        if response.status_code not in (200, 203):
            logger.warning("Skipping %s %s due to status=%s", ticker, expiry_date, response.status_code)
            continue

        payload = response.json()
        if payload.get("s") != "ok":
            logger.warning("Skipping %s %s due to payload status=%s", ticker, expiry_date, payload.get("s"))
            continue

        underlying_prices = payload.get("underlyingPrice") or []
        if spot == 0.0 and underlying_prices:
            spot = as_float(underlying_prices[0])

        timestamp = utc_now_iso()
        option_symbols = payload.get("optionSymbol") or []
        underlying_symbols = payload.get("underlying") or []
        strikes = payload.get("strike") or []
        expirations = payload.get("expiration") or []
        sides = payload.get("side") or []
        gammas = payload.get("gamma") or []
        open_interests = payload.get("openInterest") or []
        ivs = payload.get("iv") or []
        dtes = payload.get("dte") or []

        for idx, option_symbol in enumerate(option_symbols):
            gamma = as_float(gammas[idx] if idx < len(gammas) else None)
            open_interest = as_int(open_interests[idx] if idx < len(open_interests) else None)
            dte = as_int(dtes[idx] if idx < len(dtes) else None)

            if gamma == 0.0 or open_interest == 0 or dte > dte_max:
                continue

            expiry_ts = expirations[idx] if idx < len(expirations) else None
            expiry = datetime.fromtimestamp(as_int(expiry_ts), tz=timezone.utc).strftime("%Y-%m-%d") if expiry_ts else None

            contracts_all.append(
                {
                    "symbol": underlying_symbols[idx] if idx < len(underlying_symbols) else ticker,
                    "option_symbol": option_symbol,
                    "strike": as_float(strikes[idx] if idx < len(strikes) else None),
                    "expiry": expiry,
                    "type": sides[idx] if idx < len(sides) else None,
                    "gamma": gamma,
                    "open_interest": open_interest,
                    "iv": as_float(ivs[idx] if idx < len(ivs) else None),
                    "dte": dte,
                    "underlying_price": spot,
                    "timestamp": timestamp,
                }
            )

    logger.info(
        "Fetched %s contracts for %s across expiries=%s spot=%s",
        len(contracts_all),
        ticker,
        sorted({c['expiry'] for c in contracts_all if c.get('expiry')}),
        spot,
    )
    return contracts_all, spot
