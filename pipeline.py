"""수집과 분석을 분리한다. 저장한 원본 스냅샷으로 API 없이 결과를 재현한다."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from account_mapper import map_accounts, accounting_checks
from credit_risk import assess_receivables, credit_diagnostics
from dart_client import NoDataError
from period_validator import profile_reasons, validate_period
from ratio_calculator import calculate
from peer_comparison import compare
from outlier_detector import detect_cross_section, detect_time_series


def collect(client, companies, start_year, end_year, progress=print):
    snapshot = {"schema_version": 1, "synthetic": False, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "start_year": start_year, "end_year": end_year, "companies": []}
    for index, company in enumerate(companies):
        progress(f"[{index + 1}/{len(companies)}] {company.name} 조회")
        try:
            profile = client.profile(company.code)
        except NoDataError:
            profile = {}
        entry = {"code": company.code, "name": company.name, "stock_code": company.stock_code,
                 "role": "대상기업" if index == 0 else "비교기업", "profile": profile, "years": {}}
        snapshot["companies"].append(entry)
        if profile_reasons(profile):
            continue
        for year in range(start_year - 1, end_year + 1):
            year_data = {"full": [], "period": [], "notes": []}
            entry["years"][str(year)] = year_data
            try:
                year_data["full"] = client.financials(company.code, year, "CFS")
            except NoDataError:
                year_data["notes"].append("CFS 데이터 없음; OFS로 대체하지 않음")
                continue
            try:
                year_data["period"] = client.period_accounts(company.code, year, company.stock_code)
            except NoDataError:
                year_data["notes"].append("결산기간 확인용 자료 없음; 해당 연도 비교 제외")
    return snapshot


def save_snapshot(snapshot, path):
    Path(path).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or type(data.get("synthetic")) is not bool:
        raise ValueError("지원하지 않는 스냅샷 형식입니다.")
    start, end = data.get("start_year"), data.get("end_year")
    if type(start) is not int or type(end) is not int or end - start != 2 or start < 2016:
        raise ValueError("스냅샷 분석 기간은 2016년 이후 연속 3개년이어야 합니다.")
    companies = data.get("companies")
    if not isinstance(companies, list) or not 2 <= len(companies) <= 11:
        raise ValueError("스냅샷에 대상기업과 비교기업 1~10개가 필요합니다.")
    seen = set()
    for i, company in enumerate(companies):
        if not isinstance(company, dict) or not all(k in company for k in ("code", "name", "stock_code", "role", "profile", "years")):
            raise ValueError("스냅샷 기업 필드 누락")
        if company["code"] in seen or company["role"] != ("대상기업" if i == 0 else "비교기업"):
            raise ValueError("스냅샷 기업 중복/역할 오류")
        seen.add(company["code"])
        if not isinstance(company["profile"], dict) or not isinstance(company["years"], dict):
            raise ValueError("스냅샷 기업 정보 구조 오류")
        for year, payload in company["years"].items():
            if not isinstance(payload, dict) or not all(isinstance(payload.get(k), list) for k in ("full", "period", "notes")):
                raise ValueError("스냅샷 연도별 응답 구조 오류")
            for endpoint in ("full", "period"):
                for row in payload[endpoint]:
                    if not isinstance(row, dict) or row.get("bsns_year") != year or row.get("reprt_code") != "11011":
                        raise ValueError("스냅샷 연도/보고서 오류")
                    if endpoint == "full" and row.get("corp_code") != company["code"]:
                        raise ValueError("스냅샷 고유번호 오류")
                    if endpoint == "period" and row.get("stock_code") != company["stock_code"]:
                        raise ValueError("스냅샷 종목코드 오류")
    return data


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyze(snapshot, notes, scope, turnover_threshold, margin_pp):
    records, observations = [], []
    start, end = snapshot["start_year"], snapshot["end_year"]
    for company in snapshot["companies"]:
        company_observations = {}
        for year in range(start - 1, end + 1):
            raw = company["years"].get(str(year), {"full": [], "period": [], "notes": []})
            period = validate_period(raw["full"], raw["period"], year)
            reasons = profile_reasons(company["profile"]) + period["reasons"] + raw["notes"]
            mapped = map_accounts(raw["full"])
            credit = assess_receivables(mapped["receivables"], notes.get((company["code"], year)), period["receipts"], scope)
            observation = {"code": company["code"], "name": company["name"], "role": company["role"], "year": year,
                           "eligible": not reasons, "reasons": reasons, "period": period,
                           "mapping": mapped, "values": {k: v.amount for k, v in mapped.items()}, "credit": credit,
                           "checks": accounting_checks(mapped), "raw": raw}
            company_observations[year] = observation
            observations.append(observation)
        company_records = {}
        for year in range(start, end + 1):
            observation = company_observations[year]
            ratios, reasons = calculate(observation, company_observations[year - 1])
            previous_ratios = company_records.get(year - 1, {}).get("ratios", {})
            findings, rollforward = credit_diagnostics(observation, company_observations[year - 1], ratios, previous_ratios)
            record = {**observation, "ratios": ratios, "ratio_reasons": reasons,
                      "credit_findings": findings, "rollforward_difference": rollforward}
            company_records[year] = record
            records.append(record)
    peer_codes = {c["code"] for c in snapshot["companies"] if c["role"] == "비교기업"}
    comparisons, statistics = compare(records, peer_codes, end)
    return {"records": records, "observations": observations, "comparisons": comparisons,
            "statistics": statistics, "cross_outliers": detect_cross_section(comparisons),
            "time_outliers": detect_time_series(records, turnover_threshold, margin_pp),
            "scope": scope, "turnover_threshold": turnover_threshold, "margin_pp": margin_pp}
