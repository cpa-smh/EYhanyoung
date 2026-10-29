from io import BytesIO
from unittest.mock import Mock
from zipfile import ZipFile
import pytest
import requests
from dart_client import DartClient, DartError, NoDataError, Company, resolve_company
from pipeline import collect

KEY = "a" * 40  # 가짜 테스트 키


def client(response=None):
    session = Mock()
    session.get.return_value = response
    return DartClient(KEY, session=session, sleep=Mock())


def response(data=None, content=b"", status=200):
    return Mock(status_code=status, content=content, json=Mock(return_value=data))


def test_corporation_zip_and_exact_resolution():
    data = BytesIO()
    with ZipFile(data, "w") as z:
        z.writestr("CORPCODE.xml", "<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list></result>")
    companies = client(response(content=data.getvalue())).companies()
    assert resolve_company(companies, "삼성전자").code == "00126380"
    with pytest.raises(DartError):
        resolve_company(companies, "삼성")


def test_ambiguous_name_requires_code():
    companies = [Company("00000001", "동명"), Company("00000002", "동명")]
    with pytest.raises(DartError):
        resolve_company(companies, "동명")
    assert resolve_company(companies, "동명", "00000002").code == "00000002"


@pytest.mark.parametrize("status", ["010", "011", "012", "020", "800", "900", "901"])
def test_api_errors_and_key_redaction(status):
    api = client(response({"status": status, "message": KEY}))
    with pytest.raises(DartError) as error:
        api.profile("00000001")
    assert KEY not in str(error.value)
    assert api.session.get.call_count == 1


def test_timeout_retry_and_safe_error():
    api = client()
    api.session.get.side_effect = requests.Timeout(KEY)
    with pytest.raises(DartError) as error:
        api.profile("00000001")
    assert KEY not in str(error.value)
    assert api.session.get.call_count == 3


def test_http_retry_recovery():
    api = client()
    api.session.get.side_effect = [response(status=503), response({"status": "000", "acc_mt": "12"})]
    assert api.profile("00000001")["acc_mt"] == "12"
    assert api.session.get.call_count == 2


def test_bad_json_and_xml():
    bad = response()
    bad.json.side_effect = ValueError()
    with pytest.raises(DartError):
        client(bad).profile("00000001")
    with pytest.raises(DartError):
        client(response(content=b"broken")).companies()


def test_period_endpoint_checks_identity():
    row = {"bsns_year": "2025", "reprt_code": "11011", "stock_code": "005930"}
    api = client(response({"status": "000", "list": [row]}))
    assert api.period_accounts("00126380", 2025, "005930") == [row]
    with pytest.raises(DartError):
        api.period_accounts("00126380", 2025, "000000")


def test_collect_no_cfs_does_not_fetch_ofs():
    api = Mock()
    api.profile.return_value = {"corp_cls": "Y", "acc_mt": "12", "induty_code": "26110"}
    api.financials.side_effect = NoDataError()
    result = collect(api, [Company("00000001", "테스트", "900001")], 2023, 2025, progress=lambda _: None)
    assert len(result["companies"][0]["years"]) == 4
    assert all(call.args[2] == "CFS" for call in api.financials.call_args_list)
    assert not api.period_accounts.called


def test_limit_error_not_converted_to_missing():
    api = Mock()
    api.profile.return_value = {"corp_cls": "Y", "acc_mt": "12", "induty_code": "26110"}
    api.financials.side_effect = DartError("API 요청 한도 초과")
    with pytest.raises(DartError):
        collect(api, [Company("00000001", "테스트")], 2023, 2025, progress=lambda _: None)
