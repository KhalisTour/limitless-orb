from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aion_terminal.models.dto import RawChainSnapshotRecord, UnderlyingBarRecord
from aion_terminal.utils.math_utils import as_float, as_int
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

BASE_URL = "https://api.marketdata.app/v1"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RETRIES = 4


@dataclass(slots=True)
class FetchResult:
    ok: bool
    status_code: int | None
    payload: dict[str, Any] | list[Any] | None
    error: str | None = None


@dataclass(slots=True)
class ExpiryCandidate:
    expiry: str
    dte: int
    is_monthly_anchor: bool = False


class MarketDataClient:
    """Resilient MarketData API client with timeout/retry and graceful entitlement handling."""

    def __init__(self, token: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, retries: int = DEFAULT_RETRIES):
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._retries = retries

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> FetchResult:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{BASE_URL}{path}{query}"
        last_error: str | None = None

        for attempt in range(1, self._retries + 1):
            req = Request(url, headers={"Authorization": f"Bearer {self._token}"})
            try:
                with urlopen(req, timeout=self._timeout_seconds) as response:
                    status_code = getattr(response, "status", 200)
                    raw = response.read().decode("utf-8")
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        return FetchResult(ok=False, status_code=status_code, payload=None, error="invalid_json")

                    status_flag = payload.get("s") if isinstance(payload, dict) else None
                    if status_flag and status_flag not in {"ok", "success"}:
                        normalized = str(status_flag).lower()
                        if normalized in {"denied", "unauthorized", "forbidden", "not_entitled"}:
                            return FetchResult(ok=False, status_code=status_code, payload=payload, error="unauthorized")
                        return FetchResult(ok=False, status_code=status_code, payload=payload, error=f"api_status_{status_flag}")

                    return FetchResult(ok=True, status_code=status_code, payload=payload)
            except HTTPError as exc:
                if exc.code in (401, 403):
                    return FetchResult(ok=False, status_code=exc.code, payload=None, error="unauthorized")
                if exc.code == 404:
                    return FetchResult(ok=False, status_code=exc.code, payload=None, error="http_404")
                if exc.code == 429:
                    backoff_schedule = [5, 15, 45]
                    if attempt <= len(backoff_schedule):
                        wait = backoff_schedule[attempt - 1]
                        logger.warning("rate limited (429) on attempt %s/%s — backing off %ss", attempt, self._retries, wait)
                        time.sleep(wait)
                        continue
                    return FetchResult(ok=False, status_code=exc.code, payload=None, error="rate_limited")
                if exc.code >= 500:
                    last_error = f"server_error_{exc.code}"
                    time.sleep(0.25 * attempt)
                    continue
                return FetchResult(ok=False, status_code=exc.code, payload=None, error=f"http_{exc.code}")
            except TimeoutError:
                last_error = "timeout"
                time.sleep(0.25 * attempt)
            except URLError as exc:
                last_error = f"url_error:{exc.reason}"
                time.sleep(0.25 * attempt)
            except OSError as exc:
                last_error = f"socket_error:{exc.__class__.__name__}"
                time.sleep(0.25 * attempt)

        return FetchResult(ok=False, status_code=None, payload=None, error=last_error or "request_failed")

    def fetch_latest_quote(self, symbol: str) -> FetchResult:
        candidate_paths = [f"/stocks/quotes/{symbol}/", f"/stocks/quote/{symbol}/"]
        result = FetchResult(ok=False, status_code=None, payload=None, error="not_attempted")
        for path in candidate_paths:
            result = self._request_json(path)
            if result.ok or result.error == "unauthorized":
                return result
        return result

    def fetch_underlying_bars(self, symbol: str, timeframe: str, start_date: str, end_date: str) -> FetchResult:
        params = {"from": start_date, "to": end_date}
        candidate_paths = [f"/stocks/candles/{timeframe}/{symbol}/", f"/stocks/candles/{symbol}/"]
        result = FetchResult(ok=False, status_code=None, payload=None, error="not_attempted")
        for path in candidate_paths:
            result = self._request_json(path, params=params)
            if result.ok or result.error == "unauthorized":
                return result
        return result

    def fetch_option_expiries(self, symbol: str) -> FetchResult:
        candidate_paths = [f"/options/expirations/{symbol}/", f"/options/expiry/{symbol}/"]
        result = FetchResult(ok=False, status_code=None, payload=None, error="not_attempted")
        for path in candidate_paths:
            result = self._request_json(path)
            if result.ok or result.error == "unauthorized":
                return result
        return result

    def fetch_options_chain(self, symbol: str, expiration: str | None = None) -> FetchResult:
        params = {"expiration": expiration} if expiration else None
        return self._request_json(f"/options/chain/{symbol}/", params=params)

    def fetch_option_history(self, contract_symbol: str, start_date: str, end_date: str) -> FetchResult:
        params = {"from": start_date, "to": end_date}
        candidate_paths = [f"/options/candles/{contract_symbol}/", f"/options/history/{contract_symbol}/"]
        result = FetchResult(ok=False, status_code=None, payload=None, error="not_attempted")
        for path in candidate_paths:
            result = self._request_json(path, params=params)
            if result.ok or result.error == "unauthorized":
                return result
        return result


