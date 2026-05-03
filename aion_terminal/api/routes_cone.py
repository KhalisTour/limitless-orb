from __future__ import annotations

import json
import urllib.request
from datetime import datetime

from fastapi import APIRouter, HTTPException
from aion_terminal.app.config import settings

router = APIRouter(tags=["cone"])

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_days}d"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


@router.get("/cone/etf-bars")
def get_etf_bars(symbol: str, days: int = 90):
    """Fetch daily closes from Yahoo Finance for cone calibration."""
    symbol = symbol.upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    if settings.cache_only:
        return {"symbol": symbol, "bars": [], "warnings": ["cache_miss", "refresh_required"], "cache_only": True}

    days = max(10, min(days, 730))
    range_days = max(days * 2, 30)
    url = _YAHOO_URL.format(symbol=symbol, range_days=range_days)

    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"yahoo_fetch_failed: {exc}")

    chart = (data or {}).get("chart") or {}
    results = chart.get("result") or []
    if not results:
        raise HTTPException(status_code=404, detail=f"no bars for {symbol}")

    res = results[0]
    timestamps = res.get("timestamp") or []
    indicators = res.get("indicators") or {}
    quote_list = indicators.get("quote") or []
    quote = quote_list[0] if quote_list else {}
    closes = quote.get("close") or []

    bars: list[dict] = []
    for i, ts in enumerate(timestamps):
        if i >= len(closes):
            break
        close = closes[i]
        if close is None:
            continue
        bars.append(
            {
                "date": datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d"),
                "close": float(close),
            }
        )

    return {"symbol": symbol, "bars": bars[-days:], "cache_only": settings.cache_only}
