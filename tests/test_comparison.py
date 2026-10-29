from decimal import Decimal as D
import pytest
from peer_comparison import percentile, compare
from outlier_detector import iqr_result, detect_time_series


def record(code, value, year=2025):
    return {"code": code, "name": code, "role": "대상기업" if code == "T" else "비교기업", "year": year,
            "ratios": {"ar_turnover": value, "gross_margin": value, "inv_turnover": value}}


def test_linear_percentiles():
    sample = list(map(D, [1, 2, 3, 4, 5, 6]))
    assert percentile(sample, .25) == D("2.25")
    assert percentile(sample, .75) == D("4.75")


def test_target_excluded_from_mean_and_median():
    records = [record("T", D(100))] + [record(str(i), D(i)) for i in range(1, 7)]
    rows, stats = compare(records, {str(i) for i in range(1, 7)}, 2025)
    assert stats["ar_turnover"]["mean"] == D("3.5")
    assert stats["ar_turnover"]["median"] == D("3.5")
    assert next(r for r in rows if r["code"] == "T")["rank"] == 1
    assert iqr_result(D(100), stats["ar_turnover"])["flag"] == "높은 이상치 후보"


def test_missing_sample_count_is_per_metric():
    records = [record(str(i), D(i)) for i in range(6)]
    records[0]["ratios"]["ar_turnover"] = None
    records[1]["ratios"]["ar_turnover"] = None
    _, stats = compare(records, {str(i) for i in range(6)}, 2025)
    assert stats["ar_turnover"]["n"] == 4
    assert stats["gross_margin"]["n"] == 6
    assert iqr_result(D(200), stats["ar_turnover"])["flag"] == "판정 유보"


@pytest.mark.parametrize("sample", [[1, 2, 3, 4], [2, 2, 2, 2, 2]])
def test_insufficient_or_zero_iqr_deferred(sample):
    records = [record(str(i), D(v)) for i, v in enumerate(sample)]
    _, stats = compare(records, {str(i) for i in range(len(sample))}, 2025)
    assert iqr_result(D(100), stats["ar_turnover"])["flag"] == "판정 유보"


def test_strict_boundary_and_lower_outlier():
    stats = {"n": 5, "q1": D(4), "q3": D(8), "iqr": D(4)}
    assert iqr_result(D(-2), stats)["flag"] == "범위 내"
    assert iqr_result(D("-2.01"), stats)["flag"] == "낮은 이상치 후보"
    assert iqr_result(D(14), stats)["flag"] == "범위 내"


def test_percentage_point_difference_and_tied_rank():
    records = [record("T", D(".3")), record("A", D(".2")), record("B", D(".2"))]
    rows, _ = compare(records, {"A", "B"}, 2025)
    target = next(r for r in rows if r["code"] == "T" and r["metric"] == "gross_margin")
    assert target["median_gap"] == D(10)
    assert target["gap_unit"] == "%p"
    assert {r["rank"] for r in rows if r["code"] in ("A", "B")} == {2}


def test_time_series_change_and_three_year_decline():
    records = [record("T", D(v), y) for y, v in [(2023, "10"), (2024, "8"), (2025, "4")]]
    result = detect_time_series(records, D(".30"), D(5))
    last = next(r for r in result if r["metric"] == "ar_turnover" and r["year"] == 2025 and r["unit"] == "비율")
    assert last["delta"] == D("-.5")
    assert last["flag"] == "급락 검토"
    assert sum(r["flag"] == "3개년 연속 하락 검토" for r in result) == 3


def test_zero_base_and_missing_year_do_not_create_spurious_change():
    result = detect_time_series([record("T", D(0), 2023), record("T", D(5), 2024)])
    assert next(r for r in result if r["metric"] == "ar_turnover")["delta"] is None
    result = detect_time_series([record("T", D(2), 2023), record("T", D(5), 2025)])
    assert all(r["delta"] is None for r in result)


def test_margin_changes_in_pp():
    result = detect_time_series([record("T", D(".2"), 2024), record("T", D(".26"), 2025)])
    row = next(r for r in result if r["metric"] == "gross_margin")
    assert row["delta"] == D(6)
    assert row["flag"] == "급등 검토"


@pytest.mark.parametrize("threshold", [D(-1), D("NaN"), D("Infinity")])
def test_invalid_threshold(threshold):
    with pytest.raises(ValueError):
        detect_time_series([], threshold)
