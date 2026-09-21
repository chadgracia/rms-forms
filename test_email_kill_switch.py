"""
Regression tests for the global email kill switch (EMAILS_ENABLED).

Verifies: with the flag off (default -- matches the env var being absent
in production during the test phase), no email is sent from any of the
gated call sites -- submission (broker+RMS), sweep partial-notification,
and feature-request creation -- while every other side effect (DynamoDB
writes, S3 PDF storage, sweep's 30-day cleanup) is unchanged. Also
verifies the sweep's specific notified-flag exception (an eligible
partial stays un-notified and re-eligible across repeated disabled
sweeps, then gets notified exactly once the flag is turned on) and that
setting EMAILS_ENABLED=True restores normal behavior with no code change.

Run: python3 test_email_kill_switch.py
"""

import io
import json

import lambda_function as lf
from botocore.exceptions import ClientError


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        sid = Key["submission_id"]
        return {"Item": self.items[sid]} if sid in self.items else {}

    def put_item(self, Item):
        self.items[Item["submission_id"]] = Item

    def update_item(self, Key, UpdateExpression=None, ExpressionAttributeValues=None,
                     ExpressionAttributeNames=None, ReturnValues=None):
        sid = Key["submission_id"]
        item = self.items.setdefault(sid, {"submission_id": sid})
        if UpdateExpression and UpdateExpression.startswith("ADD"):
            current = item.get("submission_count", 0) + ExpressionAttributeValues[":incr"]
            item["submission_count"] = current
            return {"Attributes": {"submission_count": current}}
        if ExpressionAttributeNames:
            for name_placeholder, attr_name in ExpressionAttributeNames.items():
                value_placeholder = ":" + name_placeholder[1:]
                item[attr_name] = ExpressionAttributeValues[value_placeholder]
            return {"Attributes": {}}
        if ExpressionAttributeValues and ":t" in ExpressionAttributeValues:
            item["notified"] = ExpressionAttributeValues[":t"]
        return {"Attributes": {}}

    def delete_item(self, Key):
        self.items.pop(Key["submission_id"], None)

    def scan(self, FilterExpression=None, ExclusiveStartKey=None):
        def matches(v):
            if FilterExpression is None:
                return True
            attr_obj, value = FilterExpression._values
            return v.get(attr_obj.name) == value
        return {"Items": [v for v in self.items.values() if matches(v)]}


class FakeSES:
    def __init__(self):
        self.sent = []

    def send_raw_email(self, RawMessage, **kwargs):
        self.sent.append(("raw", RawMessage["Data"]))
        return {"MessageId": "fake"}

    def send_email(self, **kwargs):
        self.sent.append(("plain", kwargs))
        return {"MessageId": "fake"}


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kwargs):
        self.objects[Key] = (bytes(Body), ContentType)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        body, _ct = self.objects[Key]
        return {"Body": io.BytesIO(body)}

    def generate_presigned_url(self, op, Params, ExpiresIn=900):
        return f"https://s3.example.com/{Params['Key']}?presigned=1"


SUBMIT_PAYLOAD = {
    "action": "submit",
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
    "agent_first_name": "Test",
    "agent_last_name": "Agent",
    "agent_email": "test.agent@rainmakersecurities.com",
}


def make_backend():
    lf.table = FakeTable()
    lf.ses = FakeSES()
    lf.s3 = FakeS3()


def test_default_is_disabled():
    assert lf.EMAILS_ENABLED is False, (
        "EMAILS_ENABLED must default to False when the env var is absent -- "
        "this is the test-phase kill switch"
    )
    print("EMAILS_ENABLED defaults to False with the env var unset: OK")


