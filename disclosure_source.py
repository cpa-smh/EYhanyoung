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


def excerpts(text, keywords, limit=24000):
    """Bounded windows retain nearby headings, units and table column labels."""
    ranges = []
    pattern = "|".join(re.escape(k) for k in keywords)
    for match in re.finditer(pattern, text, re.I):
        start, end = max(0, match.start() - 1200), min(len(text), match.end() + 3000)
        if ranges and start <= ranges[-1][1]:
            ranges[-1][1] = max(end, ranges[-1][1])
        else:
            ranges.append([start, end])
    chunks, size = [], 0
    for start, end in ranges:
        chunk = text[start:end][:limit - size]
        chunks.append(f"[원문 문자 {start}:{start + len(chunk)}]\n" + chunk)
        size += len(chunk)
        if size >= limit:
            break
    return "\n\n".join(chunks)


class DisclosureSource:
    def __init__(self, client, cache_dir):
        self.client = client
        self.cache = Path(cache_dir) / "dart"
        self.cache.mkdir(parents=True, exist_ok=True)

    def latest(self, company, end_year):
        # Pick the latest annual period no later than the requested analysis year.
        rows = []
        for page in range(1, 21):
            data = self.client.json_data("list.json", corp_code=company.code,
                bgn_de=f"{end_year}0101", end_de=date.today().strftime("%Y%m%d"),
                pblntf_detail_ty="A001", last_reprt_at="Y", page_count="100", page_no=str(page))
            rows.extend(data.get("list", []))
            if page >= int(data.get("total_page", 1)):
                break
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
