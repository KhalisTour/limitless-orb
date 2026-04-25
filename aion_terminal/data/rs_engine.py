from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import pandas as pd

from aion_terminal.utils.time_utils import utc_now_iso

RS_LOOKBACK_DAYS = 63
MA_DAILY_LONG = 200
MA_WEEKLY_LONG = 250
PERF_Q1 = 63
PERF_Q2 = 126
PERF_Q3 = 189
PERF_Q4 = 252
RS_WEIGHT_Q1 = 0.40
RS_WEIGHT_Q2 = 0.20
RS_WEIGHT_Q3 = 0.20
RS_WEIGHT_Q4 = 0.20
MIN_1Y_RETURN_PCT = 1.0
MIN_BARS_REQUIRED = 260
BATCH_SIZE = 100
SCAN_INTERVAL_DAYS = 2
RS_PASS_PERCENTILE = 75.0
MIN_CONDITIONS_TO_PASS = 3

logger = logging.getLogger(__name__)


@dataclass
class RSFeatures:
    ticker: str
    computed_at: str
    rs_score: float
    rs_percentile: float
    rs_new_high_63d: bool
    rs_new_high_before_price: bool
    price_above_200d_ma: bool
    price_above_50w_ma: bool
    return_1y_pct: float
    close: float
    conditions_met: int
    passes_screen: bool


def load_universe(
    iwm_path: str = "aion_terminal/data/iwm_holdings.txt",
    sp500_path: str = "aion_terminal/data/sp500_holdings.txt",
) -> list[str]:
    for path in (iwm_path, sp500_path):
        try:
            with open(path, encoding="utf-8"):
                pass
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Required holdings file not found: {path}") from exc

    with open(iwm_path, encoding="utf-8") as f:
        iwm_tickers = [line.strip() for line in f if line.strip()]

    with open(sp500_path, encoding="utf-8") as f:
        sp500_tickers = [line.strip() for line in f if line.strip()]

    return sorted(set(iwm_tickers + sp500_tickers))


def fetch_bars_batch(
    tickers: list[str],
    period: str = "2y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV bars from Yahoo Finance directly. No yfinance dependency."""
    import urllib.request
    import json
    import time

    # Map period string to range parameter
    period_to_range = {
        "2y": "2y", "1y": "1y", "6mo": "6mo", "3mo": "3mo", "5d": "5d"
    }
    range_param = period_to_range.get(period, "2y")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    result: dict[str, pd.DataFrame] = {}
    fetched = 0
    skipped = 0

    for ticker in tickers:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?interval={interval}&range={range_param}"
        )
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())

            chart = data.get("chart", {})
            result_data = chart.get("result")
            if not result_data:
                skipped += 1
                continue

            res = result_data[0]
            timestamps = res.get("timestamp", [])
            quote = res["indicators"]["quote"][0]

            if len(timestamps) < MIN_BARS_REQUIRED:
                skipped += 1
                continue

            df = pd.DataFrame({
                "Open":   quote.get("open", []),
                "High":   quote.get("high", []),
                "Low":    quote.get("low", []),
                "Close":  quote.get("close", []),
                "Volume": quote.get("volume", []),
            }, index=pd.to_datetime(timestamps, unit="s", utc=True))

            df = df.dropna(subset=["Close"])
            if len(df) < MIN_BARS_REQUIRED:
                skipped += 1
                continue

            result[ticker] = df
            fetched += 1

        except Exception as exc:
            logger.debug("Failed to fetch %s: %s", ticker, exc)
            skipped += 1

        # Small delay to avoid rate limiting
        time.sleep(0.05)

    logger.info(
        "Fetched bars: %s tickers; skipped: %s tickers (min bars: %s)",
        fetched, skipped, MIN_BARS_REQUIRED
    )
    return result


def _aligned(close: pd.Series, bench_close: pd.Series) -> tuple[pd.Series, pd.Series]:
    joined = pd.concat([close, bench_close], axis=1, join="inner").dropna()
    if joined.shape[1] < 2:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    return joined.iloc[:, 0], joined.iloc[:, 1]


def compute_rs_score(close: pd.Series, bench_close: pd.Series) -> float:
    c, b = _aligned(close, bench_close)
    if len(c) <= PERF_Q4:
        return 0.0

    rs_stock = (
        RS_WEIGHT_Q1 * (c / c.shift(PERF_Q1))
        + RS_WEIGHT_Q2 * (c / c.shift(PERF_Q2))
        + RS_WEIGHT_Q3 * (c / c.shift(PERF_Q3))
        + RS_WEIGHT_Q4 * (c / c.shift(PERF_Q4))
    )
    rs_bench = (
        RS_WEIGHT_Q1 * (b / b.shift(PERF_Q1))
        + RS_WEIGHT_Q2 * (b / b.shift(PERF_Q2))
        + RS_WEIGHT_Q3 * (b / b.shift(PERF_Q3))
        + RS_WEIGHT_Q4 * (b / b.shift(PERF_Q4))
    )

    rs_stock_val = rs_stock.iloc[-1]
    rs_bench_val = rs_bench.iloc[-1]

    if pd.isna(rs_stock_val) or pd.isna(rs_bench_val) or rs_bench_val == 0:
        return 0.0

    return float(100.0 * rs_stock_val / rs_bench_val)


def compute_rs_line(close: pd.Series, bench_close: pd.Series) -> pd.Series:
    c, b = _aligned(close, bench_close)
    if c.empty or b.empty:
        return pd.Series(dtype=float)
    return c / b


