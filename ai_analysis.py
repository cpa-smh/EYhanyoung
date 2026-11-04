"""Evidence-backed AI additions; numerical judgments remain in the existing pipeline."""
from decimal import Decimal, InvalidOperation
import json
import re

from ai_client import AIError, TEXT, obj
from credit_risk import NUMERIC_FIELDS, load_credit_notes
from dart_client import DartError
from disclosure_source import excerpts
from period_validator import profile_reasons

BUSINESS = ("사업의 내용", "주요 제품", "주요제품", "매출실적", "매출 구성", "사업부문")
CREDIT = ("매출채권", "손실충당금", "기대신용손실", "신용위험", "연결재무제표 주석")


def array(schema):
    return {"type": "array", "items": schema}


def quoted(quote, text):
    return bool(quote.strip()) and re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", text)


def select_peers(ai, source, index, target, year, progress=print):
    target_doc = source.latest(target, year)
    target_text = excerpts(target_doc["text"], BUSINESS, 18000)
    if not target_text:
        raise AIError("대상기업 사업 내용 발췌 없음: 자동 peer 선정 유보")
    available = {c.code: c for c in index if c.stock_code and c.code != target.code}
    shortlist = ai.ask("peer_candidates", "대상기업 사업보고서를 읽고 실제 기업 목록에서 사업보고서를 확인할 후보를 최대 20개 제안하세요. "
        "이 단계는 검색 후보이며 최종 peer 선정이 아닙니다. 목록 밖 기업은 금지합니다.",
        {"target": target_text, "directory": [{"code": c.code, "name": c.name} for c in available.values()]},
        obj({"codes": array(TEXT)}))
    candidates, docs, errors = {}, {}, []
    for code in dict.fromkeys(shortlist["codes"]):
        if len(candidates) >= 20:
            break
        company = available.get(code)
        if company is None:
            continue
        try:
            if profile_reasons(source.profile(company)):
                continue
            progress(f"AI peer 근거 수집: {company.name}")
            doc = source.latest(company, year)
            text = excerpts(doc["text"], BUSINESS, 12000)
            if text:
                candidates[code] = {"code": code, "name": company.name, "year": doc["year"],
                                    "source_url": doc["source_url"], "text": text}
                docs[code] = doc
        except (DartError, ValueError) as error:
            errors.append(f"{company.name}: {error}")
    if not candidates:
        raise AIError("사업보고서 근거를 확보한 적격 peer 후보가 없습니다.")
    schema = obj({"peers": array(obj({"code": TEXT, "reason": TEXT, "differences": TEXT,
                  "target_quote": TEXT, "peer_quote": TEXT})), "limitations": TEXT})
    result = ai.ask("peer_selection", "제공된 사업보고서의 제품·서비스·사업부문·매출 구조를 비교해 적합한 peer 5~10개를 선정하세요. "
        "근거가 부족하거나 유사하지 않으면 제외하고 부족한 수를 억지로 채우지 마세요. "
        "대상 및 후보 보고서에서 선정 근거 문구를 각각 그대로 인용하세요. 추출문 이외의 사전 지식은 선정 근거로 사용하지 마세요.",
        {"target": {"name": target.name, "text": target_text}, "candidates": list(candidates.values())}, schema)
    selected, evidence, seen = [], [], set()
    for row in result["peers"]:
        code = row["code"]
        if code in seen or code not in candidates:
            continue
        if not quoted(row["target_quote"], target_text) or not quoted(row["peer_quote"], candidates[code]["text"]):
            errors.append(f"{code}: 선정 근거 인용 불일치로 제외")
            continue
        seen.add(code)
        selected.append(available[code])
        evidence.append({**row, "name": available[code].name, "source_url": docs[code]["source_url"],
                         "target_source_url": target_doc["source_url"], "report_year": docs[code]["year"]})
        if len(selected) == 10:
            break
    limitation = result["limitations"]
    if len(selected) < 5:
        limitation += f" 적격·근거 확인 peer {len(selected)}개: 유효 비교기업 5개 미만 지표는 IQR 판정 유보."
    return selected, {"mode": "AI", "peers": evidence, "limitations": limitation, "errors": errors}, target_doc


def extract_credit(ai, doc, scope):
    text = excerpts(doc["text"], CREDIT, 60000)
    if not text:
        raise AIError("매출채권 관련 주석 발췌 없음")
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
        raise AIError("ECL 주석의 연결·통화·연도·범위 근거 확인 실패")
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
    # Per-company batches limit requests, retaining all flagged/pending findings.
    for code in sorted({r["code"] for _, r in findings}):
        selected = [(kind, r) for kind, r in findings if r["code"] == code and r["flag"] not in ("범위 내", "기준 내")]
        if not selected:
            continue
        evidence = [{"year": y, "url": d["source_url"], "text": excerpts(d["text"], BUSINESS + CREDIT, 12000)}
                    for (c, y), d in documents.items() if c == code]
        schema = obj({"items": array(obj({"id": {"type": "integer"}, "meaning": TEXT, "impact": TEXT,
                    "hypotheses": TEXT, "confirmed_facts": array(obj({"statement": TEXT, "quote": TEXT, "url": TEXT}))}))})
        try:
            result = ai.ask("outlier_explanation", "Python 판정을 바꾸거나 숫자를 재계산하지 말고 모든 findings를 설명하세요. "
                "판정 유보는 이상치로 단정하지 말고 표본/결측 한계를 설명하세요. meaning은 재무적 의미, impact는 손익·현금흐름·자산건전성에 대한 가능한 영향입니다. "
                "hypotheses에는 추가 확인할 원인 가설을 명시하세요. 공시로 확인된 사실만 confirmed_facts에 원문 인용과 URL을 넣으세요. "
                "근거가 없으면 confirmed_facts는 빈 배열. 상관관계를 원인으로 단정하지 마세요.",
                {"findings": [{"id": i, "kind": kind, **r} for i, (kind, r) in enumerate(selected)], "documents": evidence}, schema)
            by_id = {item["id"]: item for item in result["items"]}
            if len(by_id) != len(result["items"]) or set(by_id) != set(range(len(selected))):
                raise AIError("AI 해설 항목 누락/중복")
            for i, (kind, finding) in enumerate(selected):
                item = by_id[i]
                for fact in item["confirmed_facts"]:
                    if not any(fact["url"] == d["url"] and quoted(fact["quote"], d["text"]) for d in evidence):
                        raise AIError("AI 해설 공시 인용 불일치")
            for i, (kind, finding) in enumerate(selected):
                explanations.append({"kind": kind, "finding": finding, "status": "완료", **by_id[i]})
        except AIError as error:
            explanations.extend({"kind": kind, "finding": r, "status": str(error)} for kind, r in selected)
    return explanations
