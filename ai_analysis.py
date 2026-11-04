"""Evidence-backed AI additions; numerical judgments remain in the existing pipeline."""
from decimal import Decimal, InvalidOperation
import json
import re

from ai_client import AIError, TEXT, obj
from credit_risk import NUMERIC_FIELDS, load_credit_notes
from dart_client import DartError
from disclosure_source import document_chunks
from report_analysis import business_profile, explanation_evidence, batches
from period_validator import profile_reasons

BUSINESS = ("사업의 내용", "주요 제품", "주요제품", "매출실적", "매출 구성", "사업부문")
CREDIT = ("매출채권", "손실충당금", "기대신용손실", "신용위험", "연결재무제표 주석")


def array(schema):
    return {"type": "array", "items": schema}


def quoted(quote, text):
    return bool(quote.strip()) and re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", text)


def select_peers(ai, source, index, target, year, progress=print):
    target_doc = source.latest(target, year)
    target_profile = business_profile(ai, target_doc)
    available = {c.code: c for c in index if c.stock_code and c.code != target.code}
    evidence, audit, errors = [], [], []
    schema = obj({"suitable": {"type": "boolean"}, "score": {"type": "integer", "minimum": 0, "maximum": 100},
                  "reason": TEXT, "differences": TEXT, "target_quote": TEXT, "peer_quote": TEXT})
    for position, (code, company) in enumerate(available.items(), 1):
        progress(f"AI peer 전체 후보 평가 {position}/{len(available)}: {company.name}")
        try:
            exclusions = profile_reasons(source.profile(company))
            if exclusions:
                audit.append({"code": code, "name": company.name, "status": "범위 제외", "reason": exclusions})
                continue
            doc = source.latest(company, year)
            profile = business_profile(ai, doc)
            row = ai.ask("peer_assessment", "두 기업의 사업보고서 기반 프로필을 비교하세요. 제품·서비스(40점), "
                "매출 구성(30점), 가치사슬·사업모델(20점), 고객·지역(10점)의 유사성을 합산해 score를 부여하세요. "
                "다각화·주력사업 차이를 반영하고 적절한 비교기업일 때만 suitable=true. "
                "원문 근거 없는 유사성은 인정하지 말고 각 프로필에서 원문 인용을 그대로 복사하세요.",
                {"target": {k: target_profile[k] for k in ("summary", "quotes", "year")},
                 "candidate": {k: profile[k] for k in ("summary", "quotes", "year")}}, schema)
            if row["suitable"] and (not quoted(row["target_quote"], target_doc["text"]) or not quoted(row["peer_quote"], doc["text"])):
                raise AIError("peer 선정 인용 불일치")
            audit.append({"code": code, "name": company.name, "status": "평가 완료", "assessment": row, "profile": profile})
            if row["suitable"]:
                evidence.append({**row, "code": code, "name": company.name, "source_url": doc["source_url"],
                                 "target_source_url": target_doc["source_url"], "report_year": doc["year"]})
        except (DartError, ValueError) as error:
            errors.append(f"{company.name}: {error}")
            audit.append({"code": code, "name": company.name, "status": "평가 실패", "reason": str(error)})
    evidence.sort(key=lambda row: (-row["score"], row["code"]))
    # Only final report size stays at 10 peers; every eligible candidate is evaluated.
    selected = evidence[:10]
    limitation = "전체 상장 후보의 적격성 및 사업보고서 평가; AI 유사성 점수순 최종 최대 10개."
    if errors:
        limitation += f" {len(errors)}개 후보 평가 실패: 전체 평가 미완료, 성공한 후보 중 선정."
    if len(selected) < 5:
        limitation += f" 적격 peer {len(selected)}개: 유효 비교기업 5개 미만 지표는 IQR 판정 유보."
    return [available[r["code"]] for r in selected], {"mode": "AI", "peers": selected,
        "limitations": limitation, "errors": errors, "candidate_audit": audit, "target_profile": target_profile}, target_doc


