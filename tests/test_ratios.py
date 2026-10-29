from decimal import Decimal as D
import pytest
from ratio_calculator import calculate, average, divide


def observation(revenue=120, cost=90, inventory=20, net=30):
    return {"eligible": True, "values": {"revenue": D(revenue), "cost": D(cost), "inventory": D(inventory)},
            "credit": {"net": D(net), "gross": D(40), "allowance": D(10), "ecl_expense": D(-2)}}


def test_known_ratios_and_credit_indicators():
    current, previous = observation(), observation(inventory=10, net=10)
    values, reasons = calculate(current, previous)
    assert values["ar_turnover"] == D(6)
    assert values["gross_margin"] == D(".25")
    assert values["inv_turnover"] == D(6)
    assert values["dso"] == D(365) / D(6)
    assert values["dio"] == D(365) / D(6)
    assert values["gross_ar_turnover"] == D(3)
    assert values["allowance_ratio"] == D(".25")
    assert values["ecl_to_sales"] == D(-2) / 120
    assert not any(reasons.values())


@pytest.mark.parametrize("denominator", [None, D(0), D(-1)])
def test_invalid_denominator(denominator):
    assert divide(D(10), denominator) is None


@pytest.mark.parametrize("a,b", [(None, D(2)), (D(2), None), (D(-1), D(10))])
def test_missing_average(a, b):
    assert average(a, b) is None


def test_zero_flow_valid_turnover_but_days_unavailable():
    values, _ = calculate(observation(revenue=0, cost=0), observation())
    assert values["ar_turnover"] == 0
    assert values["inv_turnover"] == 0
    assert values["gross_margin"] is None
    assert values["dso"] is None


def test_negative_margin_is_preserved():
    assert calculate(observation(revenue=100, cost=120), observation())[0]["gross_margin"] == D("-.2")


def test_missing_prior_does_not_replace_with_ending_balance():
    result, _ = calculate(observation(), {})
    assert result["ar_turnover"] is None
    assert result["inv_turnover"] is None
    assert result["gross_margin"] == D(".25")


def test_ineligible_year_all_ratios_missing():
    current = observation()
    current["eligible"] = False
    assert all(v is None for v in calculate(current, observation())[0].values())


def test_missing_credit_notes_only_blocks_credit_ratios(inputs):
    from pipeline import analyze
    result = analyze(inputs[0], {}, "total_trade", D(".30"), D(5))
    assert all(r["ratios"]["ar_turnover"] is None for r in result["records"])
    assert all(r["ratios"]["gross_margin"] is not None for r in result["records"])
    assert all(r["ratios"]["inv_turnover"] is not None for r in result["records"])
