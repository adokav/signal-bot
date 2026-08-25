from datetime import date

from macro_japan import MofJgbBackfill


SAMPLE = """Interest Rate,,,,,,,,,,,,,,,(Unit : %)\nDate,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y\n2026/8/14,1.100,1.200,1.300,1.400,1.500,1.600,1.700,1.800,1.900,2.000,2.100,2.200,2.300,2.400,2.500\n2026/8/17,1.110,1.210,1.310,1.410,1.510,1.610,1.710,1.810,1.910,2.010,2.110,2.210,2.310,2.410,2.510\n2026/8/18,1.120,1.220,1.320,1.420,1.520,1.620,1.720,1.820,1.920,2.020,2.120,2.220,2.320,2.420,2.520\n"""


def test_mof_uses_next_source_business_day_as_release_date():
    rows=MofJgbBackfill.parse_csv(SAMPLE,start=date(2026,8,14),end=date(2026,8,17))
    first=[r for r in rows if r.series=="jgb_10y" and r.observation_time.startswith("2026-08-14")][0]
    assert first.available_time=="2026-08-17T00:30:00+00:00"
    assert first.value==2.0
    assert first.provider=="mof_jgb_reconstruction"


def test_mof_omits_final_row_without_known_next_business_day():
    rows=MofJgbBackfill.parse_csv(SAMPLE,start=date(2026,8,18),end=date(2026,8,18))
    assert rows==()


def test_mof_emits_only_required_curve_nodes():
    rows=MofJgbBackfill.parse_csv(SAMPLE,start=date(2026,8,14),end=date(2026,8,14))
    assert {r.series for r in rows}=={"jgb_2y","jgb_10y","jgb_30y"}


def test_mof_rejects_missing_required_column():
    broken="Interest Rate\nDate,2Y,10Y\n2026/8/14,1.2,2.0\n2026/8/17,1.3,2.1\n"
    try:
        MofJgbBackfill.parse_csv(broken,start=date(2026,8,14),end=date(2026,8,14))
    except RuntimeError as exc:
        assert "30Y" in str(exc)
    else:
        raise AssertionError("missing curve node must fail closed")
