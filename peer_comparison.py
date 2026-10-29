"""사용자가 고른 비교기업만 기준집단으로 사용한다. 대상기업은 기준에서 제외."""
from decimal import Decimal
from statistics import median
from ratio_calculator import CORE


def percentile(values, probability):
    """선형 보간: h=(n-1)*p. Excel PERCENTILE.INC / numpy linear와 동일."""
    ordered = sorted(values)
    if not ordered:
        return None
    h = Decimal(len(ordered) - 1) * Decimal(str(probability))
    low = int(h)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (h - low)


def compare(records, peer_codes, year):
    rows, stats = [], {}
    current = [r for r in records if r["year"] == year]
    for metric in CORE:
        sample = [r["ratios"][metric] for r in current if r["code"] in peer_codes and r["ratios"][metric] is not None]
        q1, q3 = percentile(sample, .25), percentile(sample, .75)
        stats[metric] = {"n": len(sample), "mean": sum(sample) / len(sample) if sample else None,
                         "median": median(sample) if sample else None, "q1": q1, "q3": q3,
                         "iqr": q3 - q1 if sample else None}
        all_valid = [r["ratios"][metric] for r in current if r["ratios"][metric] is not None]
        for record in current:
            value = record["ratios"][metric]
            center = stats[metric]
            factor = Decimal(100) if metric == "gross_margin" else Decimal(1)
            rows.append({"code": record["code"], "name": record["name"], "role": record["role"],
                         "year": year, "metric": metric, "value": value, **center,
                         "mean_gap": (value - center["mean"]) * factor if value is not None and center["mean"] is not None else None,
                         "median_gap": (value - center["median"]) * factor if value is not None and center["median"] is not None else None,
                         "gap_unit": "%p" if metric == "gross_margin" else "배",
                         "rank": 1 + sum(v > value for v in all_valid) if value is not None else None})
    return rows, stats
