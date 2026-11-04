"""DART annual-report originals, cached by receipt; keep tables and section context."""
from datetime import date
from io import BytesIO
import json
import re
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from dart_client import DartError, NoDataError, check_status


def url(receipt):
    return "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt


def document_text(payload):
    try:
        if not payload.startswith(b"PK"):
            check_status(ET.fromstring(payload).findtext("status"))
            raise DartError("공시 원문 ZIP 없음")
        texts = []
        with ZipFile(BytesIO(payload)) as archive:
            infos = [i for i in archive.infolist() if i.filename.lower().endswith(".xml")]
            if sum(i.file_size for i in infos) > 100_000_000:
                raise DartError("공시 원문 크기 제한 초과")
            for info in infos:
                root = ET.fromstring(archive.read(info))
                def render(node):
                    tag = node.tag.rsplit("}", 1)[-1].upper()
                    text = (node.text or "") + "".join(render(c) + (c.tail or "") for c in node)
                    if tag in ("TD", "TH", "TE"):
                        return text + " | "
                    if tag in ("P", "TR", "TITLE", "SUBTITLE", "TABLE", "SECTION-1", "SECTION-2"):
                        return "\n" + text + "\n"
                    return text
                texts.append(info.filename + "\n" + render(root))
        text = re.sub(r"[ \t]+", " ", "\n".join(texts))
        return re.sub(r"\n\s*\n+", "\n", text).strip()
    except (BadZipFile, ET.ParseError, DefusedXmlException):
        raise DartError("공시 원문 ZIP/XML 파싱 실패") from None


def document_chunks(text, size=24000, overlap=4000):
    """Cover every character, with overlapping boundaries and carried section context.

    size limits an individual request, never the amount of report processed.
    Prefer paragraph/row boundaries; even a huge single row is covered in full.
    """
    if size <= overlap or overlap < 0:
        raise ValueError("chunk size must exceed overlap")
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start + overlap:
                end = boundary + 1
        # Carry the most recent section titles and table header/units. No body is dropped.
        prefix = text[:start]
        context_lines = [line for line in prefix.splitlines() if
            (len(line) < 160 and any(k in line for k in ("재무제표", "주석", "단위", "당기", "전기", "사업의 내용")))]
        context = "\n".join(context_lines[-12:])
        yield {"start": start, "end": end, "text": text[start:end], "context": context}
        if end == len(text):
            break
        start = end - overlap


class DisclosureSource:
    def __init__(self, client, cache_dir):
        self.client = client
        self.cache = Path(cache_dir) / "dart"
        self.cache.mkdir(parents=True, exist_ok=True)

    def latest(self, company, end_year):
        # Pick the latest annual period no later than the requested analysis year.
        rows = []
        page = 1
        while True:
            data = self.client.json_data("list.json", corp_code=company.code,
                bgn_de=f"{end_year}0101", end_de=date.today().strftime("%Y%m%d"),
                pblntf_detail_ty="A001", last_reprt_at="Y", page_count="100", page_no=str(page))
            rows.extend(data.get("list", []))
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
        candidates = []
        for row in rows:
            match = re.search(r"사업보고서\s*\((\d{4})\.12\)", row.get("report_nm", ""))
            if match and int(match[1]) <= end_year and row.get("corp_code") == company.code:
                candidates.append((int(match[1]), row["rcept_no"]))
        if not candidates:
            raise NoDataError("사업보고서 원문을 찾지 못했습니다.")
        year, receipt = max(candidates)
        return self.read(receipt, company.code, year)

    def read(self, receipt, code, year):
        if not re.fullmatch(r"\d{14}", receipt):
            raise DartError("잘못된 공시 접수번호")
        path = self.cache / (receipt + ".txt")
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            raw = self.client._get("document.xml", rcept_no=receipt).content
            text = document_text(raw)
            if not text:
                raise DartError("공시 원문 텍스트 없음")
            (self.cache / (receipt + ".zip")).write_bytes(raw)
            path.write_text(text, encoding="utf-8")
        return {"corp_code": code, "year": year, "rcept_no": receipt, "source_url": url(receipt), "text": text}

    def profile(self, company):
        path = self.cache / f"profile_{company.code}_{date.today().isoformat()}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        result = self.client.profile(company.code)
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result
