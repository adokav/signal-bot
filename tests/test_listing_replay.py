from __future__ import annotations

import json
import math
import random

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from trading.backtest import listing_replay as lp
from trading.data import binance_history_identity as hid
from trading.data import binance_listings as bl
from trading.data.binance_perp import Candle, DataQualityError
from trading.research.robustness import TrialRegistry, trial_id_for

H = lp.HOUR
D = lp.DAY
START = 1_609_459_200  # 2021-01-01T00:00:00Z


# ---------------------------------------------------------------------------
# Identity layer
# ---------------------------------------------------------------------------


def test_history_identity_catches_what_live_rules_miss():
    bases = {"BTC", "ETH", "BULL", "BEAR", "ETHBULL", "SOL"}
    assert hid.exclusion_reason("BULL", bases) == "LEVERAGED"
    assert hid.exclusion_reason("ETHBULL", bases) == "LEVERAGED"     # live rule
    assert hid.exclusion_reason("PAX", bases) == "STABLE"
    assert hid.exclusion_reason("USDC", bases) == "STABLE"           # live rule
    assert hid.exclusion_reason("U", bases) == "STABLE"
    assert hid.exclusion_reason("XAUT", bases) == "COMMODITY"
    assert hid.exclusion_reason("BNSOL", bases) == "PEGGED"
    assert hid.exclusion_reason("SOL", bases) is None
    assert hid.migration_source("POL") == "MATIC" and hid.migration_source("SOL") is None


def test_migration_check_uses_the_data():
    months = {"POLUSDT": ["2024-09", "2024-10"], "MATICUSDT": ["2019-04", "2024-09"],
              "SUSDT": ["2025-01"], "FTMUSDT": ["2019-06", "2025-01"],
              "AUSDT": ["2025-05"], "EOSUSDT": ["2018-05", "2025-01"]}
    check = hid.verify_migrations(months)
    assert check["POL"] == "OK" and check["S"] == "OK"
    assert check["A"] == "GAP_4_MONTHS" and check["RENDER"] == "MISSING_PAIR"


# ---------------------------------------------------------------------------
# Listing and selection
# ---------------------------------------------------------------------------


def test_month_keys_and_pagination_marker():
    page = ("<ListBucketResult><IsTruncated>true</IsTruncated>"
            "<Key>data/spot/monthly/klines/XUSDT/1d/XUSDT-1d-2021-03.zip</Key>"
            "<Key>data/spot/monthly/klines/XUSDT/1d/XUSDT-1d-2021-03.zip.CHECKSUM</Key>"
            "<Key>data/spot/monthly/klines/XUSDT/1d/XUSDT-1d-2021-04.zip</Key></ListBucketResult>")
    months, marker = bl.parse_month_keys(page)
    assert months == ["2021-03", "2021-04"] and marker.endswith("2021-04.zip")
    assert bl.parse_month_keys(page.replace("true", "false"))[1] is None
    odd = "<Key>data/spot/monthly/klines/ADABKRW/1d/ADABKRW-1d-2020-8.zip</Key>"
    assert bl.parse_month_keys(odd)[0] == ["2020-08"]
    with pytest.raises(DataQualityError):
        bl.parse_month_keys("<Key>data/spot/monthly/klines/X/1d/X-1d-2020-13.zip</Key>")
    with pytest.raises(DataQualityError):
        bl.parse_month_keys("<Key>data/spot/monthly/klines/X/1d/X-1d-latest.zip</Key>")


