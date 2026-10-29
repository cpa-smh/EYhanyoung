"""매출채권 순액/총액과 ECL을 구분한다. 주석 자료 없는 ECL을 추정하지 않는다."""
import json
import re
from decimal import Decimal
from pathlib import Path

from account_mapper import amount

NUMERIC_FIELDS = ("gross", "allowance", "net", "ecl_expense", "writeoffs", "recoveries", "other_movements")


def load_credit_notes(path):
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("records"), list):
        raise ValueError("신용손실 주석 파일 구조가 잘못되었습니다.")
    result = {}
    for row in data["records"]:
        if not isinstance(row, dict):
            raise ValueError("신용손실 주석 레코드는 객체여야 합니다.")
        code, year = str(row.get("corp_code", "")), row.get("year")
        if not re.fullmatch(r"\d{8}", code) or type(year) is not int:
            raise ValueError("주석의 고유번호와 연도를 확인하세요.")
        if (code, year) in result:
            raise ValueError("기업·연도별 주석 레코드는 하나만 허용합니다.")
        if (row.get("fs_div") != "CFS" or row.get("currency") != "KRW"
                or row.get("scope") not in ("total_trade", "current_trade")
                or row.get("reported_basis") not in ("net", "gross", "not_available")
                or row.get("balance_date") != f"{year}-12-31"
                or not re.fullmatch(r"\d{14}", str(row.get("rcept_no", "")))
                or not str(row.get("source_url", "")).startswith("https://")
                or not str(row.get("source_ref", "")).strip()):
            raise ValueError("주석에 CFS/KRW, 범위, 순액·총액 구분, 날짜, 접수번호, HTTPS 출처와 주석 위치가 필요합니다.")
        item = dict(row)
        for field in NUMERIC_FIELDS:
            value = row.get(field)
            parsed = amount(value)
            if value is not None and parsed is None:
                raise ValueError(f"주석 {field} 금액이 숫자가 아닙니다.")
            if parsed is not None and field not in ("ecl_expense", "other_movements") and parsed < 0:
                raise ValueError(f"주석 {field}는 양수 크기로 입력하세요.")
            item[field] = parsed
        result[code, year] = item
    return result


def assess_receivables(mapped, note, receipts, scope="total_trade"):
    """동일 범위·순액이 확인되어야 기업 간 매출채권회전율에 사용한다."""
    result = {"net": None, "gross": None, "allowance": None, "ecl_expense": None,
              "writeoffs": None, "recoveries": None, "other_movements": None,
              "status": "REVIEW", "reason": "매출채권의 순액·유동/전체 범위를 주석으로 확인 필요",
              "source_url": "", "source_ref": "", "reconciliation": None, "scope": scope}
    if not note:
        return result
    result.update({"source_url": note["source_url"], "source_ref": note["source_ref"]})
    if note["scope"] != scope or note["rcept_no"] not in receipts:
        result["reason"] = "주석 범위/접수번호가 선택 기준 또는 원천 공시와 불일치"
        return result
    # 명시적인 단기 계정을 전체 채권이라고 확인해도 자동 수용하지 않는다.
    short_term = "ShortTerm" in mapped.account_id or "CurrentTrade" in mapped.account_id or any(t in mapped.account_nm for t in ("단기", "유동"))
    if short_term and scope == "total_trade" and note["reported_basis"] != "not_available":
        result["reason"] = "API 단기 매출채권과 전체 매출채권 범위 불일치; 전체 주석 금액은 not_available로 별도 입력"
        return result
    gross, allowance, net = (note.get(k) for k in ("gross", "allowance", "net"))
    if gross is not None and allowance is not None:
        if allowance > gross:
            result["reason"] = "손실충당금이 총액 초과"
            return result
        derived_net = gross - allowance
        if net is not None and abs(net - derived_net) > 1:
            result["reason"] = "주석 순액 ≠ 총액 − 손실충당금"
            return result
        net = derived_net if net is None else net
    basis = note["reported_basis"]
    if basis == "net" and mapped.amount is not None:
        if net is not None and abs(mapped.amount - net) > 1:
            result["reason"] = "API 장부금액과 주석 순액 불일치"
            return result
        net = mapped.amount if net is None else net
    elif basis == "gross" and mapped.amount is not None:
        if gross is None or abs(mapped.amount - gross) > 1:
            result["reason"] = "API 금액과 주석 총액 불일치"
            return result
    if net is None:
        result["reason"] = "확인 가능한 순액 없음; 통합 계정으로 대체하지 않음"
        return result
    result.update({k: note.get(k) for k in NUMERIC_FIELDS})
    result.update(net=net, status="OK", reason="주석 출처로 순액 및 범위 확인(사용자 입력)")
    result["reconciliation"] = gross - allowance - net if gross is not None and allowance is not None else None
    return result


def credit_diagnostics(current, previous, current_ratios, previous_ratios):
    findings = []
    curr, prev = current["credit"], previous.get("credit", {})
    coverage = current_ratios.get("allowance_ratio")
    old_coverage = previous_ratios.get("allowance_ratio")
    turnover = current_ratios.get("ar_turnover")
    old_turnover = previous_ratios.get("ar_turnover")
    if all(x is not None for x in (coverage, old_coverage, turnover, old_turnover)):
        if coverage > old_coverage and turnover > old_turnover:
            findings.append("순액 회전율과 충당금 설정률 동반 상승: 순액 감소 효과와 실제 회수 개선을 구분 검토")
    # 충당금 증감은 ECL 비용과 다르다. 모든 변동항목이 있을 때만 대사.
    components = [prev.get("allowance"), curr.get("ecl_expense"), curr.get("writeoffs"), curr.get("other_movements"), curr.get("allowance")]
    delta = None
    if all(v is not None for v in components):
        opening, expense, writeoff, other, closing = components
        delta = opening + expense - writeoff + other - closing
        if abs(delta) > Decimal(1):
            findings.append("충당금 롤포워드 불일치: ECL·제각·환율/기타 변동 주석 검토")
    if curr.get("writeoffs") is not None and curr["writeoffs"] > 0:
        findings.append("제각 존재: 총액 회전율 개선도 현금 회수 증가와 구분 필요")
    if curr.get("ecl_expense") is None:
        findings.append("매출채권 한정 ECL 비용/환입 미확인; 전체 금융자산 손상금액으로 대체하지 않음")
    return findings, delta
