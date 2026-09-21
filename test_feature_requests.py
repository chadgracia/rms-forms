"""
Regression test for the feature-request punch-list box (Glen notes view +
admin view): create/toggle/list actions, honeypot, rate limiting, the
admin-key-or-notes=glen authorization split on toggle, and the guard that
keeps a toggle from writing done/done_at onto a non-feature-request item
(e.g. a real CEF submission) even when authorized.

Run: python3 test_feature_requests.py
"""

import json
import os

os.environ.setdefault("ADMIN_KEY", "test-admin-key")

import lambda_function as lf

# This file's create test verifies the feature-request notification email,
# so force the kill switch on regardless of the EMAILS_ENABLED env var the
# test process happens to run under.
lf.EMAILS_ENABLED = True


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
        if UpdateExpression.startswith("ADD"):
            current = item.get("submission_count", 0) + ExpressionAttributeValues[":incr"]
            item["submission_count"] = current
            return {"Attributes": {"submission_count": current}}
        # SET #done = :done[, #done_at = :done_at] [REMOVE #done_at]
        item["done"] = ExpressionAttributeValues[":done"]
        if ":done_at" in (ExpressionAttributeValues or {}):
            item["done_at"] = ExpressionAttributeValues[":done_at"]
        elif "REMOVE #done_at" in UpdateExpression:
            item.pop("done_at", None)
        return {"Attributes": {}}

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

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "fake"}

    def send_raw_email(self, **kwargs):
        return {"MessageId": "fake"}


def test_create_honeypot():
    lf.table = FakeTable()
    lf.ses = FakeSES()
    resp = lf.handle_feature_request_create({"website": "spam", "text": "hi"}, "1.1.1.1", "forms.example.com")
    assert json.loads(resp["body"])["ok"] is True
    assert len(lf.table.items) == 0
    print("honeypot -> ok:true, nothing stored: OK")