def test_selection_keeps_only_genuinely_new_assets():
    symbols = ["NEWUSDT", "OLDUSDT", "OLDBTC", "POLUSDT", "MATICUSDT", "USD1USDT", "EARLYUSDT", "LATEUSDT", "BTCUSDT"]
    months = {"NEWUSDT": ["2021-05", "2021-06"], "OLDUSDT": ["2021-03"], "OLDBTC": ["2018-01", "2021-06"],
              "POLUSDT": ["2024-09"], "MATICUSDT": ["2019-04", "2024-09"], "USD1USDT": ["2025-05"],
              "EARLYUSDT": ["2019-01"], "LATEUSDT": ["2026-08"], "BTCUSDT": ["2017-08", "2026-08"]}
    sel = bl.select_listings(symbols, months, window_end="2026-05")
    assert sel.listings == {"NEWUSDT": "2021-05"}
    assert sel.excluded["OLDUSDT"].startswith("LISTED_EARLIER_IN_OLDBTC")
    assert sel.excluded["POLUSDT"] == "MIGRATION_FROM_MATIC"
    assert sel.excluded["USD1USDT"] == "STABLE"
    assert "EARLYUSDT" not in sel.excluded and "LATEUSDT" not in sel.listings


def _candle(t, price, qv=1e5):
    return Candle(open_time=t, close_time=t + H - 1, available_at=t + H, open=price, high=price * 1.01,
                  low=price * 0.99, close=price, volume=qv / price, quote_volume=qv)


def test_build_downloads_listings_and_benchmark(monkeypatch, tmp_path):
    usdt = [f"C{k}USDT" for k in range(300)] + ["NEWUSDT", "POLUSDT", "MATICUSDT", "BTCUSDT"]
    months = {s: ["2019-01", "2026-08"] for s in usdt}
    months.update({"NEWUSDT": ["2026-05", "2026-06"], "POLUSDT": ["2026-05"], "MATICUSDT": ["2019-04", "2026-04"],
                   "BTCUSDT": ["2017-08", "2026-08"]})
    calls = []

    def fetch(session, symbol, timeframe, year, month, *, decision_at):
        calls.append((symbol, year, month))
        base = 1_000_000 if symbol == "BTCUSDT" else 1.0
        t0 = int(pd.Timestamp(year=year, month=month, day=1, tz="UTC").timestamp())
        return [_candle(t0 + i * H, base) for i in range(3)]

    monkeypatch.setattr(bl.bu, "list_spot_symbols", lambda session: sorted(usdt))
    monkeypatch.setattr(bl, "list_months", lambda session, pair: months.get(pair, []))
    monkeypatch.setattr(bl.bu, "fetch_month", fetch)
    decision_at = int(pd.Timestamp("2026-09-15", tz="UTC").timestamp())
    manifest = bl.build_listings(tmp_path, decision_at=decision_at, session=object(), workers=2)
    assert set(manifest["listings"]) == {"NEWUSDT"}
    assert manifest["excluded"]["POLUSDT"] == "MIGRATION_FROM_MATIC"
    assert manifest["window"] == ["2020-10", "2026-08"]
    assert manifest["data_end"] == int(pd.Timestamp("2026-09-01", tz="UTC").timestamp()) - 1
    assert manifest["listings"]["NEWUSDT"]["months"] == ["2026-05", "2026-06", "2026-07", "2026-08"]
    assert (tmp_path / "1h" / "NEWUSDT.parquet").exists() and (tmp_path / "1h" / "BTCUSDT.parquet").exists()
    assert manifest["can_authorize_trade"] is False


def test_build_fails_closed_on_download_errors(monkeypatch, tmp_path):
    usdt = [f"C{k}USDT" for k in range(300)] + ["NEWUSDT", "BTCUSDT"]
    months = {s: ["2019-01", "2026-08"] for s in usdt}
    months["NEWUSDT"] = ["2026-05"]

    def fetch(*args, **kwargs):
        raise bl.bu.DownloadError("HTTP 503 after 4 attempts")

    monkeypatch.setattr(bl.bu, "list_spot_symbols", lambda session: sorted(usdt))
    monkeypatch.setattr(bl, "list_months", lambda session, pair: months.get(pair, []))
    monkeypatch.setattr(bl.bu, "fetch_month", fetch)
    with pytest.raises(bl.bu.DownloadError):
        bl.build_listings(tmp_path, decision_at=int(pd.Timestamp("2026-09-15", tz="UTC").timestamp()),
                          session=object(), workers=2)
    assert not (tmp_path / "manifest.json").exists()