def _extract_credit_part(ai, doc, scope, text):
    fields = (*NUMERIC_FIELDS, "opening_allowance")
    value_schema = obj({"value": {"type": ["string", "null"]}, "unit": {"type": "string", "enum": ["원", "천원", "백만원", "억원", "unknown"]},
                        "quote": TEXT, "note": TEXT})
    schema = obj({"fs_div": TEXT, "currency": TEXT, "scope": TEXT, "balance_date": TEXT,
                  "context_quote": TEXT, "reason": TEXT, "values": obj({f: value_schema for f in fields})})
    result = ai.ask("credit_notes", "매출채권만의 연결(CFS) 주석을 추출하세요. 기타채권·계약자산·전체 금융자산 합계는 금지합니다. "
        "요청 사업연도 당기 기말 금액 및 당기 변동을 구분하세요. opening_allowance는 같은 당기 변동표의 기초입니다. "
        "value는 원문 단위의 숫자 문자열(쉼표 없이), 없으면 null. 각 값의 표 머리글·단위·당기/전기·계정명이 확인되는 원문을 quote에 그대로 복사하세요. "
        "당기 ECL 비용(환입 음수)은 명시된 값만 추출하고 충당금 증감으로 계산하지 마세요. 제각 등은 양수 크기입니다. "
        "fs_div, currency, scope(total_trade/current_trade), balance_date를 근거로 확인하고 불명확하면 unknown을 쓰세요. "
        "context_quote에는 연결·해당연도·채권 범위를 판단한 원문을 인용하세요. 추정·합산으로 빈값을 채우지 마세요.",
        {"year": doc["year"], "requested_scope": scope, "text": text}, schema)
    if (result["fs_div"] != "CFS" or result["currency"] != "KRW" or result["scope"] != scope
            or result["balance_date"] != f"{doc['year']}-12-31" or not quoted(result["context_quote"], text)):
        return None  # Non-credit/standalone/other-period segments are not accepted.
    row = {k: doc[k] for k in ("corp_code", "year", "rcept_no", "source_url")}
    row.update(fs_div="CFS", currency="KRW", scope=scope, balance_date=result["balance_date"],
               reported_basis="not_available", source_ref="AI 주석 추출: " + result["reason"],
               origin="AI", evidence=result, context_quote=result["context_quote"])
    multipliers = {"원": 1, "천원": 1000, "백만원": 1000000, "억원": 100000000}
    for field, cell in result["values"].items():
        row[field] = None
        if cell["value"] is None:
            continue
        if not quoted(cell["quote"], text) or cell["unit"] not in multipliers:
            raise AIError(f"ECL {field}: 인용 또는 단위 확인 실패")
        try:
            value = Decimal(cell["value"])
        except InvalidOperation:
            raise AIError(f"ECL {field}: 숫자 형식 오류") from None
        if not value.is_finite() or (value < 0 and field not in ("ecl_expense", "other_movements")):
            raise AIError(f"ECL {field}: 금액 부호/유한값 오류")
        # The source must contain the numerical magnitude, before unit conversion.
        tokens = re.findall(r"-?\d[\d,]*(?:\.\d+)?", cell["quote"])
        if not any(abs(Decimal(t.replace(",", ""))) == abs(value) for t in tokens):
            raise AIError(f"ECL {field}: 인용에서 숫자를 확인할 수 없음")
        row[field] = str(value * multipliers[cell["unit"]])
    return row


def extract_credit(ai, doc, scope):
    parts, coverage = [], []
    for chunk in document_chunks(doc["text"]):
        row = _extract_credit_part(ai, doc, scope, chunk["context"] + "\n" + chunk["text"])
        if row is not None:
            parts.append(row)
        coverage.append({"start": chunk["start"], "end": chunk["end"]})
    if not parts:
        raise AIError("전체 공시에서 연결·통화·연도·범위가 확인된 ECL 주석 없음")
    merged = dict(parts[0])
    conflicts = []
    for field in (*NUMERIC_FIELDS, "opening_allowance"):
        values = {Decimal(row[field]) for row in parts if row[field] is not None}
        merged[field] = str(next(iter(values))) if len(values) == 1 else None
        if len(values) > 1:
            conflicts.append(field)
    merged["evidence"] = {"parts": [p["evidence"] for p in parts], "coverage": coverage, "conflicts": conflicts}
    merged["source_ref"] = "AI 전체 공시 분할 추출" + ("; 상충 금액 유보: " + ", ".join(conflicts) if conflicts else "")
    # Do not let the legacy calculator reconstruct a conflicting balance from the other two.
    if set(conflicts) & {"gross", "allowance", "net"}:
        merged.update(gross=None, allowance=None, net=None)
    return merged


