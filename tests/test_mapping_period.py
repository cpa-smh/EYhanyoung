from copy import deepcopy
from decimal import Decimal as D
import pytest
from account_mapper import amount, map_accounts, accounting_checks
from period_validator import validate_period, profile_reasons


@pytest.mark.parametrize("raw,expected", [("1,234", D(1234)), ("(50)", D(-50)), ("0", D(0)), ("-", None), ("NaN", None), ("Infinity", None), (None, None)])
def test_amount(raw, expected):
    assert amount(raw) == expected


def test_combined_receivables_never_used():
    rows = [{"sj_div": "BS", "account_id": "ifrs-full_TradeAndOtherCurrentReceivables", "account_nm": "매출채권및기타채권", "currency": "KRW", "thstrm_amount": "100"}]
    assert map_accounts(rows)["receivables"].amount is None


def test_no_duplicate_sum_and_conflict_is_missing(inputs):
    rows = deepcopy(inputs[0]["companies"][0]["years"]["2025"]["full"])
    mapped = map_accounts(rows)
    rows.append(dict(rows[0]))
    assert map_accounts(rows)["revenue"].amount == mapped["revenue"].amount
    rows[-1]["thstrm_amount"] = "1"
    assert map_accounts(rows)["revenue"].status == "REVIEW"


def test_id_over_alias_and_is_over_cis(inputs):
    rows = deepcopy(inputs[0]["companies"][0]["years"]["2025"]["full"])
    rows.extend([{**rows[0], "sj_div": "CIS", "thstrm_amount": "1"},
                 {**rows[0], "account_id": "custom", "thstrm_amount": "2"}])
    assert map_accounts(rows)["revenue"].amount == D(1200000000)


def test_cf_excluded_and_negative_cost_not_flipped(inputs):
    rows = deepcopy(inputs[0]["companies"][0]["years"]["2025"]["full"])
    rows[1]["thstrm_amount"] = "-100"
    rows.append({**rows[0], "sj_div": "CF", "thstrm_amount": "1"})
    assert map_accounts(rows)["cost"].amount is None
    assert map_accounts(rows)["revenue"].amount == D(1200000000)


def test_accounting_equations(inputs):
    rows = deepcopy(inputs[0]["companies"][0]["years"]["2025"]["full"])
    assert all(c["status"] == "PASS" for c in accounting_checks(map_accounts(rows)))
    rows[0]["thstrm_amount"] = "1"
    assert accounting_checks(map_accounts(rows))[1]["status"] == "FAIL"


def test_calendar_period_verified(inputs):
    p = inputs[0]["companies"][0]["years"]["2025"]
    assert validate_period(p["full"], p["period"], 2025)["valid"]


@pytest.mark.parametrize("change", ["short_period", "ofs", "different_receipt", "missing", "currency"])
def test_bad_period_is_excluded(inputs, change):
    p = deepcopy(inputs[0]["companies"][0]["years"]["2025"])
    if change == "short_period":
        p["period"][1]["thstrm_dt"] = "2025.07.01 ~ 2025.12.31"
    elif change == "ofs":
        p["period"][0]["fs_div"] = "OFS"
    elif change == "different_receipt":
        p["period"][0]["rcept_no"] = "20260301000999"
    elif change == "missing":
        p["period"] = []
    else:
        p["full"][0]["currency"] = "USD"
    assert not validate_period(p["full"], p["period"], 2025)["valid"]


@pytest.mark.parametrize("field,value", [("corp_cls", "E"), ("acc_mt", "03"), ("induty_code", "64110"), ("induty_code", "")])
def test_ineligible_profile(field, value):
    profile = {"corp_cls": "Y", "acc_mt": "12", "induty_code": "26110", field: value}
    assert profile_reasons(profile)
