# rms-forms

Standalone client-onboarding forms system for Rainmaker Securities. Completely
separate from every other project in this account. Everything lives in one
file: `lambda_function.py` — stdlib + boto3 only, no external dependencies.

## AWS resources (hardcoded constants in lambda_function.py — do not rename)

- Lambda function: `rms-forms`, us-east-1, Python 3.12, function URL enabled
  (auth NONE). Deployed via `.github/workflows/deploy.yml` on push to `main`
  (`aws lambda update-function-code --function-name rms-forms`).
- DynamoDB table: `rms-forms`, partition key `submission_id` (String). Also
  used to store rate-limit counters under keys `rl#{ip}#{YYYYMMDDHH}` — these
  have no `form_type` attribute so they're naturally excluded from admin scans.
- S3 bucket: `rms-forms-uploads-271378210266` (private, us-east-1). Uploads
  are keyed `{form_type}/{YYYY}/{MM}/{uuid}.{ext}`.
- SES (production mode) sender identity: `agent@agent.graciagroup.com`.
  Reply-To on notification emails: `cgracia@rainmakersecurities.com`.
- `ADMIN_KEY` env var is set directly on the Lambda — read via
  `os.environ["ADMIN_KEY"]`, never hardcoded.

## Routing (single function URL, one handler)

- `GET /` → serves the current form's HTML page.
- `POST /` `{"action":"presign", "filename":..., "content_type":...}` →
  validates extension (jpg/jpeg/png/pdf/heic) and content-type match, returns
  a 15-minute presigned S3 PUT URL + the object key. 15MB max is enforced
  client-side only.
- `POST /` `{"action":"submit", ...fields}` → full server-side validation,
  writes to DynamoDB, sends notification email, returns
  `{"ok":true,"submission_id":...}`.
- `GET /?view=admin&key=XXX` → admin list (403 plain text "Forbidden" if key
  doesn't match `ADMIN_KEY`). `GET /?view=admin&key=XXX&id=SUBMISSION_ID` →
  admin detail view with an inline/download link for the uploaded ID via a
  15-minute presigned S3 GET URL.

## form_type convention

Each form on this Lambda gets its own `form_type` value stored on every
DynamoDB item, so the shared table/admin scan can filter per form:

- `cef-natural` — Client Engagement Form for Natural Persons (built first).
- `iqf-natural` — planned (IQF).
- `cef-entity` — planned (Client Engagement Form for entity/legal persons).

When adding a new form, give it its own `form_type`, its own S3 key prefix,
and add its tab to the shared header/tab bar (see below) instead of touching
the existing forms' logic.

## Design system (shared across all forms/admin pages in this Lambda)

- Colors: navy `#1b2a4a` (headings, body text, header bar background), gold
  `#b9975b` (accent, active tab underline, button border/hover fill), white
  background.
- Buttons: white bg, 1px gold border, gold uppercase letter-spaced text;
  hover inverts to gold bg / white text.
- Headings: Georgia serif stack. Body: system sans-serif. Max content width
  ~760px, centered.
- Page header: navy bar, "RAINMAKER SECURITIES" + "Admin Hub" subtitle, white
  text. Beneath it, a tab bar: one active tab per current page/form, other
  tabs grayed out with a "coming soon" tooltip (not links).
- No links anywhere except the two rainmakersecurities.com URLs referenced in
  the CEF-Natural instructions/privacy text, and normal admin nav within the
  app itself.

## Anti-abuse rules (non-negotiable, apply to every form added here)

- **Honeypot**: hidden text input named `website`. Non-empty on submit →
  return a fake `{"ok":true}` with a random submission_id; store nothing,
  email nothing.
- **Rate limit**: max 5 submissions per IP per hour, enforced via DynamoDB
  `update_item` ADD counters on `rl#{ip}#{YYYYMMDDHH}` keys. Over limit → HTTP
  429.
- **Email whitelist**: notification emails only ever go to the address the
  submitter designated as the referring/relevant agent, and only if that
  address ends with `@rainmakersecurities.com` (case-insensitive). Any other
  domain: store the submission, send no email. At most one email per
  submission, ever.
- **No PII in logs**: never log form field values, request bodies, or
  uploaded content. Log only route, HTTP status, and submission_id.