def collect_credit(ai, source, snapshot, manual, scope, output, progress=print):
    rows, evidence, documents = [], [], {}
    for company in snapshot["companies"]:
        for year, data in company["years"].items():
            key = (company["code"], int(year))
            if key in manual:
                continue
            receipts = {r.get("rcept_no") for r in data["full"] if r.get("sj_div") in ("BS", "IS", "CIS")}
            if len(receipts) != 1:
                continue
            try:
                progress(f"AI ECL 추출: {company['name']} {year}")
                doc = source.read(next(iter(receipts)), *key)
                documents[key] = doc
                row = extract_credit(ai, doc, scope)
                # Reuse the existing note contract before accepting any row.
                temporary = output / "credit_notes_auto.json"
                temporary.write_text(json.dumps({"schema_version": 1, "records": [row]}, ensure_ascii=False), encoding="utf-8")
                load_credit_notes(temporary)
                rows.append(row)
                evidence.append({"company": company["name"], "year": year, "status": "추출 완료·계산 검증 별도", "record": row})
            except (AIError, DartError, ValueError) as error:
                evidence.append({"company": company["name"], "year": year, "status": "미실행/실패", "reason": str(error)})
    path = output / "credit_notes_auto.json"
    path.write_text(json.dumps({"schema_version": 1, "records": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_credit_notes(path), evidence, documents


def explain(ai, analysis, documents):
    explanations = []
    findings = [("기업 간", r) for r in analysis["cross_outliers"]] + [("연도 간", r) for r in analysis["time_outliers"]]
    schema = obj({"meaning": TEXT, "impact": TEXT, "hypotheses": TEXT,
                  "confirmed_facts": array(obj({"statement": TEXT, "quote": TEXT, "url": TEXT}))})
    for code in sorted({r["code"] for _, r in findings}):
        selected = [(kind, r) for kind, r in findings if r["code"] == code and r["flag"] not in ("범위 내", "기준 내")]
        if not selected:
            continue
        checkpoint = len(explanations)
        try:
            scans = [explanation_evidence(ai, doc, [r for _, r in selected])
                     for (c, _), doc in documents.items() if c == code]
            facts = [fact for scan in scans for fact in scan["facts"]]
            for kind, finding in selected:
                parts, confirmed = [], []
                for batch in list(batches(facts)) or [[]]:
                    item = ai.ask("outlier_explanation", "Python 판정과 숫자를 변경하지 마세요. 판정 유보는 이상치로 단정하지 마세요. "
                        "meaning은 의미·판정 이유, impact는 가능한 손익·현금흐름·자산건전성 영향, hypotheses는 미확인 원인 가설입니다. "
                        "confirmed_facts에는 입력 근거로 확인된 사실만 원문 인용과 URL을 그대로 넣으세요. 근거 없으면 빈 배열.",
                        {"kind": kind, "finding": finding, "evidence": batch}, schema)
                    for fact in item["confirmed_facts"]:
                        if not any(fact["url"] == f["url"] and quoted(fact["quote"], f["quote"]) for f in batch):
                            raise AIError("AI 해설 공시 인용 불일치")
                        if fact not in confirmed:
                            confirmed.append(fact)
                    parts.append({k: item[k] for k in ("meaning", "impact", "hypotheses")})
                # Consume every evidence batch, keeping original citations separately.
                while len(parts) > 1:
                    reduced = []
                    for start in range(0, len(parts), 4):
                        reduced.append(ai.ask("explanation_synthesis", "모든 부분 해설을 통합하세요. 중복만 정리하고 서로 다른 근거·상충 사실·"
                            "불확실성을 유지하세요. 확인 사실과 가설을 섞지 말고 Python 판정을 바꾸지 마세요.",
                            {"finding": finding, "parts": parts[start:start + 4]},
                            obj({"meaning": TEXT, "impact": TEXT, "hypotheses": TEXT})))
                    parts = reduced
                explanations.append({"kind": kind, "finding": finding, "status": "완료", **parts[0],
                    "confirmed_facts": confirmed, "document_coverage": [{"url": d["url"], "coverage": d["coverage"]} for d in scans]})
        except AIError as error:
            del explanations[checkpoint:]
            explanations.extend({"kind": kind, "finding": r, "status": str(error)} for kind, r in selected)
    return explanations
