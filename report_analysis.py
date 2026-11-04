"""Full-document evidence passes. Request boundaries never truncate the corpus."""
import json
from ai_client import AIError, TEXT, obj
from disclosure_source import document_chunks


def array(schema):
    return {"type": "array", "items": schema}


def quoted(quote, text):
    return bool(quote.strip()) and "".join(quote.split()) in "".join(text.split())


PROFILE_SCHEMA = obj({"summary": TEXT, "quotes": array(TEXT)})


def business_profile(ai, doc):
    parts = []
    coverage = []
    for chunk in document_chunks(doc["text"]):
        result = ai.ask("business_scan", "사업보고서 구간에서 제품·서비스, 사업부문별 매출 비중, 가치사슬 위치, 고객·지역, "
            "사업모델·규모·회계 비교가능성을 빠짐없이 정리하세요. 자료가 없으면 summary는 빈 문자열, quotes는 빈 배열. "
            "종속회사 단독사업과 연결그룹의 주력사업을 구분하세요. 중요 사실의 원문을 quotes에 그대로 인용하세요. "
            "외부 지식은 금지합니다. 구간 밖의 내용을 추정하지 마세요.",
            {"year": doc["year"], **chunk}, PROFILE_SCHEMA)
        if any(not quoted(q, chunk["text"] + chunk["context"]) for q in result["quotes"]):
            raise AIError("사업보고서 프로필 인용 불일치")
        if result["summary"]:
            if not result["quotes"]:
                raise AIError("사업보고서 프로필 근거 누락")
            parts.append(result)
        coverage.append({"start": chunk["start"], "end": chunk["end"]})
    if not parts:
        raise AIError("사업보고서 전체를 읽었으나 사업구조 근거를 확보하지 못했습니다.")
    originals = list(parts)
    # Hierarchical synthesis consumes every part, rather than keeping the first N.
    while len(parts) > 1:
        merged = []
        for start in range(0, len(parts), 4):
            group = parts[start:start + 4]
            if len(group) == 1:
                merged.extend(group)
                continue
            result = ai.ask("business_synthesis", "모든 구간 분석을 통합하세요. 중복만 정리하고 주요 사업·매출 구성·다각화·" 
                "가치사슬·지역·규모의 중요한 차이와 상충 정보를 유지하세요. 수치와 기간을 유지하세요. "
                "quotes는 입력의 원문 인용 중 통합 프로필의 주요 판단을 뒷받침하는 것을 그대로 복사하세요.", group, PROFILE_SCHEMA)
            allowed = [q for part in group for q in part["quotes"]]
            if not result["summary"] or not result["quotes"] or any(q not in allowed for q in result["quotes"]):
                raise AIError("사업 프로필 통합 근거 검증 실패")
            merged.append(result)
        parts = merged
    return {**parts[0], "source_url": doc["source_url"], "year": doc["year"],
            "coverage": coverage, "parts": originals, "characters": len(doc["text"])}


def explanation_evidence(ai, doc, findings):
    """Read every report segment for this company's numerical findings."""
    facts, coverage, seen = [], [], set()
    schema = obj({"facts": array(obj({"statement": TEXT, "quote": TEXT}))})
    for chunk in document_chunks(doc["text"]):
        result = ai.ask("explanation_evidence", "주어진 재무지표의 변동 및 동종기업 차이에 관련된 공시 사실을 찾으세요. "
            "제품 구성·가격·원가·회수·재고·손상·사업결합·회계정책·재작성 등을 포함합니다. "
            "명시되지 않은 인과관계는 만들지 마세요. 관련 사실이 없으면 빈 배열. quote는 원문 그대로 복사하세요.",
            {"year": doc["year"], "findings": findings, **chunk}, schema)
        for fact in result["facts"]:
            if not quoted(fact["quote"], chunk["text"] + chunk["context"]):
                raise AIError("이상치 근거 탐색 인용 불일치")
            key = (fact["statement"], fact["quote"])
            if key not in seen:
                seen.add(key)
                facts.append({**fact, "url": doc["source_url"], "year": doc["year"]})
        coverage.append({"start": chunk["start"], "end": chunk["end"]})
    return {"facts": facts, "coverage": coverage, "url": doc["source_url"]}


def batches(items, size=32000):
    """Pack all records into requests without dropping any tail records."""
    batch, used = [], 0
    for item in items:
        length = len(json.dumps(item, ensure_ascii=False, default=str))
        if batch and used + length > size:
            yield batch
            batch, used = [], 0
        batch.append(item)
        used += length
    if batch:
        yield batch