def test_build_rejects_a_broken_symbol_listing(monkeypatch, tmp_path):
    monkeypatch.setattr(bl.bu, "list_spot_symbols", lambda session: ["BTCUSDT", "ETHUSDT"])
    with pytest.raises(DataQualityError):
        bl.build_listings(tmp_path, decision_at=START, session=object())


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def _cols(start, hours, price, volume=1e5, lead_zero=0):
    t = start + np.arange(hours) * H
    p = np.array([price(i) for i in range(hours)], dtype=float)
    qv = np.full(hours, volume, dtype=float)
    qv[:lead_zero] = 0.0
    return {"open_time": t, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "quote_volume": qv}


def _btc(hours=500 * 24, start=START - 30 * D):
    return lp.clean_series(_cols(start, hours, lambda i: 30_000.0 * (1 + 0.0001 * i)))


def test_entries_exits_and_btc_excess_are_exact():
    s = lp.clean_series(_cols(START, 120 * 24, lambda i: 10.0 * (1 + 0.001 * i), lead_zero=2))
    btc = _btc()
    obs = lp.observations({"XUSDT": s}, btc, data_end=START + 400 * D)
    row = next(r for r in obs.rows if r.entry == "24h" and r.horizon == "30d")
    first = START + 2 * H                              # first candle with volume
    assert row.first_trade == first and row.entry_time == first + D
    entry_i, exit_i = 2 + 24, 2 + 24 + 30 * 24
    assert row.gross_pct == pytest.approx(((1 + 0.001 * exit_i) / (1 + 0.001 * entry_i) - 1) * 100)
    b0 = (first + D - (START - 30 * D)) // H
    assert row.btc_pct == pytest.approx(((1 + 0.0001 * (b0 + 30 * 24)) / (1 + 0.0001 * b0) - 1) * 100)
    assert row.pump_pct == pytest.approx((1 + 0.001 * entry_i) / (1 + 0.001 * 2) * 100 - 100)
    assert row.volume_before_entry == pytest.approx(24 * 1e5)
    assert not row.delisted and len(obs.rows) == 6


def test_delisting_exits_at_last_close_and_late_windows_are_left_out():
    dying = lp.clean_series(_cols(START, 20 * 24, lambda i: 10.0 * 0.995 ** i))
    btc = _btc()
    obs = lp.observations({"DUSDT": dying}, btc, data_end=START + 400 * D)
    rows = [r for r in obs.rows if r.entry == "1h"]
    assert rows and all(r.delisted for r in rows)
    assert rows[0].gross_pct == pytest.approx((0.995 ** (20 * 24 - 1) / 0.995 ** 1 - 1) * 100)
    short = lp.observations({"DUSDT": dying}, btc, data_end=START + 60 * D)
    assert "1h@90d" in short.incomplete_window and all(r.horizon == "30d" for r in short.rows)
    gone = lp.observations({"GUSDT": lp.clean_series(_cols(START, 5 * 24, lambda i: 1.0))}, btc,
                           data_end=START + 400 * D)
    assert gone.ended_before_entry == {"7d": 1}


def test_maintenance_holes_use_the_next_candle_within_six_hours():
    cols = _cols(START, 60 * 24, lambda i: 10.0 + i * 0.01)
    entry_i = 24                                       # first trade at bar 0, entry +24h
    keep = np.ones(60 * 24, dtype=bool)
    keep[entry_i:entry_i + 3] = False                  # 3-hour maintenance hole at the entry
    short = {k: v[keep] for k, v in cols.items()}
    row = next(r for r in lp.observations({"X": lp.clean_series(short)}, _btc(), data_end=START + 400 * D).rows
               if r.entry == "24h" and r.horizon == "30d")
    assert row.gross_pct == pytest.approx(((10.0 + (24 + 720) * 0.01) / (10.0 + 27 * 0.01) - 1) * 100)
    keep[entry_i:entry_i + 8] = False                  # 8 hours: longer than the tolerance
    long_hole = {k: v[keep] for k, v in cols.items()}
    obs = lp.observations({"X": lp.clean_series(long_hole)}, _btc(), data_end=START + 400 * D)
    assert obs.unresolvable.get("24h@30d") == 1