def compute_rs_new_high(rs_line: pd.Series, lookback: int = RS_LOOKBACK_DAYS) -> bool:
    if len(rs_line) < lookback:
        return False
    rolling_max = rs_line.rolling(lookback).max().iloc[-1]
    if pd.isna(rolling_max):
        return False
    return bool(rs_line.iloc[-1] >= rolling_max)


def compute_rs_new_high_before_price(
    rs_line: pd.Series,
    high: pd.Series,
    lookback: int = RS_LOOKBACK_DAYS,
) -> bool:
    aligned = pd.concat([rs_line, high], axis=1, join="inner").dropna()
    if len(aligned) < lookback:
        return False

    rs = aligned.iloc[:, 0]
    h = aligned.iloc[:, 1]

    rs_max = rs.rolling(lookback).max().iloc[-1]
    h_max = h.rolling(lookback).max().iloc[-1]
    if pd.isna(rs_max) or pd.isna(h_max):
        return False

    rs_at_high = rs.iloc[-1] >= rs_max
    price_at_high = h.iloc[-1] >= h_max
    return bool(rs_at_high and not price_at_high)


def compute_price_above_ma(close: pd.Series, period: int) -> bool:
    if len(close) < period:
        return False
    ma = close.rolling(period).mean().iloc[-1]
    if pd.isna(ma):
        return False
    return bool(close.iloc[-1] > ma)


def compute_1y_return(close: pd.Series) -> float:
    if len(close) < PERF_Q4:
        return -999.0
    base = close.iloc[-PERF_Q4]
    if base == 0:
        return -999.0
    return float((close.iloc[-1] / base - 1.0) * 100.0)


def compute_rs_features(
    ticker: str,
    close: pd.Series,
    high: pd.Series,
    bench_close: pd.Series,
) -> RSFeatures | None:
    ret_1y = compute_1y_return(close)
    if ret_1y < MIN_1Y_RETURN_PCT:
        return None

    rs_line = compute_rs_line(close, bench_close)
    close_value = float(close.dropna().iloc[-1]) if not close.dropna().empty else 0.0

    return RSFeatures(
        ticker=ticker,
        computed_at=utc_now_iso(),
        rs_score=compute_rs_score(close, bench_close),
        rs_percentile=0.0,
        rs_new_high_63d=compute_rs_new_high(rs_line, RS_LOOKBACK_DAYS),
        rs_new_high_before_price=compute_rs_new_high_before_price(rs_line, high, RS_LOOKBACK_DAYS),
        price_above_200d_ma=compute_price_above_ma(close, MA_DAILY_LONG),
        price_above_50w_ma=compute_price_above_ma(close, MA_WEEKLY_LONG),
        return_1y_pct=ret_1y,
        close=close_value,
        conditions_met=0,
        passes_screen=False,
    )


def rank_rs_features(features: list[RSFeatures]) -> list[RSFeatures]:
    if not features:
        return []

    ordered_by_score = sorted(features, key=lambda x: x.rs_score)
    total = len(ordered_by_score)

    for rank, feature in enumerate(ordered_by_score, start=1):
        feature.rs_percentile = 100.0 * rank / total

    for feature in features:
        checks = [
            feature.rs_percentile >= RS_PASS_PERCENTILE,
            feature.rs_new_high_63d,
            feature.rs_new_high_before_price,
            feature.price_above_200d_ma,
            feature.price_above_50w_ma,
        ]
        feature.conditions_met = sum(1 for c in checks if c)
        feature.passes_screen = feature.conditions_met >= MIN_CONDITIONS_TO_PASS

    return sorted(features, key=lambda x: x.rs_percentile, reverse=True)


def run_rs_scan(
    tickers: list[str] | None = None,
    bench_ticker: str = "SPY",
) -> list[RSFeatures]:
    universe = tickers or load_universe()
    if not universe:
        logger.info("Universe is empty. No scan run.")
        return []

    bench_frames = fetch_bars_batch([bench_ticker])
    bench_df = bench_frames.get(bench_ticker)
    if bench_df is None or "Close" not in bench_df:
        logger.warning("Benchmark %s unavailable; aborting scan.", bench_ticker)
        return []

    bench_close = bench_df["Close"]

    features: list[RSFeatures] = []
    fetched_tickers = 0
    skipped_tickers = 0

    for start in range(0, len(universe), BATCH_SIZE):
        batch = universe[start : start + BATCH_SIZE]
        batch_frames = fetch_bars_batch(batch)
        fetched_tickers += len(batch_frames)

        for ticker in batch:
            frame = batch_frames.get(ticker)
            if frame is None:
                skipped_tickers += 1
                continue
            if "Close" not in frame or "High" not in frame:
                skipped_tickers += 1
                continue
            feature = compute_rs_features(
                ticker=ticker,
                close=frame["Close"],
                high=frame["High"],
                bench_close=bench_close,
            )
            if feature is None:
                skipped_tickers += 1
                continue
            features.append(feature)

        if start + BATCH_SIZE < len(universe):
            time.sleep(1)

    ranked = rank_rs_features(features)
    passing = [f for f in ranked if f.passes_screen]

    logger.info(
        "RS scan complete total=%d fetched=%d skipped=%d passing=%d",
        len(universe),
        fetched_tickers,
        skipped_tickers,
        len(passing),
    )
    return passing
