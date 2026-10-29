"""통계적 후보와 시계열 검토 신호만 표시한다. 회계부정을 판단하지 않는다."""
from decimal import Decimal
from ratio_calculator import CORE


def iqr_result(value, stats):
    result = {"lower": None, "upper": None, "flag": "판정 유보", "review": ""}
    if value is None:
        result["review"] = "해당 기업 비율 계산 불가"
    elif stats["n"] < 5:
        result["review"] = "해당 비율의 유효 비교기업 5개 미만"
    elif stats["iqr"] == 0:
        result["review"] = "IQR=0; 판정 유보"
    else:
        lower = stats["q1"] - Decimal("1.5") * stats["iqr"]
        upper = stats["q3"] + Decimal("1.5") * stats["iqr"]
        flag = "낮은 이상치 후보" if value < lower else "높은 이상치 후보" if value > upper else "범위 내"
        result.update(lower=lower, upper=upper, flag=flag,
                      review="사업구조·회계정책·원천 주석 추가 검토; 부정/오류 단정 불가" if "후보" in flag else "통계적 범위 내이며 위험 부재를 보장하지 않음")
    return result


def detect_cross_section(comparison):
    return [{**row, **iqr_result(row["value"], row)} for row in comparison]


def detect_time_series(records, turnover_threshold=Decimal("0.30"), margin_pp=Decimal("5")):
    """회전율은 전년 대비 상대변화, 이익률은 %p 절대변화로 비교한다."""
    if not turnover_threshold.is_finite() or not margin_pp.is_finite() or turnover_threshold < 0 or margin_pp < 0:
        raise ValueError("이상변동 기준은 유한한 0 이상의 값이어야 합니다.")
    results = []
    companies = sorted({r["code"] for r in records})
    for code in companies:
        company_rows = sorted((r for r in records if r["code"] == code), key=lambda r: r["year"])
        for metric in CORE:
            for previous, current in zip(company_rows, company_rows[1:]):
                a, b = previous["ratios"][metric], current["ratios"][metric]
                delta = None
                if a is not None and b is not None and current["year"] == previous["year"] + 1:
                    delta = (b - a) * 100 if metric == "gross_margin" else (b - a) / abs(a) if a != 0 else None
                threshold = margin_pp if metric == "gross_margin" else turnover_threshold
                flag = "판정 유보" if delta is None else "급락 검토" if delta < -threshold else "급등 검토" if delta > threshold else "기준 내"
                results.append({"code": code, "name": current["name"], "year": current["year"], "metric": metric,
                                "previous": a, "value": b, "delta": delta, "threshold": threshold,
                                "unit": "%p" if metric == "gross_margin" else "비율", "flag": flag,
                                "review": "전년값 0/결측 또는 기간 불연속" if delta is None else "변동 기준은 사용자가 정한 검토 기준이며 업종 규범이 아님"})
            if len(company_rows) == 3:
                sequence = [r["ratios"][metric] for r in company_rows]
                consecutive = company_rows[-1]["year"] - company_rows[0]["year"] == 2
                if consecutive and all(v is not None for v in sequence) and sequence[0] > sequence[1] > sequence[2]:
                    results.append({"code": code, "name": company_rows[-1]["name"], "year": company_rows[-1]["year"], "metric": metric,
                                    "previous": sequence[0], "value": sequence[-1], "delta": None, "threshold": None,
                                    "unit": "추세", "flag": "3개년 연속 하락 검토",
                                    "review": "회전율 하락은 회수/재고회전 지연 가능성; 사업구조 변화와 함께 검토"})
    return results