def test_future_candles_cannot_change_entry_features_or_earlier_outcomes():
    base = _cols(START, 120 * 24, lambda i: 10.0 * (1 + 0.002 * math.sin(i / 7)))
    poisoned = {k: v.copy() for k, v in base.items()}
    cut = 2 + 24 + 30 * 24          # after the 24h@30d exit
    for k in ("open", "high", "low", "close"):
        poisoned[k][cut + 1:] *= 50
    poisoned["quote_volume"][cut + 1:] *= 1000
    btc = _btc()
    a = {(r.entry, r.horizon): r for r in lp.observations({"X": lp.clean_series(base)}, btc, data_end=START + 400 * D).rows}
    b = {(r.entry, r.horizon): r for r in lp.observations({"X": lp.clean_series(poisoned)}, btc, data_end=START + 400 * D).rows}
    for key in (("1h", "30d"), ("24h", "30d")):
        assert a[key] == b[key]
    assert a[("7d", "30d")].pump_pct == b[("7d", "30d")].pump_pct
    assert a[("7d", "90d")].gross_pct != b[("7d", "90d")].gross_pct


def test_malformed_rows_are_dropped_and_counted():
    cols = _cols(START, 6, lambda i: 10.0)
    cols["high"][1] = 5.0            # high below open
    cols["open_time"][2] += 7        # off the hour grid
    cols["close"][3] = float("nan")
    cols["open_time"] = np.append(cols["open_time"], cols["open_time"][0])
    for k in ("open", "high", "low", "close", "quote_volume"):
        cols[k] = np.append(cols[k], cols[k][0])
    s = lp.clean_series(cols)
    assert s.rejected == 4 and list(s.open_time) == [START, START + 4 * H, START + 5 * H]


# ---------------------------------------------------------------------------
# Statistics and verdicts
# ---------------------------------------------------------------------------


def _obs(gross, btc):
    rows = []
    for k, (g, b) in enumerate(zip(gross, btc)):
        first = START + k * 4 * D        # chronological, about 7 listings per month
        rows.append(lp.Observation("S", "24h", "30d", first, first + D, g, b, 0.0, 0.0, False))
    return rows


def test_verdicts():
    rng = random.Random(3)
    costs = lp.ListingCosts()
    losing = [-30 + rng.gauss(0, 20) for _ in range(200)]
    assert lp.group_stats("x", _obs(losing, [0.0] * 200), costs).verdict == "AVOID_CONFIRMED"
    # loses money but beats a falling BTC: no avoidance claim against the market
    assert lp.group_stats("x", _obs(losing, [-60.0] * 200), costs).verdict == "NO_CLAIM"
    assert lp.group_stats("x", _obs(losing[:80], [0.0] * 80), costs).verdict == "INSUFFICIENT"
    mixed = [-40 + rng.gauss(0, 5) for _ in range(100)] + [25 + rng.gauss(0, 5) for _ in range(100)]
    assert lp.group_stats("x", _obs(mixed, [0.0] * 200), costs).verdict == "NO_CLAIM"
    winning = [30 + rng.gauss(0, 10) for _ in range(200)]
    assert lp.group_stats("x", _obs(winning, [0.0] * 200), costs).verdict == "POSITIVE_SURPRISE"
    stats = lp.group_stats("x", _obs(losing, [0.0] * 200), costs, decision_group=False)
    assert stats.verdict == "DIAGNOSTIC_ONLY" and stats.mean_net_pct == pytest.approx(stats.mean_gross_pct - 0.65)


