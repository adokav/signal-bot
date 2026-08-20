from __future__ import annotations

import pytest

from acce_unified.tactical_long import DataQualityError
from acce_unified.tactical_long_data import (
    MexcTacticalMarketData,
    Quote,
    TacticalTimeframe,
)


def _row(open_time: int, close_time: int, *, close: float = 100.0):
    return [
        open_time * 1000,
        "99",
        "101",
        "98",
        str(close),
        "10",
        close_time * 1000,
    ]


def test_last_closed_request_never_targets_current_open_bucket():
    decision_at = 10_001
    end_ms = MexcTacticalMarketData.last_closed_end_time_ms(
        timeframe=TacticalTimeframe.M5,
        decision_at=decision_at,
    )
    assert end_ms == 9_899_999
    assert end_ms < decision_at * 1000


def test_completed_rows_are_parsed_chronologically():
    rows = [
        _row(9_000, 9_299),
        _row(9_300, 9_599),
        _row(9_600, 9_899),
    ]
    candles = MexcTacticalMarketData.parse_kline_rows(
        rows,
        timeframe=TacticalTimeframe.M5,
        decision_at=10_000,
    )
    assert [row.close_time for row in candles] == [9_299, 9_599, 9_899]


def test_single_current_open_candle_is_discarded_at_provider_boundary():
    rows = [
        _row(9_300, 9_599),
        _row(9_600, 9_899),
        _row(9_900, 10_199),
    ]
    candles = MexcTacticalMarketData.parse_kline_rows(
        rows,
        timeframe=TacticalTimeframe.M5,
        decision_at=10_000,
    )
    assert [row.close_time for row in candles] == [9_599, 9_899]


def test_multiple_future_or_open_rows_still_fail_closed():
    rows = [
        _row(9_600, 9_899),
        _row(9_900, 10_199),
        _row(10_200, 10_499),
    ]
    with pytest.raises(DataQualityError, match="multiple future or open"):
        MexcTacticalMarketData.parse_kline_rows(
            rows,
            timeframe=TacticalTimeframe.M5,
            decision_at=10_000,
        )


def test_payload_with_no_closed_candle_fails_closed():
    rows = [_row(9_900, 10_199)]
    with pytest.raises(DataQualityError, match="no closed candles"):
        MexcTacticalMarketData.parse_kline_rows(
            rows,
            timeframe=TacticalTimeframe.M5,
            decision_at=10_000,
        )


def test_stale_candle_set_fails_closed():
    rows = [_row(8_700, 8_999)]
    with pytest.raises(DataQualityError, match="stale"):
        MexcTacticalMarketData.parse_kline_rows(
            rows,
            timeframe=TacticalTimeframe.M5,
            decision_at=10_000,
        )


def test_malformed_rows_are_not_partially_accepted():
    with pytest.raises(DataQualityError, match="malformed"):
        MexcTacticalMarketData.parse_kline_rows(
            [[1, 2, 3]],
            timeframe=TacticalTimeframe.M15,
            decision_at=10_000,
        )


def test_quote_computes_execution_spread():
    quote = Quote(
        symbol="BTCUSDT",
        observed_at=10_000,
        available_at=10_000,
        bid=99.0,
        ask=101.0,
    )
    assert quote.midpoint == 100.0
    assert quote.spread_bps == pytest.approx(200.0)


def test_quote_rejects_inverted_market():
    with pytest.raises(DataQualityError, match="bid/ask"):
        Quote(
            symbol="ETHUSDT",
            observed_at=10_000,
            available_at=10_000,
            bid=101.0,
            ask=100.0,
        )


def test_adapter_never_accepts_another_execution_venue_symbol():
    adapter = MexcTacticalMarketData(candle_limit=60)
    with pytest.raises(ValueError, match="unsupported tactical symbol"):
        adapter.fetch_candles(
            "SOLUSDT",
            TacticalTimeframe.M5,
            decision_at=10_000,
        )
