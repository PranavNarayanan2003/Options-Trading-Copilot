from scripts.export_research_data import _effectiveness_summary


def test_effectiveness_summary_separates_approved_real_and_veto_counterfactual():
    outcomes = [
        {"pnl_pct": .20, "payload": {"alert": {"ai_review": {"verdict": "APPROVE"}}}},
        {"pnl_pct": -.10, "payload": {"alert": {"ai_review": {"verdict": "APPROVE"}}}},
        {"pnl_pct": .50, "payload": {"alert": {"ai_review": {"verdict": "SKIPPED"}}}},
    ]
    counter = [
        {"payload": {"terminal": True, "current_pnl_pct": -.15}},
        {"payload": {"terminal": True, "current_pnl_pct": .05}},
        {"payload": {"terminal": False, "current_pnl_pct": .10}},
    ]
    rows = _effectiveness_summary(outcomes, counter)
    approved, vetoed = rows
    assert approved["sample_size"] == 2 and approved["wins"] == 1
    assert vetoed["sample_size"] == 2 and vetoed["wins"] == 1
    assert approved["evidence_status"] == "PRELIMINARY"