def test_month_cluster_ci_resamples_months():
    values = [1.0] * 10 + [-1.0] * 10
    one_month = lp.month_cluster_ci(values, [7] * 20, alpha=0.05)
    assert one_month is None                      # a single cluster says nothing
    ci = lp.month_cluster_ci(values, [1] * 10 + [2] * 10, alpha=0.05)
    assert ci[0] == pytest.approx(-1.0) and ci[1] == pytest.approx(1.0)


def test_diagnostic_bands_follow_the_live_radar_thresholds():
    assert lp.pump_band(81) == "pump>80%" and lp.pump_band(80) == "pump-35..80%" and lp.pump_band(-36) == "pump<-35%"
    assert lp.volume_band(1e6) == "vol<5M" and lp.volume_band(6e6) == "vol 5-50M" and lp.volume_band(6e7) == "vol>50M"


# ---------------------------------------------------------------------------
# CLI and pre-registration
# ---------------------------------------------------------------------------


def _write_dataset(directory, *, n=120, btc_end_extra=0):
    (directory / "1h").mkdir(parents=True)
    rng = random.Random(5)
    listings = {}
    for k in range(n):
        start = START + k * 2 * D
        drift = rng.uniform(-0.002, 0.0005)
        cols = _cols(start, 100 * 24, lambda i, d=drift: 5.0 * math.exp(d * i))
        frame = pd.DataFrame({c: cols[c] for c in ("open_time", "open", "high", "low", "close", "quote_volume")})
        frame.to_parquet(directory / "1h" / f"N{k:03d}USDT.parquet", index=False)
        listings[f"N{k:03d}USDT"] = {"first_month": "2021-01"}
    btc_hours = 700 * 24
    cols = _cols(START - 10 * D, btc_hours, lambda i: 30_000.0)
    pd.DataFrame(cols).to_parquet(directory / "1h" / "BTCUSDT.parquet", index=False)
    data_end = START - 10 * D + btc_hours * H - 1 - btc_end_extra
    (directory / "manifest.json").write_text(json.dumps({
        "data_end": data_end, "listings": listings, "excluded": {"POLUSDT": "MIGRATION_FROM_MATIC"},
        "migration_check": {"POL": "OK"},
    }))


def test_cli_runs_end_to_end_and_refuses_data_past_the_end(tmp_path, capsys):
    data = tmp_path / "ok"
    _write_dataset(data)
    out = tmp_path / "listing.json"
    registry = tmp_path / "registry.jsonl"
    argv = ["run", "--data-dir", str(data), "--out", str(out), "--trial-registry", str(registry)]
    assert lp._cli([*argv, "--record-trial"]) == 0
    assert "NOT pre-registered" in capsys.readouterr().out
    payload = json.loads(out.read_text())
    assert set(payload["decision"]) == {f"{e}@{h}" for e in lp.ENTRY_OFFSETS for h in lp.HORIZONS}
    assert payload["decision"]["24h@30d"]["n"] == 120 and payload["can_authorize_trade"] is False
    assert "WHAT COULD BLOW UP" in out.with_suffix(".md").read_text().upper()
    assert len(TrialRegistry(registry).selection_trials(lp.REPLAY_FAMILY)) == 1
    assert lp._cli(argv) == 0 and "(pre-registered)" in capsys.readouterr().out

    late = tmp_path / "late"
    _write_dataset(late, n=3, btc_end_extra=5 * H)
    with pytest.raises(SystemExit):
        lp._cli(["run", "--data-dir", str(late), "--out", str(tmp_path / "x.json"), "--trial-registry", str(registry)])


def test_listing_replay_is_pre_registered_for_the_current_code():
    """Tripwire: changing the identity layer, the data path or the study is a new trial."""

    params = lp.replay_trial_params(fingerprint=lp.engine_fingerprint(), costs=lp.ListingCosts())
    trial_id = trial_id_for(family=lp.REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(lp.REPO_ROOT / "research" / "trials" / "registry.jsonl")
    assert trial_id in {r.trial_id for r in registry.selection_trials(lp.REPLAY_FAMILY)}
