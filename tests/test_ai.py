from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook
from ai_client import AIClient, AIError, TEXT, obj
from ai_analysis import extract_credit, select_peers, explain
from dart_client import Company
from disclosure_source import document_text


def test_response_cache_and_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = []
    class Session:
        def post(self, url, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status_code=200, json=lambda: {"status": "completed", "output": [
                {"content": [{"type": "output_text", "text": '{"answer":"ok"}'}]}]})
    client = AIClient(tmp_path, Session())
    for _ in range(2):
        assert client.ask("test", "test", {}, obj({"answer": TEXT})) == {"answer": "ok"}
    assert len(calls) == 1
    assert "test-key" not in next((tmp_path / "ai").glob("*.json")).read_text()


def test_original_table_preserved():
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("report.xml", '<DOC><TITLE>연결재무제표 주석</TITLE><TABLE><TR><TD>당기</TD><TD>매출채권 1,000</TD></TR></TABLE></DOC>')
    text = document_text(data.getvalue())
    assert "당기 | 매출채권 1,000 |" in text


def credit_response():
    from credit_risk import NUMERIC_FIELDS
    text = "2025년 12월 31일 연결재무제표 주석 전체 매출채권 (단위: 백만원) 당기 총액 100 손실충당금 10 순액 90"
    values = {f: {"value": None, "unit": "unknown", "quote": "", "note": ""} for f in (*NUMERIC_FIELDS, "opening_allowance")}
    for f, v in (("gross", "100"), ("allowance", "10"), ("net", "90")):
        values[f] = {"value": v, "unit": "백만원", "quote": text, "note": "매출채권"}
    return text, {"fs_div": "CFS", "currency": "KRW", "scope": "total_trade", "balance_date": "2025-12-31",
                  "context_quote": text, "reason": "매출채권 주석", "values": values}


def test_credit_units_null_and_evidence():
    text, response = credit_response()
    ai = SimpleNamespace(ask=lambda *args: deepcopy(response))
    doc = {"text": text, "year": 2025, "corp_code": "00000001", "rcept_no": "20260301000001", "source_url": "https://dart.fss.or.kr/"}
    result = extract_credit(ai, doc, "total_trade")
    assert result["gross"] == "100000000"
    assert result["net"] == "90000000"
    assert result["ecl_expense"] is None
    response["values"]["gross"]["value"] = "999"
    with pytest.raises(AIError, match="숫자"):
        extract_credit(ai, doc, "total_trade")


def test_peer_only_verified_candidates():
    target, peer = Company("00000001", "대상", "1"), Company("00000002", "후보", "2")
    source = SimpleNamespace(profile=lambda c: {"corp_cls": "Y", "acc_mt": "12", "induty_code": "26"},
        latest=lambda c, y: {"text": "사업의 내용 반도체 제조", "year": y, "source_url": "https://dart.fss.or.kr/"})
    def ask(name, *args):
        if name == "business_scan":
            return {"summary": "반도체 제조", "quotes": ["반도체 제조"]}
        return {"suitable": True, "score": 90, "reason": "반도체", "differences": "규모",
                "target_quote": "반도체 제조", "peer_quote": "반도체 제조"}
    selected, evidence, _ = select_peers(SimpleNamespace(ask=ask), source, [target, peer], target, 2025)
    assert selected == [peer]
    assert "5개 미만" in evidence["limitations"]


def test_explanation_failure_preserves_python_judgment(analysis):
    def fail(*args):
        raise AIError("통신 실패")
    original = deepcopy(analysis["cross_outliers"])
    result = explain(SimpleNamespace(ask=fail), analysis, {})
    assert result and all(r["status"] == "통신 실패" for r in result)
    assert analysis["cross_outliers"] == original


