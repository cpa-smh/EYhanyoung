from copy import deepcopy
from decimal import Decimal as D
import json
import pytest
from account_mapper import map_accounts
from credit_risk import assess_receivables, credit_diagnostics, load_credit_notes


def credit_fixture(inputs):
    snapshot, notes = inputs
    rows = snapshot["companies"][0]["years"]["2025"]["full"]
    return map_accounts(rows)["receivables"], deepcopy(notes["00000001", 2025]), [rows[0]["rcept_no"]]


def test_net_not_reduced_twice(inputs):
    mapped, note, receipts = credit_fixture(inputs)
    result = assess_receivables(mapped, note, receipts)
    assert result["net"] == D(450000000)
    assert result["gross"] == D(600000000)
    assert result["reconciliation"] == 0


def test_no_note_no_unsupported_basis_guess(inputs):
    mapped, _, receipts = credit_fixture(inputs)
    assert assess_receivables(mapped, None, receipts)["net"] is None


@pytest.mark.parametrize("change", ["scope", "receipt", "net", "allowance"])
def test_bad_notes_block_ar(inputs, change):
    mapped, note, receipts = credit_fixture(inputs)
    if change == "scope":
        note["scope"] = "current_trade"
    elif change == "receipt":
        note["rcept_no"] = "20260301009999"
    elif change == "net":
        note["net"] = D(2)
    else:
        note["allowance"] = D(999999999)
    assert assess_receivables(mapped, note, receipts)["net"] is None


def test_trade_only_note_can_supply_missing_separate_account(inputs):
    mapped, note, receipts = credit_fixture(inputs)
    mapped.amount = None
    note["reported_basis"] = "not_available"
    assert assess_receivables(mapped, note, receipts)["net"] == D(450000000)


def test_note_can_confirm_net_without_inventing_allowance(inputs):
    mapped, note, receipts = credit_fixture(inputs)
    note.update(gross=None, allowance=None, net=None)
    result = assess_receivables(mapped, note, receipts)
    assert result["net"] == mapped.amount
    assert result["allowance"] is None


def test_short_term_scope_cannot_be_called_total(inputs):
    mapped, note, receipts = credit_fixture(inputs)
    mapped.account_id = "dart_ShortTermTradeReceivable"
    assert assess_receivables(mapped, note, receipts)["net"] is None


def test_credit_effect_signal_and_rollforward():
    previous = {"credit": {"allowance": D(10)}}
    current = {"credit": {"allowance": D(20), "ecl_expense": D(15), "writeoffs": D(5), "other_movements": D(0)}}
    findings, delta = credit_diagnostics(current, previous, {"allowance_ratio": D(".2"), "ar_turnover": D(6)},
                                        {"allowance_ratio": D(".1"), "ar_turnover": D(5)})
    assert delta == 0
    assert any("동반 상승" in f for f in findings)
    assert any("제각" in f for f in findings)
    current["credit"]["other_movements"] = None
    assert credit_diagnostics(current, previous, {}, {})[1] is None


def test_rollforward_mismatch_not_silently_balanced():
    current = {"credit": {"allowance": D(99), "ecl_expense": D(15), "writeoffs": D(5), "other_movements": D(0)}}
    findings, delta = credit_diagnostics(current, {"credit": {"allowance": D(10)}}, {}, {})
    assert delta == D(-79)
    assert any("불일치" in f for f in findings)


def test_note_loader_rejects_missing_provenance_and_duplicates(tmp_path):
    from sample_data import synthetic_inputs
    _, notes = synthetic_inputs()
    notes["records"][0]["source_ref"] = ""
    path = tmp_path / "notes.json"
    path.write_text(json.dumps(notes), encoding="utf-8")
    with pytest.raises(ValueError):
        load_credit_notes(path)
    _, notes = synthetic_inputs()
    notes["records"].append(notes["records"][0])
    path.write_text(json.dumps(notes), encoding="utf-8")
    with pytest.raises(ValueError):
        load_credit_notes(path)