def _is_monthly_anchor(expiry_str: str) -> bool:
    d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    first = d.replace(day=1)
    first_friday = (4 - first.weekday()) % 7 + 1
    third_friday = first_friday + 14
    return d.day == third_friday and d.weekday() == 4


def _parse_expiry_candidates(payload: dict[str, Any] | list[Any] | None) -> list[ExpiryCandidate]:
    today = datetime.now(timezone.utc).date()
    out: list[ExpiryCandidate] = []
    seen: set[str] = set()

    def add(expiry_str: str, dte_value: int | None = None):
        if expiry_str in seen:
            return
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        except ValueError:
            return
        dte = dte_value if dte_value is not None else max((expiry_date - today).days, 0)
        out.append(ExpiryCandidate(expiry=expiry_str, dte=dte, is_monthly_anchor=_is_monthly_anchor(expiry_str)))
        seen.add(expiry_str)

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                expiry = item.get("expiry") or item.get("expiration")
                if expiry:
                    add(str(expiry), as_int(item.get("dte"), default=-1) if item.get("dte") is not None else None)
    elif isinstance(payload, dict):
        expiries = payload.get("expirations") or payload.get("expiry") or payload.get("expiration") or []
        if isinstance(expiries, list):
            for idx, item in enumerate(expiries):
                expiry_str = str(item)
                dtes = payload.get("dte") or []
                dte = as_int(dtes[idx], default=-1) if idx < len(dtes) else None
                add(expiry_str, dte if dte is not None and dte >= 0 else None)

    return sorted(out, key=lambda c: (c.dte, c.expiry))


def fallback_expiry_candidates(weeks_ahead: int = 10) -> list[ExpiryCandidate]:
    """Fallback weekly calendar when expiries endpoint is unavailable."""
    today = datetime.now(timezone.utc).date()
    candidates: list[ExpiryCandidate] = []
    for offset in range(0, weeks_ahead * 7 + 1):
        d = today + timedelta(days=offset)
        if d.weekday() == 4:  # Friday weekly cycle
            expiry = d.strftime("%Y-%m-%d")
            candidates.append(
                ExpiryCandidate(expiry=expiry, dte=offset, is_monthly_anchor=_is_monthly_anchor(expiry))
            )
    return candidates


def select_relevant_expiry_buckets(candidates: list[ExpiryCandidate]) -> list[ExpiryCandidate]:
    """Select up to 6 expiries within the next 14 days. Skip DTE < 2."""
    if not candidates:
        return []

    eligible = [c for c in candidates if 2 <= c.dte <= 14]
    eligible.sort(key=lambda c: (c.dte, c.expiry))
    return eligible[:6]


def discover_relevant_expiries(client: MarketDataClient, symbol: str) -> tuple[list[ExpiryCandidate], str | None]:
    """Discover expiries and bucket-select for ingestion. Returns (expiries, warning)."""
    fetch = client.fetch_option_expiries(symbol)
    if fetch.ok:
        parsed = _parse_expiry_candidates(fetch.payload)
        selected = select_relevant_expiry_buckets(parsed)
        if selected:
            return selected, None

    if fetch.error == "unauthorized":
        return [], "expiries_unauthorized"

    fallback = select_relevant_expiry_buckets(fallback_expiry_candidates())
    warning = "expiries_fallback_calendar"
    return fallback, warning


