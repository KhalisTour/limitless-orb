from __future__ import annotations

import pandas as pd
import pytest

from aion_terminal.data.rs_engine import (
    RSFeatures,
    compute_1y_return,
    compute_price_above_ma,
    compute_rs_line,
    compute_rs_new_high,
    compute_rs_new_high_before_price,
    compute_rs_score,
    load_universe,
    rank_rs_features,
)


def make_series(values, name: str = "close"):
    return pd.Series(values, name=name)


def make_trending(n: int = 300, start: float = 100.0, drift: float = 0.001):
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift))
    return pd.Series(closes)


def _feature(score: float, ticker: str = "AAA") -> RSFeatures:
    return RSFeatures(
        ticker=ticker,
        computed_at="2026-01-01T00:00:00+00:00",
        rs_score=score,
        rs_percentile=0.0,
        rs_new_high_63d=True,
        rs_new_high_before_price=True,
        price_above_200d_ma=True,
        price_above_50w_ma=True,
        return_1y_pct=10.0,
        close=100.0,
        conditions_met=0,
        passes_screen=False,
    )


def test_compute_rs_score_outperformer():
    stock = make_trending(n=300, drift=0.002)
    bench = make_trending(n=300, drift=0.0005)
    assert compute_rs_score(stock, bench) > 100.0


def test_compute_rs_score_underperformer():
    stock = make_trending(n=300, drift=0.0)
    bench = make_trending(n=300, drift=0.001)
    assert compute_rs_score(stock, bench) < 100.0


def test_compute_rs_new_high_true():
    rs_line = make_series(list(range(1, 101)))
    assert compute_rs_new_high(rs_line, lookback=63) is True


def test_compute_rs_new_high_false():
    values = list(range(1, 91)) + [80 - i for i in range(10)]
    rs_line = make_series(values)
    assert compute_rs_new_high(rs_line, lookback=63) is False


def test_rs_new_high_before_price_early_leadership():
    close = make_series([100 + i for i in range(300)], name="close")
    bench = make_series([100 + 0.4 * i for i in range(300)], name="bench")
    rs_line = compute_rs_line(close, bench)

    highs = [100 + i for i in range(300)]
    highs[-1] = highs[-10]
    high_series = make_series(highs, name="high")

    assert compute_rs_new_high_before_price(rs_line, high_series, lookback=63) is True


def test_rs_new_high_before_price_no_divergence():
    close = make_series([100 + i for i in range(300)], name="close")
    bench = make_series([100 + 0.4 * i for i in range(300)], name="bench")
    rs_line = compute_rs_line(close, bench)

    high_series = make_series([100 + i for i in range(300)], name="high")
    assert compute_rs_new_high_before_price(rs_line, high_series, lookback=63) is False


def test_price_above_ma_true():
    close = make_trending(n=300, drift=0.002)
    assert compute_price_above_ma(close, period=200) is True


def test_price_above_ma_false():
    close = make_trending(n=300, drift=-0.001)
    assert compute_price_above_ma(close, period=200) is False


def test_1y_return_positive():
    close = make_trending(n=300, drift=0.001)
    assert compute_1y_return(close) > 0.0


def test_1y_return_insufficient_bars():
    close = make_trending(n=100, drift=0.001)
    assert compute_1y_return(close) == -999.0


def test_rank_rs_features_percentiles():
    features = [
        _feature(50.0, "A"),
        _feature(60.0, "B"),
        _feature(70.0, "C"),
        _feature(80.0, "D"),
        _feature(90.0, "E"),
    ]

    ranked = rank_rs_features(features)
    by_ticker = {f.ticker: f for f in ranked}
    assert by_ticker["E"].rs_percentile == pytest.approx(100.0)
    assert by_ticker["A"].rs_percentile == pytest.approx(20.0)


def test_rank_rs_features_sets_passes_screen():
    features = [_feature(88.0, "WIN")]
    ranked = rank_rs_features(features)
    assert ranked[0].rs_percentile == pytest.approx(100.0)
    assert ranked[0].passes_screen is True
    assert ranked[0].conditions_met >= 3


def test_load_universe_missing_file(tmp_path):
    missing_iwm = tmp_path / "missing_iwm.txt"
    missing_sp = tmp_path / "missing_sp500.txt"
    with pytest.raises(FileNotFoundError):
        load_universe(str(missing_iwm), str(missing_sp))


def test_load_universe_deduplicates(tmp_path):
    iwm = tmp_path / "iwm.txt"
    sp500 = tmp_path / "sp500.txt"

    iwm.write_text("AAPL\nMSFT\nTSLA\n", encoding="utf-8")
    sp500.write_text("MSFT\nNVDA\nAAPL\n", encoding="utf-8")

    universe = load_universe(str(iwm), str(sp500))
    assert universe == ["AAPL", "MSFT", "NVDA", "TSLA"]
