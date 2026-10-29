"""회계 계산만 수행한다. 빈값을 0으로 바꾸지 않고 계산 불가 이유를 남긴다."""
from decimal import Decimal

METRICS = {
    "ar_turnover": ("매출채권회전율(순액)", "배"),
    "gross_margin": ("매출총이익률", "%"),
    "inv_turnover": ("재고자산회전율", "배"),
    "dso": ("매출채권회전기간", "일"),
    "dio": ("재고자산회전기간", "일"),
    "gross_ar_turnover": ("매출채권회전율(총액)", "배"),
    "allowance_ratio": ("손실충당금 설정률", "%"),
    "ecl_to_sales": ("매출채권 ECL 비용/매출", "%"),
}
CORE = ("ar_turnover", "gross_margin", "inv_turnover")


def divide(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def average(a, b):
    if a is None or b is None or a < 0 or b < 0:
        return None
    return (a + b) / 2


def calculate(current, previous):
    result = dict.fromkeys(METRICS)
    reasons = {}
    if not current.get("eligible"):
        return result, {key: "당기 기업/기간/통화 기준 미충족" for key in METRICS}
    values, credit = current["values"], current["credit"]
    prior_valid = previous.get("eligible", False)
    old_values, old_credit = previous.get("values", {}), previous.get("credit", {})
    revenue, cost = values.get("revenue"), values.get("cost")
    result["gross_margin"] = divide(revenue - cost if revenue is not None and cost is not None else None, revenue)
    result["allowance_ratio"] = divide(credit.get("allowance"), credit.get("gross"))
    result["ecl_to_sales"] = divide(credit.get("ecl_expense"), revenue)
    if prior_valid:
        result["ar_turnover"] = divide(revenue, average(credit.get("net"), old_credit.get("net")))
        result["gross_ar_turnover"] = divide(revenue, average(credit.get("gross"), old_credit.get("gross")))
        result["inv_turnover"] = divide(cost, average(values.get("inventory"), old_values.get("inventory")))
    result["dso"] = divide(Decimal(365), result["ar_turnover"])
    result["dio"] = divide(Decimal(365), result["inv_turnover"])
    for key, value in result.items():
        if value is None:
            reasons[key] = "필수 금액/주석 누락, 기초기간 미검증 또는 분모 0/음수"
        else:
            reasons[key] = ""
    return result, reasons