def _extract_quote_price(payload: dict[str, Any] | list[Any] | None) -> float | None:
    if not payload:
        return None
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            for key in ("last", "lastPrice", "price", "mid"):
                if key in first and first[key] is not None:
                    return as_float(first[key], default=0.0)
    if isinstance(payload, dict):
        for key in ("last", "lastPrice", "price", "mid"):
            if key in payload and payload[key] is not None:
                return as_float(payload[key], default=0.0)
        arr = payload.get("last") or payload.get("close") or payload.get("c")
        if isinstance(arr, list) and arr:
            return as_float(arr[-1], default=0.0)
    return None


def normalize_underlying_bars(
    symbol: str,
    timeframe: str,
    payload: dict[str, Any] | list[Any] | None,
    source: str = "marketdata",
) -> list[UnderlyingBarRecord]:
    now = utc_now_iso()
    if not payload:
        return []

    bars: list[UnderlyingBarRecord] = []
    if isinstance(payload, list):
        iterable = payload
    elif isinstance(payload, dict) and isinstance(payload.get("bars"), list):
        iterable = payload["bars"]
    else:
        times = payload.get("t") or payload.get("timestamp") or []
        opens = payload.get("o") or payload.get("open") or []
        highs = payload.get("h") or payload.get("high") or []
        lows = payload.get("l") or payload.get("low") or []
        closes = payload.get("c") or payload.get("close") or []
        vols = payload.get("v") or payload.get("volume") or []
        vwaps = payload.get("vw") or payload.get("vwap") or []

        for idx, ts in enumerate(times):
            bar_ts = datetime.fromtimestamp(as_int(ts), tz=timezone.utc).isoformat() if isinstance(ts, (int, float)) else str(ts)
            bars.append(
                UnderlyingBarRecord(
                    symbol=symbol,
                    timeframe=timeframe,
                    bar_ts=bar_ts,
                    open=as_float(opens[idx] if idx < len(opens) else None),
                    high=as_float(highs[idx] if idx < len(highs) else None),
                    low=as_float(lows[idx] if idx < len(lows) else None),
                    close=as_float(closes[idx] if idx < len(closes) else None),
                    volume=as_int(vols[idx] if idx < len(vols) else None),
                    vwap=as_float(vwaps[idx] if idx < len(vwaps) else None),
                    source=source,
                    created_at=now,
                    updated_at=now,
                )
            )
        return bars

    for raw in iterable:
        if not isinstance(raw, dict):
            continue
        raw_ts = raw.get("t") or raw.get("timestamp") or raw.get("time")
        bar_ts = datetime.fromtimestamp(as_int(raw_ts), tz=timezone.utc).isoformat() if isinstance(raw_ts, (int, float)) else str(raw_ts)
        bars.append(
            UnderlyingBarRecord(
                symbol=symbol,
                timeframe=timeframe,
                bar_ts=bar_ts,
                open=as_float(raw.get("o") or raw.get("open")),
                high=as_float(raw.get("h") or raw.get("high")),
                low=as_float(raw.get("l") or raw.get("low")),
                close=as_float(raw.get("c") or raw.get("close")),
                volume=as_int(raw.get("v") or raw.get("volume")),
                vwap=as_float(raw.get("vw") or raw.get("vwap")),
                source=source,
                created_at=now,
                updated_at=now,
            )
        )
    return bars


