"""계정 후보를 보존하며 한 계정만 선택한다. 통합채권을 매출채권으로 쓰지 않는다."""
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
import re


@dataclass
class MappedAccount:
    item: str
    amount: Decimal | None = None
    account_id: str = ""
    account_nm: str = ""
    statement: str = ""
    receipt: str = ""
    currency: str = ""
    method: str = ""
    status: str = "MISSING"
    reason: str = "대상 계정 없음"
    candidates: list | None = None

    def record(self):
        return asdict(self)


ACCOUNTS = {
    "revenue": (("ifrs-full_Revenue", "ifrs_Revenue"), ("매출액", "수익(매출액)", "매출"), ("IS", "CIS")),
    "cost": (("ifrs-full_CostOfSales", "ifrs_CostOfSales"), ("매출원가",), ("IS", "CIS")),
    "receivables": (("ifrs-full_TradeReceivables", "ifrs_TradeReceivables", "dart_ShortTermTradeReceivable", "ifrs-full_CurrentTradeReceivables"),
                    ("매출채권", "매출채권(순액)", "매출채권,순액", "유동매출채권", "단기매출채권"), ("BS",)),
    "inventory": (("ifrs-full_Inventories", "ifrs_Inventories"), ("재고자산",), ("BS",)),
    "gross_profit": (("ifrs-full_GrossProfit", "ifrs_GrossProfit"), ("매출총이익", "매출총이익(손실)"), ("IS", "CIS")),
    "assets": (("ifrs-full_Assets", "ifrs_Assets"), ("자산총계",), ("BS",)),
    "liabilities": (("ifrs-full_Liabilities", "ifrs_Liabilities"), ("부채총계",), ("BS",)),
    "equity": (("ifrs-full_Equity", "ifrs_Equity"), ("자본총계",), ("BS",)),
}


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("−", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        result = Decimal(text)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def normalize(name):
    return re.sub(r"\s+", "", str(name))


def map_accounts(rows):
    mapped = {}
    for item, (ids, aliases, statements) in ACCOUNTS.items():
        eligible = [r for r in rows if r.get("sj_div") in statements]
        if item == "receivables":
            # ID가 비슷하더라도 기타채권/계약자산/총액/충당금은 순액 후보에서 제외.
            eligible = [r for r in eligible if not any(t in normalize(r.get("account_nm", ""))
                        for t in ("기타", "계약자산", "충당금", "손실", "총액", "gross"))
                        and "Other" not in str(r.get("account_id", ""))]
        candidates = [r for r in eligible if r.get("account_id") in ids]
        method = "표준 ID"
        if not candidates:
            candidates = [r for r in eligible if normalize(r.get("account_nm", "")) in aliases]
            method = "계정명 정확 일치"
        evidence = [{k: r.get(k) for k in ("account_id", "account_nm", "sj_div", "thstrm_amount", "currency", "rcept_no")} for r in candidates]
        result = MappedAccount(item=item, candidates=evidence)
        # 손익계산서 IS를 우선한다. 차이는 원본/후보에 남는다.
        for statement in statements:
            subset = [r for r in candidates if r.get("sj_div") == statement]
            if subset:
                candidates = subset
                break
        if candidates:
            signatures = {(amount(r.get("thstrm_amount")), r.get("currency"), r.get("rcept_no")) for r in candidates}
            if len(signatures) != 1:
                result.status, result.reason = "REVIEW", "중복 후보 금액/통화/접수번호 충돌; 합산하지 않음"
            else:
                row = candidates[0]
                result.account_id, result.account_nm = row.get("account_id", ""), row.get("account_nm", "")
                result.statement, result.receipt = row.get("sj_div", ""), row.get("rcept_no", "")
                result.currency, result.method = row.get("currency", ""), method
                value = amount(row.get("thstrm_amount"))
                if result.currency != "KRW" or value is None:
                    result.status, result.reason = "REVIEW", "KRW 통화/금액 미확인"
                elif item in ("revenue", "cost", "receivables", "inventory") and value < 0:
                    result.status, result.reason = "REVIEW", "음수 금액; 부호를 임의 반전하지 않음"
                else:
                    result.amount, result.status, result.reason = value, "OK", ""
        mapped[item] = result
    return mapped


def accounting_checks(mapped):
    values = {k: v.amount for k, v in mapped.items()}
    results = []
    for label, terms in (("자산-부채-자본", ("assets", "liabilities", "equity")),
                         ("매출-원가-매출총이익", ("revenue", "cost", "gross_profit"))):
        a, b, c = (values.get(k) for k in terms)
        difference = a - b - c if all(v is not None for v in (a, b, c)) else None
        results.append({"check": label, "difference": difference,
                        "status": "SKIP" if difference is None else "PASS" if abs(difference) <= 1 else "FAIL"})
    return results
