"""Read-only audit: counts identity-document data held in AWS. Prints COUNTS and
S3 object keys only - never contents, names, filenames or field values (this log is public)."""
import email, boto3
from email import policy
s3 = boto3.client("s3", region_name="us-east-1")

def objects(bucket):
    out = []
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            out += page.get("Contents", [])
    except Exception as e:
        print(f"{bucket}: cannot list ({type(e).__name__}: {getattr(e, 'response', {}).get('Error', {}).get('Code', '')})")
        return None
    return out

print("== rms-forms-uploads-271378210266")
objs = objects("rms-forms-uploads-271378210266")
if objs is not None:
    by = {}
    for o in objs:
        p = o["Key"].split("/")[0] if "/" in o["Key"] else "(root)"
        by.setdefault(p, [0, 0]); by[p][0] += 1; by[p][1] += o["Size"]
    print(f"total objects: {len(objs)}")
    for p, (n, sz) in sorted(by.items()):
        print(f"  prefix {p}: {n} objects, {sz/1e6:.1f} MB")

print("== gracia-agent-inbox")
objs = objects("gracia-agent-inbox")
if objs is not None:
    print(f"total raw emails: {len(objs)}; oldest {min((o['LastModified'] for o in objs), default='-')}")
    flagged = []
    for o in objs:
        try:
            raw = s3.get_object(Bucket="gracia-agent-inbox", Key=o["Key"])["Body"].read()
            msg = email.message_from_bytes(raw, policy=policy.default)
            kinds = [p.get_content_type() for p in msg.iter_attachments()]
            imgs = [k for k in kinds if k.startswith("image/") or k == "application/pdf"]
            if imgs:
                flagged.append((o["Key"], len(imgs)))
        except Exception:
            pass
    print(f"emails with image/PDF attachments: {len(flagged)}")
    for k, n in flagged:
        print(f"  {k}  ({n} attachment(s))")

print("== DynamoDB table rms-forms")
try:
    t = boto3.resource("dynamodb", region_name="us-east-1").Table("rms-forms")
    items, kw = [], {}
    while True:
        r = t.scan(**kw); items += r["Items"]
        if "LastEvaluatedKey" not in r: break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    def has(i, f): return bool(str(i.get(f, "")).strip())
    print(f"items: {len(items)}")
    for f in ["tax_id", "date_of_birth", "id_upload_s3_key", "pdf_rms_s3_key", "client_email"]:
        print(f"  items with {f}: {sum(has(i, f) for i in items)}")
except Exception as e:
    print(f"cannot scan ({type(e).__name__}: {getattr(e, 'response', {}).get('Error', {}).get('Code', '')})")