def normalize_chain_snapshots(
    symbol: str,
    payload: dict[str, Any] | None,
    dte_max: int,
    fallback_spot: float | None = None,
    source: str = "marketdata",
) -> tuple[list[RawChainSnapshotRecord], list[dict[str, Any]], float]:
    if not payload:
        return [], [], fallback_spot or 0.0

    now = utc_now_iso()
    spot = fallback_spot or 0.0
    underlying_prices = payload.get("underlyingPrice") or payload.get("underlying_price") or []
    if underlying_prices:
        spot = as_float(underlying_prices[0], default=spot)

    option_symbols = payload.get("optionSymbol") or []
    underlying_symbols = payload.get("underlying") or []
    strikes = payload.get("strike") or []
    expirations = payload.get("expiration") or []
    sides = payload.get("side") or []
    bids = payload.get("bid") or []
    asks = payload.get("ask") or []
    lasts = payload.get("last") or []
    marks = payload.get("mid") or payload.get("mark") or []
    deltas = payload.get("delta") or []
    gammas = payload.get("gamma") or []
    thetas = payload.get("theta") or []
    vegas = payload.get("vega") or []
    rhos = payload.get("rho") or []
    open_interests = payload.get("openInterest") or []
    volumes = payload.get("volume") or []
    ivs = payload.get("iv") or []
    dtes = payload.get("dte") or []

    research_rows: list[RawChainSnapshotRecord] = []
    legacy_rows: list[dict[str, Any]] = []

    for idx, option_symbol in enumerate(option_symbols):
        gamma = as_float(gammas[idx] if idx < len(gammas) else None)
        open_interest = as_int(open_interests[idx] if idx < len(open_interests) else None)
        dte = as_int(dtes[idx] if idx < len(dtes) else None)
        if dte > dte_max:
            continue

        expiry_raw = expirations[idx] if idx < len(expirations) else None
        expiry = (
            datetime.fromtimestamp(as_int(expiry_raw), tz=timezone.utc).strftime("%Y-%m-%d")
            if isinstance(expiry_raw, (int, float))
            else str(expiry_raw) if expiry_raw is not None else None
        )

        normalized_symbol = underlying_symbols[idx] if idx < len(underlying_symbols) else symbol
        strike = as_float(strikes[idx] if idx < len(strikes) else None)
        side = sides[idx] if idx < len(sides) else None
        iv = as_float(ivs[idx] if idx < len(ivs) else None)

        research_rows.append(
            RawChainSnapshotRecord(
                snapshot_ts=now,
                symbol=normalized_symbol,
                option_symbol=option_symbol,
                expiry=expiry,
                side=side,
                strike=strike,
                bid=as_float(bids[idx] if idx < len(bids) else None),
                ask=as_float(asks[idx] if idx < len(asks) else None),
                last=as_float(lasts[idx] if idx < len(lasts) else None),
                mark=as_float(marks[idx] if idx < len(marks) else None),
                iv=iv,
                delta=as_float(deltas[idx] if idx < len(deltas) else None),
                gamma=gamma,
                theta=as_float(thetas[idx] if idx < len(thetas) else None),
                vega=as_float(vegas[idx] if idx < len(vegas) else None),
                rho=as_float(rhos[idx] if idx < len(rhos) else None),
                open_interest=open_interest,
                volume=as_int(volumes[idx] if idx < len(volumes) else None),
                dte=dte,
                underlying_price=spot,
                source=source,
                created_at=now,
                updated_at=now,
            )
        )

        if gamma != 0.0 and open_interest != 0:
            legacy_rows.append(
                {
                    "symbol": normalized_symbol,
                    "option_symbol": option_symbol,
                    "strike": strike,
                    "expiry": expiry,
                    "type": side,
                    "gamma": gamma,
                    "open_interest": open_interest,
                    "iv": iv,
                    "dte": dte,
                    "underlying_price": spot,
                    "timestamp": now,
                }
            )

    return research_rows, legacy_rows, spot


def group_chain_by_expiry(rows: list[RawChainSnapshotRecord]) -> dict[str, dict[str, Any]]:
    """Grouped chain structure for expiry-aware dealer positioning analysis."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        expiry_key = row.expiry or "unknown"
        bucket = grouped.setdefault(
            expiry_key,
            {
                "expiry": expiry_key,
                "dte_min": row.dte,
                "dte_max": row.dte,
                "contracts": [],
                "contract_count": 0,
            },
        )

        bucket["contracts"].append(
            {
                "option_symbol": row.option_symbol,
                "side": row.side,
                "strike": row.strike,
                "gamma": row.gamma,
                "open_interest": row.open_interest,
                "iv": row.iv,
                "dte": row.dte,
                "mark": row.mark,
                "bid": row.bid,
                "ask": row.ask,
            }
        )
        bucket["contract_count"] += 1
        if row.dte is not None:
            if bucket["dte_min"] is None or row.dte < bucket["dte_min"]:
                bucket["dte_min"] = row.dte
            if bucket["dte_max"] is None or row.dte > bucket["dte_max"]:
                bucket["dte_max"] = row.dte

    return {k: grouped[k] for k in sorted(grouped.keys())}


def default_recent_window(days: int = 7) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()