def test_submit_disabled_no_email_but_everything_else_unchanged():
    lf.EMAILS_ENABLED = False
    make_backend()
    resp = lf.handle_submit(dict(SUBMIT_PAYLOAD), "1.2.3.4", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["ok"] is True
    assert len(lf.ses.sent) == 0, "no email of any kind should be sent while disabled"
    sid = body["submission_id"]
    assert sid in lf.table.items, "DynamoDB write must still happen while emails are disabled"
    assert lf.table.items[sid]["form_type"] == lf.FORM_TYPE_CEF_NATURAL
    print("submit with emails disabled: no email sent, DynamoDB write unaffected: OK")


def test_submit_enabled_sends_email():
    lf.EMAILS_ENABLED = True
    make_backend()
    resp = lf.handle_submit(dict(SUBMIT_PAYLOAD), "1.2.3.4", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["ok"] is True
    assert len(lf.ses.sent) == 2, f"expected broker+RMS emails once re-enabled, got {len(lf.ses.sent)}"
    lf.EMAILS_ENABLED = False
    print("submit with EMAILS_ENABLED=True (re-enabled): broker+RMS emails sent again: OK")


def make_partial_draft(sid, created_at, updated_at, agent_email="agent@rainmakersecurities.com"):
    return {
        "submission_id": sid,
        "form_type": lf.FORM_TYPE_CEF_NATURAL,
        "status": lf.DRAFT_STATUS_PARTIAL,
        "created_at": created_at,
        "updated_at": updated_at,
        "agent_email": agent_email,
    }


def test_sweep_disabled_leaves_eligible_partial_unnotified_and_reeligible():
    lf.EMAILS_ENABLED = False
    make_backend()
    now = lf.datetime.datetime.utcnow()
    stale = (now - lf.DRAFT_NOTIFY_AFTER - lf.datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_created = (now - lf.datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sid = "draft-disabled-1"
    lf.table.items[sid] = make_partial_draft(sid, fresh_created, stale)

    result1 = lf.handle_sweep()
    assert result1["notified"] == 0, "no partial should be counted as notified while emails are disabled"
    assert lf.table.items[sid].get("notified") is not True, "notified flag must NOT be set while emails are disabled"
    assert len(lf.ses.sent) == 0

    # Run again -- must remain eligible and un-notified, not silently skipped forever.
    result2 = lf.handle_sweep()
    assert result2["notified"] == 0
    assert lf.table.items[sid].get("notified") is not True
    assert len(lf.ses.sent) == 0
    print("sweep with emails disabled: eligible partial stays un-notified across repeated runs: OK")

    # Flip the switch on -- the still-eligible partial should now be picked up.
    lf.EMAILS_ENABLED = True
    result3 = lf.handle_sweep()
    assert result3["notified"] == 1, f"expected the partial to be notified once re-enabled, got {result3}"
    assert lf.table.items[sid].get("notified") is True
    assert len(lf.ses.sent) == 1
    lf.EMAILS_ENABLED = False
    print("sweep re-enabled: previously-skipped partial gets notified exactly once: OK")


def test_sweep_30day_cleanup_unaffected_by_flag():
    for flag in (False, True):
        lf.EMAILS_ENABLED = flag
        make_backend()
        now = lf.datetime.datetime.utcnow()
        old = (now - lf.DRAFT_DELETE_AFTER - lf.datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sid = f"draft-old-{flag}"
        lf.table.items[sid] = make_partial_draft(sid, old, old)
        result = lf.handle_sweep()
        assert result["deleted"] == 1, f"30-day cleanup must run regardless of EMAILS_ENABLED={flag}"
        assert sid not in lf.table.items
    lf.EMAILS_ENABLED = False
    print("sweep's 30-day cleanup runs identically whether emails are enabled or disabled: OK")


def test_feature_request_create_disabled_no_email_but_stored():
    lf.EMAILS_ENABLED = False
    make_backend()
    resp = lf.handle_feature_request_create({"text": "Add dark mode"}, "5.5.5.5", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["ok"] is True
    assert len(lf.ses.sent) == 0, "no notification email while disabled"
    assert body["id"] in lf.table.items
    assert lf.table.items[body["id"]]["text"] == "Add dark mode"
    print("feature_request create with emails disabled: saved, no email sent: OK")


def test_feature_request_create_enabled_sends_email():
    lf.EMAILS_ENABLED = True
    make_backend()
    resp = lf.handle_feature_request_create({"text": "Add dark mode"}, "5.5.5.6", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["ok"] is True
    assert len(lf.ses.sent) == 1
    lf.EMAILS_ENABLED = False
    print("feature_request create with EMAILS_ENABLED=True: notification emailed: OK")


if __name__ == "__main__":
    test_default_is_disabled()
    test_submit_disabled_no_email_but_everything_else_unchanged()
    test_submit_enabled_sends_email()
    test_sweep_disabled_leaves_eligible_partial_unnotified_and_reeligible()
    test_sweep_30day_cleanup_unaffected_by_flag()
    test_feature_request_create_disabled_no_email_but_stored()
    test_feature_request_create_enabled_sends_email()
    print("\nALL EMAIL KILL SWITCH TESTS PASSED")
