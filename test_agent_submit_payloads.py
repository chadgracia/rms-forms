"""
Local test for the Step 1 "submit failure" fix.

Builds a submit payload exactly as the current Step 1 JS would send it —
once for the roster type-ahead selection path, once for the "My agent is
not listed" manual-entry path — and runs each through
validate_submission() to prove both are accepted with no errors and no
exceptions.

Run: python3 test_agent_submit_payloads.py
"""

import lambda_function as lf


def build_step2_fields():
    return {
        "client_first_name": "Jane",
        "client_last_name": "Doe",
        "address_country": "United States",
        "address_street": "123 Main St",
        "address_street2": "",
        "address_city": "Chicago",
        "address_state": "Illinois",
        "address_zip": "60614",
        "client_phone": "312-555-0100",
        "client_email": "jane@example.com",
        "tax_id": "123-45-6789",
        "date_of_birth": "1985-05-05",
        "associated_person": "not_associated",
        "crd_number": "",
        "missing_info_notes": "",
    }


def build_step3_fields():
    return {
        "occupation": "Engineer",
        "occupation_other": "",
        "employer_name": "Acme Corp",
        "employer_country": "United States",
        "employer_city": "Chicago",
        "employer_state": "Illinois",
        "employer_zip": "60614",
        "retiring_five_years": "No",
        "net_worth": "$1,000,000 - $4,999,999",
        "cumulative_investments": "$5,000,000 - $9,999,999",
        "annual_income": "over $100,000 per year",
        "investment_objectives": ["growth"],
        "other_objective": "",
        "previous_investment_types": ["Mutual Funds"],
        "years_experience": "10",
        "client_sophistication": [],
        "sophistication_other": "",
        "q_private_equity_five_years": "Yes",
        "q_illiquid_investments": "No",
        "q_risk_tolerance": "Yes",
        "q_independent_judgement": "Yes",
        "attestation": "agent",
    }


def build_payload(agent_fields):
    # Every field the JS collectFormData() sweep would include from
    # #cef-form, in the exact keys it uses.
    payload = {"action": "submit", "website": ""}
    payload["confirm_read"] = True
    payload.update(agent_fields)
    payload.update(build_step2_fields())
    payload.update(build_step3_fields())
    return payload


def test_roster_selection_path():
    # User typed into #agent_search, clicked "Ben Martin" in the
    # suggestion list -> selectAgent("Ben Martin") fills the hidden
    # agent_first_name/agent_last_name/agent_email inputs from
    # AGENT_ROSTER_MAP, exactly as sent by collectFormData().
    resolved = lf.AGENT_ROSTER_JS_MAP
    import json
    roster_map = json.loads(resolved)
    entry = roster_map["Ben Martin"]
    payload = build_payload({
        "agent_first_name": entry["first"],
        "agent_last_name": entry["last"],
        "agent_email": entry["email"],
    })
    errors, data = lf.validate_submission(payload)
    assert not errors, f"roster selection path failed validation: {errors}"
    assert data["agent_first_name"] == "Ben"
    assert data["agent_last_name"] == "Martin"
    assert data["agent_email"] == "bmartin@rainmakersecurities.com"
    print("roster selection path: OK")


def test_manual_entry_path():
    # User clicked "My agent is not listed" in the suggestion list, then
    # typed into the revealed agent_first_name/agent_last_name/agent_email
    # inputs.
    payload = build_payload({
        "agent_first_name": "Taylor",
        "agent_last_name": "Reed",
        "agent_email": "taylor.reed@example.com",
    })
    errors, data = lf.validate_submission(payload)
    assert not errors, f"manual entry path failed validation: {errors}"
    assert data["agent_first_name"] == "Taylor"
    assert data["agent_last_name"] == "Reed"
    assert data["agent_email"] == "taylor.reed@example.com"
    print("manual entry path: OK")


def test_no_referring_agent_path():
    # User clicked "No referring agent" in the suggestion list ->
    # selectAgent("__none__") fills the Ops fallback.
    payload = build_payload({
        "agent_first_name": lf.OPS_FALLBACK_FIRST,
        "agent_last_name": lf.OPS_FALLBACK_LAST,
        "agent_email": lf.OPS_FALLBACK_EMAIL,
    })
    errors, data = lf.validate_submission(payload)
    assert not errors, f"no-referring-agent path failed validation: {errors}"
    assert data["agent_email"] == "ops@rainmakersecurities.com"
    print("no referring agent (Ops fallback) path: OK")


if __name__ == "__main__":
    test_roster_selection_path()
    test_manual_entry_path()
    test_no_referring_agent_path()
    print("\nALL AGENT SUBMIT PAYLOAD TESTS PASSED")
