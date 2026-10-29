"""현재 기업개황과 연도별 실제 기간 정보를 구분해 비교 적격성을 검사한다."""
from datetime import date
import re


def profile_reasons(profile):
    reasons = []
    if profile.get("corp_cls") not in ("Y", "K", "N"):
        reasons.append("현재 상장기업 구분 미확인")
    if profile.get("acc_mt") != "12":
        reasons.append("기업개황상 12월 결산 아님/미확인")
    industry = str(profile.get("induty_code", ""))
    if not re.fullmatch(r"\d{2,6}", industry):
        reasons.append("업종코드 미확인")
    elif industry.startswith(("64", "65", "66")):
        reasons.append("금융·보험 관련 업종(64~66) 제외")
    return reasons


def dates_in(text):
    matches = re.findall(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", str(text))
    try:
        return tuple(date(*map(int, match)).isoformat() for match in matches)
    except ValueError:
        return ()


def validate_period(full_rows, period_rows, year):
    """같은 공시의 CFS BS 결산일과 IS 1년 기간을 확인; 연도만으로 날짜 추정 금지."""
    reasons = []
    receipts = {r.get("rcept_no") for r in full_rows if r.get("sj_div") in ("BS", "IS", "CIS")}
    if len(receipts) != 1 or not all(re.fullmatch(r"\d{14}", str(r or "")) for r in receipts):
        reasons.append("전체 재무제표 접수번호 미확정/복수")
    matching = [r for r in period_rows if r.get("fs_div") == "CFS" and r.get("rcept_no") in receipts]
    balance_dates = {dates_in(r.get("thstrm_dt", "")) for r in matching if r.get("sj_div") == "BS"}
    income_dates = {dates_in(r.get("thstrm_dt", "")) for r in matching if r.get("sj_div") in ("IS", "CIS")}
    if balance_dates != {(f"{year}-12-31",)}:
        reasons.append("같은 CFS 공시의 12월 31일 결산일 확인 불가")
    if income_dates != {(f"{year}-01-01", f"{year}-12-31")}:
        reasons.append("같은 CFS 공시의 1월 1일~12월 31일 손익기간 확인 불가")
    currencies = {r.get("currency") for r in full_rows if r.get("sj_div") in ("BS", "IS", "CIS")}
    if currencies != {"KRW"}:
        reasons.append("전체 BS/IS/CIS 통화가 KRW로 일치하지 않음")
    return {"valid": not reasons, "reasons": reasons,
            "balance_date": next(iter(balance_dates))[0] if len(balance_dates) == 1 and len(next(iter(balance_dates))) == 1 else "",
            "income_dates": sorted(income_dates), "receipts": sorted(str(r or "") for r in receipts)}