def test_target_only_cli_mocked_end_to_end(monkeypatch, tmp_path):
    import main as cli
    import ai_analysis
    from sample_data import synthetic_inputs
    snapshot, raw_notes = synthetic_inputs(2023, 2025)
    snapshot["synthetic"] = False
    companies = [Company(c["code"], c["name"], c["stock_code"]) for c in snapshot["companies"]]
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    monkeypatch.setattr(cli, "DartClient", lambda key: SimpleNamespace(companies=lambda: companies, close=lambda: None))
    monkeypatch.setattr(cli, "collect", lambda *args: snapshot)
    monkeypatch.setattr(ai_analysis, "select_peers", lambda *args: (companies[1:], {"mode": "AI", "peers": [], "limitations": "mock"},
        {"year": 2025, "text": "사업의 내용", "source_url": "https://dart.fss.or.kr/"}))
    def credit(ai, source, snap, manual, scope, output):
        from credit_risk import load_credit_notes
        path = output / "mock_notes.json"
        path.write_text(json.dumps(raw_notes), encoding="utf-8")
        docs = {(c["code"], 2025): {"text": "사업의 내용", "source_url": "https://dart.fss.or.kr/"} for c in snap["companies"]}
        return load_credit_notes(path), [], docs
    monkeypatch.setattr(ai_analysis, "collect_credit", credit)
    monkeypatch.setattr(ai_analysis, "explain", lambda *args: [])
    out = tmp_path / "run"
    assert cli.main(["--target", companies[0].name, "--output-dir", str(out), "--cache-dir", str(tmp_path / "cache")]) == 0
    book = load_workbook(out / "peer_financial_ratio_analysis_2025.xlsx")
    assert {"AI peer 선정", "AI 이상치 해설", "AI ECL 추출 근거"}.issubset(book.sheetnames)
    assert book["재무비율 계산"]["E7"].value == pytest.approx(.125)
    book.close()
    assert (out / "credit_notes.json").exists()


def test_chunks_cover_entire_report_and_tail():
    from disclosure_source import document_chunks
    text = ("매출채권 자료\n" * 12000) + "마지막 ECL 핵심표"
    chunks = list(document_chunks(text))
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == len(text)
    assert all(b["start"] <= a["end"] for a, b in zip(chunks, chunks[1:]))
    assert "마지막 ECL 핵심표" in chunks[-1]["text"]


def test_peers_beyond_twentieth_are_evaluated():
    target = Company("00000001", "대상", "1")
    peers = [Company(f"{i:08}", f"후보{i}", str(i)) for i in range(2, 27)]
    visited = []
    def latest(c, year):
        visited.append(c.code)
        return {"text": "사업의 내용 반도체 제조", "year": year, "source_url": "https://dart.fss.or.kr/"}
    source = SimpleNamespace(profile=lambda c: {"corp_cls": "Y", "acc_mt": "12", "induty_code": "26"}, latest=latest)
    def ask(name, *args):
        if name == "business_scan":
            return {"summary": "반도체 제조", "quotes": ["반도체 제조"]}
        return {"suitable": True, "score": 90, "reason": "반도체", "differences": "규모", "target_quote": "반도체 제조", "peer_quote": "반도체 제조"}
    selected, evidence, _ = select_peers(SimpleNamespace(ask=ask), source, [target, *peers], target, 2025, progress=lambda x: None)
    assert len(visited) == 26 and peers[-1].code in visited
    assert len(evidence["candidate_audit"]) == 25 and len(selected) == 10


def test_credit_reads_tail_and_does_not_accept_conflicting_amounts():
    text, response = credit_response()
    calls = []
    def ask(name, instruction, payload, schema):
        calls.append(payload["text"])
        result = deepcopy(response)
        if text not in payload["text"]:
            result["fs_div"] = "unknown"
        return result
    doc = {"text": "무관한 내용\n" * 15000 + text, "year": 2025, "corp_code": "00000001",
           "rcept_no": "20260301000001", "source_url": "https://dart.fss.or.kr/"}
    result = extract_credit(SimpleNamespace(ask=ask), doc, "total_trade")
    assert len(calls) > 1 and result["gross"] == "100000000"
    assert result["evidence"]["coverage"][-1]["end"] == len(doc["text"])
