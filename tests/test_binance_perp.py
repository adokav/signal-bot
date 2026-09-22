"""Tests for the Binance USDⓈ-M perpetual data adapter."""

from __future__ import annotations

import pytest

from trading.data.binance_perp import (
    Candle,
    DataQualityError,
    FundingRow,
    OpenInterestRow,
    Timeframe,
    last_closed_end_time_ms,
    parse_funding,
    parse_klines,
    parse_open_interest,
)


def _kline_row(open_time_ms: int, close_time_ms: int, price: float = 100.0, vol: float = 1.0):
    return [
        open_time_ms, str(price), str(price + 1.0), str(price - 1.0), str(price + 0.5),
        str(vol), close_time_ms, str(vol * price), 5, str(vol / 2), str(vol / 2 * price), "0",
    ]


def test_parse_klines_drops_a_single_open_bucket():
    interval = Timeframe.H1.seconds
    decision_at = 10 * interval  # exactly on a bucket boundary
    # Three closed buckets ending just before decision_at (freshness stays
    # inside the 2x-interval staleness budget).
    rows = [
        _kline_row(
            (decision_at - (3 - k) * interval) * 1000,
            (decision_at - (2 - k) * interval - 1) * 1000,
        )
        for k in range(3)
    ]
    # One open bucket at decision_at.
    rows.append(
        _kline_row(decision_at * 1000, (decision_at + interval - 1) * 1000)
    )
    candles = parse_klines(rows, timeframe=Timeframe.H1, decision_at=decision_at)
    assert len(candles) == 3
    assert candles[-1].close_time <= decision_at


def test_parse_klines_rejects_multiple_open_buckets():
    interval = Timeframe.H1.seconds
    decision_at = 5 * interval
    rows = [
        _kline_row(k * interval * 1000, ((k + 1) * interval - 1) * 1000)
        for k in range(3)
    ]
    # Two open buckets beyond decision_at.
    rows.append(_kline_row(decision_at * 1000, (decision_at + interval - 1) * 1000))
    rows.append(_kline_row((decision_at + interval) * 1000, (decision_at + 2 * interval - 1) * 1000))
    with pytest.raises(DataQualityError):
        parse_klines(rows, timeframe=Timeframe.H1, decision_at=decision_at)


def test_parse_klines_rejects_stale_series():
    interval = Timeframe.H1.seconds
    decision_at = 100 * interval
    # last close is 20 hours behind decision_at → stale (max = 2h)
    rows = [
        _kline_row((decision_at - 22 * interval) * 1000, (decision_at - 21 * interval - 1) * 1000),
        _kline_row((decision_at - 21 * interval) * 1000, (decision_at - 20 * interval - 1) * 1000),
    ]
    with pytest.raises(DataQualityError):
        parse_klines(rows, timeframe=Timeframe.H1, decision_at=decision_at)


def test_parse_klines_rejects_non_chronological():
    interval = Timeframe.H1.seconds
    decision_at = 10 * interval
    a = _kline_row(1 * interval * 1000, (2 * interval - 1) * 1000)
    b = _kline_row(0 * interval * 1000, (1 * interval - 1) * 1000)
    with pytest.raises(DataQualityError):
        parse_klines([a, b], timeframe=Timeframe.H1, decision_at=decision_at)


def test_parse_klines_rejects_bad_ohlc_geometry():
    interval = Timeframe.H1.seconds
    decision_at = 3 * interval
    row = [
        0, "100", "90", "110", "95", "1.0", 3599_000, "100", 1, "0.5", "50", "0",
    ]  # high < close
    with pytest.raises(DataQualityError):
        parse_klines([row], timeframe=Timeframe.H1, decision_at=decision_at)


def test_last_closed_end_time_aligns_to_bucket():
    interval = Timeframe.H4.seconds
    end_ms = last_closed_end_time_ms(timeframe=Timeframe.H4, decision_at=interval * 3 + 500)
    # decision inside bucket 3; last closed bucket is 2, whose end is (3 * 4h - 1)ms
    assert end_ms == interval * 3 * 1000 - 1


def test_parse_funding_visible_rows_only():
    decision_at = 10_000
    rows = [
        {"symbol": "btcusdt", "fundingTime": 8_000_000, "fundingRate": "0.0001"},
        {"symbol": "btcusdt", "fundingTime": 20_000_000, "fundingRate": "0.0002"},
    ]
    parsed = parse_funding(rows, decision_at=decision_at)
    assert len(parsed) == 1
    assert parsed[0].symbol == "BTCUSDT"
    assert parsed[0].funding_time == 8_000
    assert parsed[0].funding_rate == pytest.approx(0.0001)


def test_parse_funding_rejects_out_of_range_rates():
    rows = [{"symbol": "BTCUSDT", "fundingTime": 1_000_000, "fundingRate": "5.0"}]
    with pytest.raises(DataQualityError):
        parse_funding(rows, decision_at=10_000)


def test_parse_open_interest_sorts_and_filters():
    decision_at = 10_000
    rows = [
        {
            "symbol": "BTCUSDT",
            "timestamp": 8_000_000,
            "sumOpenInterest": "100.0",
            "sumOpenInterestValue": "10000000.0",
        },
        {
            "symbol": "BTCUSDT",
            "timestamp": 3_000_000,
            "sumOpenInterest": "90.0",
            "sumOpenInterestValue": "9000000.0",
        },
        {
            "symbol": "BTCUSDT",
            "timestamp": 20_000_000,
            "sumOpenInterest": "110.0",
            "sumOpenInterestValue": "11000000.0",
        },
    ]
    parsed = parse_open_interest(rows, decision_at=decision_at)
    assert len(parsed) == 2
    assert [row.snapshot_time for row in parsed] == [3_000, 8_000]


def test_candle_rejects_impossible_ohlc():
    with pytest.raises(DataQualityError):
        Candle(
            open_time=0, close_time=3600, available_at=3600,
            open=10, high=8, low=9, close=9.5,  # high < open
            volume=1.0, quote_volume=10.0,
        )


def test_funding_row_visibility_boundary():
    row = FundingRow(symbol="BTCUSDT", funding_time=1_000, available_at=1_000, funding_rate=0.0001)
    assert row.visible_at(1_000)
    assert not row.visible_at(999)


def test_oi_row_visibility_boundary():
    row = OpenInterestRow(
        symbol="BTCUSDT",
        snapshot_time=1_000,
        available_at=1_000,
        open_interest=100.0,
        open_interest_value=10_000.0,
    )
    assert row.visible_at(1_000)
    assert not row.visible_at(999)