def test_create_empty_text_rejected_politely():
    lf.table = FakeTable()
    lf.ses = FakeSES()
    resp = lf.handle_feature_request_create({"text": "   "}, "1.1.1.1", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 400 and body["ok"] is False and "message" in body
    print("empty text -> 400 with a friendly message: OK")


def test_create_valid_request():
    lf.table = FakeTable()
    lf.ses = FakeSES()
    resp = lf.handle_feature_request_create({"text": "Add dark mode", "page": "Step 2"}, "2.2.2.2", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["ok"] is True and body["id"].startswith("fr#")
    stored = lf.table.items[body["id"]]
    assert stored["form_type"] == "feature-request"
    assert stored["text"] == "Add dark mode"
    assert stored["page"] == "Step 2"
    assert stored["done"] is False
    assert "created_at" in stored
    assert len(lf.ses.sent) == 1
    sent = lf.ses.sent[0]
    assert sent["Destination"]["ToAddresses"] == ["cgracia@rainmakersecurities.com"]
    assert sent["Message"]["Subject"]["Data"] == "RMS Forms feature request"
    assert "Add dark mode" in sent["Message"]["Body"]["Text"]["Data"]
    print("valid create -> stored correctly, notification emailed to cgracia@rainmakersecurities.com: OK")


def test_create_text_truncated_not_rejected():
    lf.table = FakeTable()
    lf.ses = FakeSES()
    resp = lf.handle_feature_request_create({"text": "x" * 3000}, "3.3.3.3", "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert len(lf.table.items[body["id"]]["text"]) == lf.FEATURE_REQUEST_MAX_LEN
    print(f"text over {lf.FEATURE_REQUEST_MAX_LEN} chars is truncated, not rejected: OK")


def test_create_shares_submission_rate_limit():
    lf.table = FakeTable()
    lf.ses = FakeSES()
    statuses = [lf.handle_feature_request_create({"text": "spam" + str(i)}, "4.4.4.4", "forms.example.com")["statusCode"]
                for i in range(25)]
    assert 429 in statuses, "expected the shared submission rate limit to eventually kick in"
    assert any(k.startswith("rl#4.4.4.4#") for k in lf.table.items), "should use the shared rl# key, not a separate counter"
    print("create shares the submission rate limit (no separate fr# budget): OK")


def test_toggle_requires_admin_key_or_notes_glen():
    lf.table = FakeTable()
    lf.table.items["fr#abc"] = {
        "submission_id": "fr#abc", "form_type": "feature-request", "text": "x",
        "done": False, "created_at": "2026-01-01T00:00:00Z",
    }
    resp = lf.handle_feature_request_toggle({"id": "fr#abc", "done": True}, "forms.example.com")
    assert resp["statusCode"] == 403 and resp["body"] == "Forbidden"
    print("toggle without admin key or notes=glen -> 403: OK")

    resp = lf.handle_feature_request_toggle({"id": "fr#abc", "done": True, "key": "test-admin-key"}, "forms.example.com")
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["ok"] is True
    assert lf.table.items["fr#abc"]["done"] is True
    assert "done_at" in lf.table.items["fr#abc"]
    print("toggle with admin key -> done=True, done_at set: OK")

    resp = lf.handle_feature_request_toggle({"id": "fr#abc", "done": False, "notes": "glen"}, "forms.example.com")
    assert resp["statusCode"] == 200
    assert lf.table.items["fr#abc"]["done"] is False
    assert "done_at" not in lf.table.items["fr#abc"]
    print("toggle undo with notes=glen -> done=False, done_at removed: OK")

    resp = lf.handle_feature_request_toggle({"id": "fr#abc", "done": True, "key": "wrong-key"}, "forms.example.com")
    assert resp["statusCode"] == 403
    print("toggle with wrong admin key -> 403: OK")


def test_toggle_cannot_pollute_non_feature_request_items():
    lf.table = FakeTable()
    lf.table.items["real-submission-uuid"] = {
        "submission_id": "real-submission-uuid", "form_type": "cef-natural", "status": "complete",
    }
    resp = lf.handle_feature_request_toggle(
        {"id": "real-submission-uuid", "done": True, "notes": "glen"}, "forms.example.com"
    )
    assert resp["statusCode"] == 404
    assert "done" not in lf.table.items["real-submission-uuid"], "toggle must never write onto a non-feature-request item"
    print("toggle refuses to write done/done_at onto a non-feature-request item: OK")

    resp = lf.handle_feature_request_toggle({"id": "fr#doesnotexist", "done": True, "notes": "glen"}, "forms.example.com")
    assert resp["statusCode"] == 404
    print("toggle on an unknown id -> 404: OK")


def test_list_returns_open_and_done_skips_other_form_types():
    lf.table = FakeTable()
    lf.table.items["fr#1"] = {
        "submission_id": "fr#1", "form_type": "feature-request", "text": "one",
        "page": "Step 1", "created_at": "2026-01-01T00:00:00Z", "done": False,
    }
    lf.table.items["fr#2"] = {
        "submission_id": "fr#2", "form_type": "feature-request", "text": "two",
        "page": "Step 2", "created_at": "2026-01-02T00:00:00Z", "done": True, "done_at": "2026-01-03T00:00:00Z",
    }
    lf.table.items["cef-1"] = {"submission_id": "cef-1", "form_type": "cef-natural", "status": "complete"}

    resp = lf.handle_feature_request_list({})
    body = json.loads(resp["body"])
    ids = sorted(r["id"] for r in body["requests"])
    assert ids == ["fr#1", "fr#2"], f"expected only the 2 feature-request items, got {ids}"
    by_id = {r["id"]: r for r in body["requests"]}
    assert by_id["fr#1"]["text"] == "one" and by_id["fr#1"]["done"] is False
    assert by_id["fr#2"]["done"] is True
    print("list returns exactly the feature-request items (open + done), skips other form_types: OK")


def test_router_wiring():
    lf.table = FakeTable()
    lf.ses = FakeSES()

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({"action": "feature_request", "text": "router test"}),
        "headers": {"host": "forms.example.com"},
    }
    r = lf.lambda_handler(event, None)
    assert r["statusCode"] == 200 and json.loads(r["body"])["ok"] is True
    print("lambda_handler routes action=feature_request (no id) to create: OK")

    event2 = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({"action": "feature_request", "id": "fr#doesnotexist", "done": True, "notes": "glen"}),
        "headers": {"host": "forms.example.com"},
    }
    r2 = lf.lambda_handler(event2, None)
    assert r2["statusCode"] == 404
    print("lambda_handler routes action=feature_request (has id) to toggle: OK")

    event3 = {
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps({"action": "feature_request_list"}),
        "headers": {"host": "forms.example.com"},
    }
    r3 = lf.lambda_handler(event3, None)
    assert r3["statusCode"] == 200
    print("lambda_handler routes action=feature_request_list: OK")


if __name__ == "__main__":
    test_create_honeypot()
    test_create_empty_text_rejected_politely()
    test_create_valid_request()
    test_create_text_truncated_not_rejected()
    test_create_shares_submission_rate_limit()
    test_toggle_requires_admin_key_or_notes_glen()
    test_toggle_cannot_pollute_non_feature_request_items()
    test_list_returns_open_and_done_skips_other_form_types()
    test_router_wiring()
    print("\nALL FEATURE REQUEST TESTS PASSED")
