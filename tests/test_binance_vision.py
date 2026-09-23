"""Tests for the Binance vision (data.binance.vision) adapter."""

from __future__ import annotations

import io
import zipfile

import pytest

from trading.data.binance_perp import DataQualityError, Timeframe
from trading.data.binance_vision import (
    _extract_single_csv,
    _iter_months,
    _latest_completed_month,
    months_ending_at,
    monthly_funding_url,
    monthly_kline_url,
    parse_funding_csv,
    parse_kline_csv,
)


def test_monthly_kline_url_layout():
    url = monthly_kline_url("BTCUSDT", Timeframe.D1, 2024, 3)
    assert url == (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        "BTCUSDT/1d/BTCUSDT-1d-2024-03.zip"
    )


def test_monthly_funding_url_layout():
    url = monthly_funding_url("ETHUSDT", 2024, 12)
    assert url == (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        "ETHUSDT/ETHUSDT-fundingRate-2024-12.zip"
    )


def test_iter_months_inclusive_range():
    months = list(_iter_months(start_year=2024, start_month=11, end_year=2025, end_month=2))
    assert months == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_iter_months_empty_when_start_after_end():
    assert list(_iter_months(start_year=2025, start_month=6, end_year=2025, end_month=3)) == []


def test_months_ending_at_walks_backward():
    pairs = months_ending_at(end_year=2025, end_month=3, lookback_months=5)
    assert pairs == [(2024, 11), (2024, 12), (2025, 1), (2025, 2), (2025, 3)]


def test_months_ending_at_crosses_year_boundary():
    pairs = months_ending_at(end_year=2025, end_month=1, lookback_months=3)
    assert pairs == [(2024, 11), (2024, 12), (2025, 1)]


def test_latest_completed_month_never_current():
    # 2025-06-15 UTC → latest completed month is 2025-05
    ts = 1749999999  # 2025-06-15 in UTC (approx)
    year, month = _latest_completed_month(ts)
    assert (year, month) < (2025, 6)


def test_latest_completed_month_wraps_january():
    # 2025-01-05 UTC → latest completed month is 2024-12
    ts = 1736035200  # 2025-01-05 00:00 UTC
    year, month = _latest_completed_month(ts)
    assert (year, month) == (2024, 12)


def test_parse_kline_csv_no_header():
    # Legacy dump: no header line.
    lines = [
        # open_time,open,high,low,close,volume,close_time,quote_volume,count,tb_vol,tb_qv,ignore
        "1704067200000,42000,42500,41800,42200,1000,1704153599000,42100000,1200,500,21100000,0",
        "1704153600000,42200,42700,42000,42600,900,1704239999000,42400000,1100,450,20800000,0",
    ]
    candles = parse_kline_csv("\n".join(lines), decision_at=2_000_000_000)
    assert len(candles) == 2
    assert candles[0].open == 42000
    assert candles[0].close == 42200
    assert candles[0].quote_volume == 42_100_000
    assert candles[1].close_time == 1_704_239_999


def test_parse_kline_csv_with_header():
    lines = [
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,tb_vol,tb_qv,ignore",
        "1704067200000,42000,42500,41800,42200,1000,1704153599000,42100000,1200,500,21100000,0",
    ]
    candles = parse_kline_csv("\n".join(lines), decision_at=2_000_000_000)
    assert len(candles) == 1


def test_parse_kline_csv_drops_bad_ohlc():
    lines = [
        # high < close → invalid OHLC → skipped, not raised
        "1704067200000,42000,41500,41800,42200,1000,1704153599000,42100000,1200,500,21100000,0",
        # valid row
        "1704153600000,42200,42700,42000,42600,900,1704239999000,42400000,1100,450,20800000,0",
    ]
    candles = parse_kline_csv("\n".join(lines), decision_at=2_000_000_000)
    assert len(candles) == 1
    assert candles[0].open == 42200


def test_parse_kline_csv_drops_future_candles():
    lines = [
        # visible before decision_at
        "1704067200000,42000,42500,41800,42200,1000,1704153599000,42100000,1200,500,21100000,0",
        # future close_time (> decision_at)
        "9999999600000,50000,50100,49900,50050,50,9999999999000,2500000,10,5,250000,0",
    ]
    candles = parse_kline_csv("\n".join(lines), decision_at=1_704_200_000)
    assert len(candles) == 1


def test_parse_funding_csv_newer_layout():
    csv_text = (
        "calc_time,last_funding_rate,funding_interval_hours\n"
        "1704067200000,0.00012,8\n"
        "1704096000000,-0.00005,8\n"
    )
    rows = parse_funding_csv(csv_text, symbol="BTCUSDT", decision_at=2_000_000_000)
    assert len(rows) == 2
    assert rows[0].funding_rate == pytest.approx(0.00012)
    assert rows[1].funding_rate == pytest.approx(-0.00005)
    assert rows[0].symbol == "BTCUSDT"


def test_parse_funding_csv_older_layout():
    # calc_time, funding_interval_hours (integer), last_funding_rate
    csv_text = (
        "1704067200000,8,0.00012\n"
        "1704096000000,8,-0.00005\n"
    )
    rows = parse_funding_csv(csv_text, symbol="ETHUSDT", decision_at=2_000_000_000)
    assert len(rows) == 2
    assert rows[0].funding_rate == pytest.approx(0.00012)


def test_parse_funding_csv_drops_out_of_range_rate():
    # A defensive skip; FundingRow rejects |rate| >= 1
    csv_text = "1704067200000,5.0,8\n"
    rows = parse_funding_csv(csv_text, symbol="BTCUSDT", decision_at=2_000_000_000)
    assert rows == []


def test_extract_single_csv_pulls_only_csv_entry():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.txt", "ignored")
        zf.writestr("BTCUSDT-1d-2024-03.csv", "1,2,3\n4,5,6\n")
    csv_text = _extract_single_csv(buf.getvalue())
    assert "1,2,3" in csv_text


def test_extract_single_csv_rejects_empty_archive():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    with pytest.raises(DataQualityError):
        _extract_single_csv(buf.getvalue())
