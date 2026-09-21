"""
Regression test for the "storage_failed" live-submit bug investigation.

Builds a complete, valid submit payload whose field set is extracted live
from FORM_HTML's <form id="cef-form"> (not hardcoded), so if the page ever
adds/renames/removes a field without this test being updated, the coverage
assertion below fails loudly instead of silently testing a stale payload
shape. On top of that baseline, it constructs the four distinct ways the
Step 1 referring-agent fields can end up populated:

  (a) magic-link ?agent= prefill
  (b) type-ahead selection via the agent_search server action
  (c) "No referring agent" -> the Ops fallback
  (d) manual entry ("My agent is not listed")

and runs each through validate_submission() AND the full handle_submit()
item-construction / put_item path against a mocked DynamoDB table that
performs the same type validation (via boto3's TypeSerializer) real
DynamoDB would, so a float or other non-serializable value reaching the
item is caught here rather than only in production.

Run: python3 test_submit_regression.py
"""

import json
import re

import lambda_function as lf
from boto3.dynamodb.types import TypeSerializer


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        sid = Key["submission_id"]
        return {"Item": self.items[sid]} if sid in self.items else {}

    def put_item(self, Item):
        ser = TypeSerializer()
        for k, v in Item.items():
            try:
                ser.serialize(v)
            except Exception as e:
                raise TypeError(
                    f"field {k!r} value {v!r} is not DynamoDB-serializable: {type(e).__name__}: {e}"
                )
        self.items[Item["submission_id"]] = Item

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ReturnValues=None):
        sid = Key["submission_id"]
        current = self.items.get(sid, {}).get("submission_count", 0)
        current += ExpressionAttributeValues[":incr"]
        self.items.setdefault(sid, {"submission_id": sid})["submission_count"] = current
        return {"Attributes": {"submission_count": current}}


class FakeSES:
    def send_raw_email(self, **kwargs):
        return {"MessageId": "fake-message-id"}


AGENT_FIELDS = {"agent_first_name", "agent_last_name", "agent_email"}

# Known-good value for every non-agent field the live #cef-form submits.
# Deliberately does NOT use `.get(field, "")` fallbacks when building the
# payload below -- an unrecognized field must fail the coverage assertion
# loudly rather than silently submitting an incomplete/guessed payload.
FIELD_VALUES = {
    "website": "",
    "confirm_read": True,
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
    "occupation": lf.OCCUPATION_OPTIONS[0],
    "occupation_other": "",
    "employer_name": "Acme Corp",
    "employer_country": "United States",
    "employer_city": "Springfield",
    "employer_state": "Illinois",
    "employer_zip": "62701",
    "retiring_five_years": "No",
    "net_worth": lf.NET_WORTH_OPTIONS[0],
    "cumulative_investments": lf.NET_WORTH_OPTIONS[1],
    "annual_income": lf.ANNUAL_INCOME_OPTIONS[0],
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


def extract_live_form_field_names():
    m = re.search(r'<form id="cef-form".*?</form>', lf.FORM_HTML, re.DOTALL)
    assert m, "could not find <form id=\"cef-form\"> in FORM_HTML -- page structure changed"
    return set(re.findall(r'''name=["']([^"']+)["']''', m.group(0)))


def test_field_coverage_matches_live_page():
    """Fails loudly if FORM_HTML's field set drifts from what this test
    (and, by extension, our understanding of what handle_submit must
    accept) knows about."""
    live_fields = extract_live_form_field_names()
    known_fields = set(FIELD_VALUES) | AGENT_FIELDS
    missing = live_fields - known_fields
    extra = known_fields - live_fields
    assert not missing, (
        f"FORM_HTML now submits field(s) this regression test doesn't know how to "
        f"fill: {sorted(missing)}. Add them to FIELD_VALUES (or AGENT_FIELDS)."
    )
    assert not extra, (
        f"This regression test still fills field(s) no longer present in "
        f"FORM_HTML: {sorted(extra)}. Remove them."
    )
    print(f"field coverage: OK ({len(live_fields)} live fields, all accounted for)")


def build_payload(agent_fields):
    payload = dict(FIELD_VALUES)
    payload["action"] = "submit"
    payload.update(agent_fields)
    return payload


def agent_search_selection(display_name):
    """Exactly what handle_agent_search() returns for a query matching
    this roster entry -- used to build path (b)'s payload the same way
    the real client does: qs("#agent_first_name").value = selection.first,
    etc., from a server-search match object. Searches by first name, the
    same way a client actually types (the type-ahead matches on a name
    PREFIX, not the full "First Last" string)."""
    first_name = lf.split_agent_display_name(display_name)[0]
    resp = lf.handle_agent_search({"q": first_name}, "9.8.7.6")
    matches = json.loads(resp["body"])["matches"]
    match = next(m for m in matches if m["name"] == display_name)
    return {
        "agent_first_name": match["first"],
        "agent_last_name": match["last"],
        "agent_email": match["email"],
    }


def run_submit(label, agent_fields):
    lf.table = FakeTable()
    lf.ses = FakeSES()
    payload = build_payload(agent_fields)

    errors, data = lf.validate_submission(payload)
    assert not errors, f"[{label}] validate_submission unexpectedly failed: {errors}"

    resp = lf.handle_submit(payload, "1.2.3.4", "forms.example.com")
    assert resp["statusCode"] == 200, f"[{label}] handle_submit returned {resp['statusCode']}: {resp['body']}"
    body = json.loads(resp["body"])
    assert body["ok"] is True, f"[{label}] response not ok: {body}"
    assert body["submission_id"], f"[{label}] missing submission_id in response"
    stored = lf.table.items[body["submission_id"]]
    assert stored["agent_first_name"] == agent_fields["agent_first_name"]
    assert stored["agent_last_name"] == agent_fields["agent_last_name"]
    assert stored["agent_email"] == agent_fields["agent_email"]
    print(f"[{label}] OK -> submission_id={body['submission_id']}")


def test_path_a_magic_link():
    # ?agent=testbroker&agent_name=Test+Broker locks these exact values,
    # readonly, before Step 1 can even be advanced past.
    run_submit("a) magic-link ?agent= prefill", {
        "agent_first_name": "Test",
        "agent_last_name": "Broker",
        "agent_email": "testbroker@rainmakersecurities.com",
    })


def test_path_b_agent_search_selection():
    run_submit("b) agent_search selection", agent_search_selection("Ben Martin"))


def test_path_c_no_referring_agent():
    run_submit("c) No referring agent (Ops fallback)", {
        "agent_first_name": lf.OPS_FALLBACK_FIRST,
        "agent_last_name": lf.OPS_FALLBACK_LAST,
        "agent_email": lf.OPS_FALLBACK_EMAIL,
    })


def test_path_d_manual_entry():
    run_submit("d) manual entry", {
        "agent_first_name": "Taylor",
        "agent_last_name": "Reed",
        "agent_email": "taylor.reed@example.com",
    })


def test_every_roster_agent_via_search():
    # Broader sweep: every real roster entry, resolved the same way the
    # browser resolves a search selection, must submit cleanly.
    for display_name, _email in lf.AGENT_ROSTER:
        run_submit(f"roster: {display_name}", agent_search_selection(display_name))


if __name__ == "__main__":
    test_field_coverage_matches_live_page()
    test_path_a_magic_link()
    test_path_b_agent_search_selection()
    test_path_c_no_referring_agent()
    test_path_d_manual_entry()
    test_every_roster_agent_via_search()
    print("\nALL SUBMIT REGRESSION TESTS PASSED")
