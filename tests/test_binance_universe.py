from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from trading.data import binance_universe as bu
from trading.data.binance_perp import DataQualityError

NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _listing(symbols, *, truncated=False, marker=None):
    prefixes = "".join(
        f"<CommonPrefixes><Prefix>{bu.SPOT_KLINES_PREFIX}{s}/</Prefix></CommonPrefixes>" for s in symbols
    )
    extra = f"<NextMarker>{marker}</NextMarker>" if marker else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{NS}">'
        f"<Prefix>{bu.SPOT_KLINES_PREFIX}</Prefix><IsTruncated>{'true' if truncated else 'false'}</IsTruncated>"
        f"{extra}{prefixes}</ListBucketResult>"
    )


class _Response:
    def __init__(self, status_code=200, text="", content=b""):
        self.status_code = status_code
        self.text = text
        self.content = content


class _Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        handler = self.routes(url, params or {})
        return handler if isinstance(handler, _Response) else _Response(404)


def _zip(rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", "\n".join(",".join(str(x) for x in r) for r in rows))
    return buffer.getvalue()


def _kline_rows(start, count, step, *, price=10.0, quote_volume=1000.0):
    rows = []
    for i in range(count):
        t = (start + i * step) * 1000
        rows.append([t, price, price * 1.01, price * 0.99, price, 1.0, t + step * 1000 - 1, quote_volume, 1, 0, 0, 0])
    return rows


def test_parse_listing_reads_prefixes_and_continuation():
    symbols, marker = bu.parse_listing(_listing(["BTCUSDT", "LUNAUSDT"], truncated=True, marker="m1"))
    assert symbols == ["BTCUSDT", "LUNAUSDT"] and marker == "m1"
    symbols, marker = bu.parse_listing(_listing(["ETHUSDT"]))
    assert symbols == ["ETHUSDT"] and marker is None


@pytest.mark.parametrize("xml", ["<not xml", _listing(["BTCUSDT"], truncated=True)])
def test_broken_listing_fails_closed(xml):
    with pytest.raises(DataQualityError):
        bu.parse_listing(xml)


def test_listing_follows_pages():
    pages = {None: _listing(["AUSDT", "BUSDT"], truncated=True, marker="p2"), "p2": _listing(["CUSDT"])}
    session = _Session(lambda url, params: _Response(text=pages[params.get("marker")]))
    assert bu.list_spot_symbols(session) == ["AUSDT", "BUSDT", "CUSDT"]
    assert session.calls[1][1]["marker"] == "p2"


def test_filter_uses_live_identity_rules():
    pairs = bu.filter_usdt_pairs(["BTCUSDT", "BTCUPUSDT", "USDCUSDT", "FDUSDUSDT", "ETHBTC", "LUNAUSDT"])
    assert pairs.eligible == ("BTCUSDT", "LUNAUSDT")
    assert set(pairs.stable) == {"USDCUSDT", "FDUSDUSDT"}
    assert pairs.leveraged == ("BTCUPUSDT",)


def test_get_retries_then_fails_closed_without_leaking_text():
    session = _Session(lambda url, params: _Response(500))
    sleeps = []
    with pytest.raises(bu.DownloadError) as info:
        bu._get(session, "https://example.invalid/x?secret=1", sleep=sleeps.append)
    assert len(session.calls) == bu.RETRIES and len(sleeps) == bu.RETRIES
    assert "secret" not in str(info.value) and "HTTP 500" in str(info.value)
    assert bu._get(_Session(lambda url, params: _Response(404)), "u", sleep=sleeps.append) is None


def test_candidate_months_rank_daily_and_add_neighbours():
    day = int(datetime(2024, 3, 10, tzinfo=timezone.utc).timestamp())
    daily = {
        "AUSDT": {day: 5e6},
        "BUSDT": {day: 4e6},
        "CUSDT": {day: 3e6},       # rank 3: outside rank_limit=2
        "DUSDT": {day + 86_400: 5e5},  # rank 1 that day but below the volume floor
    }
    window = [(2024, 2), (2024, 3)]
    plan = bu.candidate_months(daily, window=window, rank_limit=2, min_quote_volume=1e6)
    assert plan == {"AUSDT": [(2024, 2), (2024, 3)], "BUSDT": [(2024, 2), (2024, 3)]}


def test_build_universe_end_to_end_keeps_delisted_pairs(tmp_path):
    decision_at = int(datetime(2024, 4, 15, tzinfo=timezone.utc).timestamp())
    march = int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp())
    listing = [f"C{k:03d}USDT" for k in range(bu.MIN_USDT_PAIRS)] + ["LUNAUSDT", "BTCUSDT", "USDCUSDT"]

    def routes(url, params):
        if url == bu.S3_LIST_URL:
            return _Response(text=_listing(listing))
        name = url.rsplit("/", 1)[-1]
        if "2024-03" not in name:
            return None
        if name.startswith("BTCUSDT-1d"):
            return _Response(content=_zip(_kline_rows(march, 31, 86_400, quote_volume=9e9)))
        if name.startswith("LUNAUSDT-1d"):
            return _Response(content=_zip(_kline_rows(march, 10, 86_400, quote_volume=5e9)))
        if name.startswith("BTCUSDT-15m"):
            return _Response(content=_zip(_kline_rows(march, 31 * 96, 900, price=60000.0)))
        if name.startswith("LUNAUSDT-15m"):  # delisted after ten days
            return _Response(content=_zip(_kline_rows(march, 10 * 96, 900, price=0.5)))
        return None

    manifest = bu.build_universe(tmp_path, lookback_months=2, decision_at=decision_at,
                                 rank_limit=2, session=_Session(routes))
    assert manifest["candidates"] == 2 and set(manifest["symbols"]) == {"BTCUSDT", "LUNAUSDT"}
    assert manifest["stopped_before_window_end"] == ["LUNAUSDT"]
    assert manifest["can_authorize_trade"] is False
    assert (tmp_path / "15m" / "LUNAUSDT.parquet").exists()
    assert json.loads((tmp_path / "manifest.json").read_text())["window"] == ["2024-02", "2024-03"]


def test_an_unpublished_last_month_does_not_make_everything_delisted(tmp_path):
    decision_at = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp())  # March not published yet
    feb = int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp())
    listing = [f"C{k:03d}USDT" for k in range(bu.MIN_USDT_PAIRS)] + ["BTCUSDT"]

    def routes(url, params):
        if url == bu.S3_LIST_URL:
            return _Response(text=_listing(listing))
        name = url.rsplit("/", 1)[-1]
        if name == "BTCUSDT-1d-2024-02.zip":
            return _Response(content=_zip(_kline_rows(feb, 29, 86_400, quote_volume=9e9)))
        if name == "BTCUSDT-15m-2024-02.zip":
            return _Response(content=_zip(_kline_rows(feb, 29 * 96, 900, price=60000.0)))
        return None

    manifest = bu.build_universe(tmp_path, lookback_months=2, decision_at=decision_at, session=_Session(routes))
    assert manifest["symbols"]["BTCUSDT"]["months"] == ["2024-02", "2024-03"]
    assert manifest["stopped_before_window_end"] == []


def test_too_small_listing_is_refused(tmp_path):
    session = _Session(lambda url, params: _Response(text=_listing(["BTCUSDT"])) if url == bu.S3_LIST_URL else None)
    with pytest.raises(DataQualityError):
        bu.build_universe(tmp_path, lookback_months=1, decision_at=1_712_000_000, session=session)
