import json
from decimal import Decimal
import pytest
from credit_risk import load_credit_notes
from pipeline import analyze
from sample_data import synthetic_inputs


@pytest.fixture
def inputs(tmp_path):
    snapshot, notes = synthetic_inputs()
    path = tmp_path / "notes.json"
    path.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    return snapshot, load_credit_notes(path)


@pytest.fixture
def analysis(inputs):
    snapshot, notes = inputs
    return analyze(snapshot, notes, "total_trade", Decimal("0.30"), Decimal(5))
