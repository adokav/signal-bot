from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest

pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from trading.data import binance_carry_universe as cu
from trading.data.binance_perp import DataQualityError
from trading.data.binance_universe import DownloadError

NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _listing(prefix, names, *, truncated=False, marker=None):
    body = "".join(f"<CommonPrefixes><Prefix>{prefix}{n}/</Prefix></CommonPrefixes>" for n in names)
    extra = f"<NextMarker>{marker}</NextMarker>" if marker else ""
    return (f'<ListBucketResult xmlns="{NS}"><IsTruncated>{"true" if truncated else "false"}</IsTruncated>'
            f"{extra}{body}</ListBucketResult>")


class _Response:
    def __init__(self, status_code=200, text="", content=b""):
        self.status_code, self.text, self.content = status_code, text, content


class _Session:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, params=None, timeout=None):
        result = self.routes(url, params or {})
        return result if isinstance(result, _Response) else _Response(404)


def _zip(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", text)
    return buffer.getvalue()


def _klines_csv(start, count, step, *, price=10.0, quote_volume=1e9, micro=False):
    scale = 1_000_000 if micro else 1000
    rows = []
    for i in range(count):
        t = (start + i * step) * scale
        rows.append(f"{t},{price},{price * 1.01},{price * 0.99},{price},1,{t + step * scale - 1},{quote_volume},1,0,0,0")
    return "\n".join(rows)


def test_listing_pages_and_prefix():
    pages = {None: _listing(cu.PERP_PREFIX, ["AUSDT"], truncated=True, marker="m"),
             "m": _listing(cu.PERP_PREFIX, ["BUSDT"])}
    session = _Session(lambda url, p: _Response(text=pages[p.get("marker")]))
    assert cu.list_symbols(session, cu.PERP_PREFIX) == ["AUSDT", "BUSDT"]
    with pytest.raises(DataQualityError):
        cu.parse_listing(_listing(cu.PERP_PREFIX, ["X"], truncated=True), cu.PERP_PREFIX)


def test_perps_map_to_spot_including_contract_multipliers():
    mapping = cu.map_perps_to_spot(
        ["BTCUSDT", "1000PEPEUSDT", "1000SATSUSDT", "1MBABYDOGEUSDT", "USDCUSDT", "NOSPOTUSDT", "BTCBUSD"],
        ["BTCUSDT", "PEPEUSDT", "1000SATSUSDT", "BABYDOGEUSDT"],
    )
    assert mapping.mapped == {"BTCUSDT": "BTCUSDT", "1000PEPEUSDT": "PEPEUSDT",
                              "1000SATSUSDT": "1000SATSUSDT", "1MBABYDOGEUSDT": "BABYDOGEUSDT"}
    assert mapping.no_spot == ("NOSPOTUSDT",) and mapping.stable == ("USDCUSDT",)


@pytest.mark.parametrize("text", [
    "calc_time,funding_interval_hours,last_funding_rate\n1704067200000,8,0.00010000\n1704096000000,8,-0.00020000",
    "1704067200000,8,0.00010000\n1704096000000,8,-0.00020000",
    "calc_time,last_funding_rate,funding_interval_hours\n1704067200000000,0.00010000,8\n1704096000000000,-0.00020000,8",
])
def test_funding_layouts_and_timestamp_units(text):
    assert cu.parse_funding(text) == [(1704067200, 0.0001), (1704096000, -0.0002)]


def test_implausible_funding_rows_are_dropped_not_guessed():
    text = "1704067200000,8,0.0001\n17040672000,8,0.0002\n1704096000000,8,1.5\n1704124800000,8,nan"
    assert cu.parse_funding(text) == [(1704067200, 0.0001)]


def test_corrupt_archive_fails_closed():
    session = _Session(lambda url, p: _Response(content=b"not a zip"))
    with pytest.raises(DownloadError):
        cu._csv(session, "https://example.invalid/x.zip", "x")


def test_build_carry_universe_end_to_end(tmp_path):
    decision_at = int(datetime(2024, 4, 15, tzinfo=timezone.utc).timestamp())
    march = int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp())
    perps = [f"C{k:03d}USDT" for k in range(cu.MIN_PERPS)] + ["BTCUSDT", "1000PEPEUSDT", "LUNAUSDT"]
    spots = ["BTCUSDT", "PEPEUSDT", "LUNAUSDT"]

    def routes(url, params):
        if url == cu.S3_LIST_URL:
            prefix = params["prefix"]
            return _Response(text=_listing(prefix, perps if prefix == cu.PERP_PREFIX else spots))
        name = url.rsplit("/", 1)[-1]
        if "2024-03" not in name:
            return None
        days = 10 if name.startswith("LUNAUSDT") else 31
        if "-1d-" in name and "/futures/" in url:
            return _Response(content=_zip(_klines_csv(march, days, 86_400)))
        if "-8h-" in name:
            return _Response(content=_zip(_klines_csv(march, days * 3, 28_800, micro="/spot/" in url)))
        if "fundingRate" in name:
            rows = "\n".join(f"{(march + (k + 1) * 28_800) * 1000},8,0.0001" for k in range(days * 3))
            return _Response(content=_zip(rows))
        return None

    manifest = cu.build_carry_universe(tmp_path, lookback_months=2, decision_at=decision_at,
                                       rank_limit=3, min_quote_volume=1e6, session=_Session(routes))
    assert set(manifest["symbols"]) == {"BTCUSDT", "1000PEPEUSDT", "LUNAUSDT"}
    assert manifest["symbols"]["1000PEPEUSDT"]["spot"] == "PEPEUSDT"
    assert manifest["stopped_before_window_end"] == ["LUNAUSDT"]
    assert manifest["symbols"]["BTCUSDT"]["funding_rows"] == 93
    assert (tmp_path / "spot" / "1000PEPEUSDT.parquet").exists()
    assert manifest["can_authorize_trade"] is False


def test_too_small_listing_is_refused(tmp_path):
    session = _Session(lambda url, p: _Response(text=_listing(p["prefix"], ["BTCUSDT"])) if url == cu.S3_LIST_URL else None)
    with pytest.raises(DataQualityError):
        cu.build_carry_universe(tmp_path, lookback_months=1, decision_at=1_712_000_000, session=session)
