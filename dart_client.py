"""네트워크 통신만 담당한다. API 키가 담긴 URL/예외 원문은 출력하지 않는다."""
from dataclasses import dataclass
from io import BytesIO
import re
import time
from zipfile import ZipFile, BadZipFile

import requests
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

BASE_URL = "https://opendart.fss.or.kr/api/"


class DartError(Exception):
    """사용자에게 안전하게 보여 줄 수 있는 오류."""


class NoDataError(DartError):
    pass


def check_status(status):
    if status == "000":
        return
    if status == "013":
        raise NoDataError("조회된 재무제표가 없습니다 (013).")
    messages = {
        "010": "등록되지 않은 API 키", "011": "사용 중지된 API 키",
        "012": "허용되지 않은 IP", "014": "파일 없음", "020": "API 요청 한도 초과",
        "100": "잘못된 요청 인자", "101": "부적절한 접근",
        "800": "OpenDART 점검 중", "900": "OpenDART 서버 오류",
        "901": "API 키 사용기간 만료",
    }
    # 서버 message 필드를 그대로 출력하지 않아 비밀정보 반사 노출을 막는다.
    raise DartError(messages.get(status, "알 수 없는 OpenDART 응답 상태"))


@dataclass(frozen=True)
class Company:
    code: str
    name: str
    stock_code: str = ""


class DartClient:
    def __init__(self, api_key, session=None, sleep=time.sleep):
        if not re.fullmatch(r"[a-zA-Z0-9]{40}", api_key or ""):
            raise DartError(".env의 OPENDART_API_KEY에 발급받은 40자리 키를 입력하세요.")
        self._key = api_key
        self.session = session or requests.Session()
        self.sleep = sleep

    def close(self):
        self.session.close()

    def json_data(self, endpoint, **params):
        try:
            data = self._get(endpoint, **params).json()
        except ValueError:
            raise DartError("OpenDART 응답이 JSON이 아닙니다.") from None
        if not isinstance(data, dict):
            raise DartError("OpenDART 응답 구조가 잘못되었습니다.")
        check_status(data.get("status"))
        return data

    def profile(self, code):
        return self.json_data("company.json", corp_code=code)

    def period_accounts(self, code, year, stock_code):
        """전체 재무제표 API에 없는 실제 결산일/손익기간을 보조 API로 확인."""
        data = self.json_data("fnlttSinglAcnt.json", corp_code=code,
                              bsns_year=str(year), reprt_code="11011")
        rows = data.get("list")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise DartError("기간 확인용 주요계정 응답이 잘못되었습니다.")
        for row in rows:
            if (row.get("bsns_year") != str(year) or row.get("reprt_code") != "11011"
                    or row.get("stock_code") != stock_code):
                raise DartError("기간 확인 응답의 기업·연도·보고서가 다릅니다.")
        return rows

    def _get(self, endpoint, **params):
        """시간제한 + 최대 3회 시도. 인증 오류/요청 한도는 재시도하지 않는다."""
        for attempt in range(3):
            try:
                response = self.session.get(
                    BASE_URL + endpoint, params={"crtfc_key": self._key, **params},
                    timeout=(10, 40), allow_redirects=False,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        self.sleep(2 ** attempt)
                        continue
                    raise DartError("서버 혼잡/HTTP 요청 제한입니다. 잠시 후 다시 실행하세요.")
                if response.status_code != 200:
                    raise DartError("OpenDART HTTP 요청에 실패했습니다.")
                return response
            except (requests.Timeout, requests.ConnectionError):
                if attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise DartError("OpenDART 연결 실패: 네트워크를 확인하세요.") from None
            except requests.RequestException:
                raise DartError("OpenDART 통신 오류가 발생했습니다.") from None

    def companies(self):
        payload = self._get("corpCode.xml").content
        try:
            if not payload.startswith(b"PK"):
                root = ET.fromstring(payload)
                check_status(root.findtext("status"))
                raise DartError("기업 목록 응답이 ZIP 형식이 아닙니다.")
            with ZipFile(BytesIO(payload)) as archive:
                info = archive.getinfo("CORPCODE.xml")
                if info.file_size > 100_000_000:
                    raise DartError("기업 목록 파일이 허용 크기를 초과했습니다.")
                root = ET.fromstring(archive.read(info))
            companies = [Company(x.findtext("corp_code", ""),
                                 x.findtext("corp_name", ""),
                                 x.findtext("stock_code", "").strip())
                         for x in root.findall("list")]
            if not companies:
                raise DartError("기업 목록이 비어 있습니다.")
            return companies
        except (BadZipFile, KeyError, ET.ParseError, DefusedXmlException):
            raise DartError("기업 목록 ZIP/XML 응답을 읽을 수 없습니다.") from None

    def financials(self, corp_code, year, fs_div):
        response = self._get("fnlttSinglAcntAll.json", corp_code=corp_code,
                             bsns_year=str(year), reprt_code="11011", fs_div=fs_div)
        try:
            data = response.json()
        except ValueError:
            raise DartError("재무제표 응답이 올바른 JSON이 아닙니다.") from None
        if not isinstance(data, dict):
            raise DartError("재무제표 응답 구조가 잘못되었습니다.")
        check_status(data.get("status"))
        rows = data.get("list")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise DartError("정상 응답에 재무제표 목록이 없거나 형식이 잘못되었습니다.")
        for row in rows:
            if row.get("bsns_year") != str(year) or row.get("corp_code") != corp_code or row.get("reprt_code") != "11011":
                raise DartError("응답 기업·사업연도·보고서가 요청과 일치하지 않습니다.")
        return rows


def resolve_company(companies, name, corp_code=None):
    """동명이인 기업은 임의 선택하지 않고 고유번호를 요구한다."""
    name = name.strip()
    if not name:
        raise DartError("기업명을 입력하세요.")
    if corp_code:
        matches = [c for c in companies if c.code == corp_code and c.name == name]
    else:
        matches = [c for c in companies if c.name == name]
    if len(matches) == 1:
        return matches[0]
    candidates = matches or [c for c in companies if name.casefold() in c.name.casefold()]
    hint = "; ".join(f"{c.name} (고유번호 {c.code}, 종목 {c.stock_code or '비상장'})" for c in candidates[:15])
    raise DartError("기업명을 정확히 입력하고 동명 기업은 기업명@8자리고유번호로 지정하세요. 후보: " + (hint or "없음"))
