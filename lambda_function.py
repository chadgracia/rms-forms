import base64
import datetime
import html
import json
import os
import re
import uuid
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import parse_qs, quote

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

REGION = "us-east-1"
TABLE_NAME = "rms-forms"
BUCKET_NAME = "rms-forms-uploads-271378210266"
SES_SENDER = "RMS Forms <agent@agent.graciagroup.com>"
SES_REPLY_TO = "cgracia@rainmakersecurities.com"
AGENT_EMAIL_DOMAIN = "@rainmakersecurities.com"
RMS_TEAM_EMAIL = "ops@rainmakersecurities.com"
# Global email kill switch (test phase): absent/anything-but-"true" means NO
# email of any kind is sent -- every ses.send_* call site checks this first.
# Set EMAILS_ENABLED=true in the Lambda's environment variables to turn
# emails back on; no code change needed.
EMAILS_ENABLED = os.environ.get("EMAILS_ENABLED", "").lower() == "true"
# Set on the Lambda alongside ADMIN_KEY so sweep emails (which have no HTTP
# request to derive a host from) can still build a clickable admin link,
# e.g. "xxxxxxxx.lambda-url.us-east-1.on.aws". Falls back gracefully if unset.
FORM_HOST_ENV_VAR = "FORM_HOST"
FORM_TYPE_CEF_NATURAL = "cef-natural"
FORM_TYPE_FEATURE_REQUEST = "feature-request"
FEATURE_REQUEST_MAX_LEN = 2000
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
PDF_MAX_BYTES = 500 * 1024
RATE_LIMIT_PER_HOUR = 20
AGENT_SEARCH_RATE_LIMIT_PER_HOUR = 60
AGENT_SEARCH_MIN_QUERY_LEN = 3
AGENT_SEARCH_MAX_RESULTS = 5
DRAFT_STATUS_PARTIAL = "partial"
SUBMISSION_STATUS_COMPLETE = "complete"
DRAFT_MAX_BYTES = 50 * 1024
DRAFT_NOTIFY_AFTER = datetime.timedelta(hours=2)
DRAFT_NOTIFY_WITHIN = datetime.timedelta(days=7)
DRAFT_DELETE_AFTER = datetime.timedelta(days=30)

ALLOWED_UPLOAD_EXTENSIONS = {
    "jpg": ("image/jpeg", "image/jpg"),
    "jpeg": ("image/jpeg", "image/jpg"),
    "png": ("image/png",),
    "pdf": ("application/pdf",),
    "heic": ("image/heic", "image/heif"),
}

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
s3 = boto3.client("s3", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
ID_UPLOAD_STATUS_DEFERRED = "deferred-rms-secure-transfer"

US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
    "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
)

NET_WORTH_OPTIONS = (
    "$1,000,000 - $4,999,999",
    "$5,000,000 - $9,999,999",
    "$10,000,000 - $24,999,999",
    "$25,000,000 - $49,999,999",
    "$50,000,000 - $99,999,999",
    "$100,000,000 or more",
    "NONE OF THE ABOVE",
)

ANNUAL_INCOME_OPTIONS = (
    "over $100,000 per year",
    "over $250,000 per year",
    "over $350,000 per year",
    "over $1,000,000 per year",
)

OCCUPATION_OPTIONS = (
    "Accountant", "Attorney", "Banker", "Business Owner", "C-Level Executive",
    "Consultant", "Dentist", "Engineer", "Entrepreneur", "Farmer / Rancher",
    "Financial Advisor", "Government Employee", "Healthcare Professional",
    "Homemaker", "Insurance Professional", "Investor", "IT Professional",
    "Marketing / Sales", "Military", "Nurse", "Pharmacist", "Physician",
    "Pilot", "Professor / Teacher", "Real Estate Professional", "Retired",
    "Scientist", "Software Developer", "Student", "Trades / Construction",
    "Venture Capital / Private Equity", "Not Employed", "Other",
)

ELIGIBILITY_WARNING_TEXT = "May not be eligible for private secondary transactions."

# Referring Agent roster shown on Step 1 when no ?agent= URL param is present.
# Source of truth for the dropdown and for resolving/validating a selection.
AGENT_ROSTER = (
    ("Ben Martin", "bmartin@rainmakersecurities.com"),
    ("Brandon Hopen", "bhopen@rainmakersecurities.com"),
    ("Brendan Breen", "bbreen@rainmakersecurities.com"),
    ("Cang Quach", "cvq@sternventures.com"),
    ("Chad Gracia", "cgracia@rainmakersecurities.com"),
    ("Christian Lagerling", "christian@belucaventures.com"),
    ("Connor Hallisey", "ch@acceleratesportsbusiness.com"),
    ("Daniel Roche", "daniel@kellscapital.com"),
    ("David Bernstein", "dbernstein@rainmakersecurities.com"),
    ("David Knorowski", "dknorowski@rainmakersecurities.com"),
    ("David Reinikainen", "dreinikainen@rainmakersecurities.com"),
    ("Dominic Cioffi", "dcioffi@rainmakersecurities.com"),
    ("Elena Kareva", "ekareva@rainmakersecurities.com"),
    ("Gary Selz", "gselz@outsetpartners.com"),
    ("Glen Anderson", "ganderson@rainmakersecurities.com"),
    ("Greg Smith", "gsmith@rainmakersecurities.com"),
    ("Greg Capello", "gcapello@premieralts.com"),
    ("Greg Martin", "gmartin@rainmakersecurities.com"),
    ("Ian Subel", "isubel@marulacap.com"),
    ("Jack Richardson", "jack@hamiagroup.com"),
    ("Jack Stafford", "js@acceleratesportsbusiness.com"),
    ("Jordon Durst", "jdurst@rainmakersecurities.com"),
    ("Jyoti Soni", "jsoni@rainmakersecurities.com"),
    ("Ken Anderson", "kanderson@rainmakersecurities.com"),
    ("Kirat S. Lall", "kslall@rainmakersecurities.com"),
    ("Kris Gilboy", "kgilboy@outsetpartners.com"),
    ("Laurence Hayward", "lh@lab-137.com"),
    ("Marco Sutic", "msutic@rainmakersecurities.com"),
    ("Marianna Prueger", "mprueger@rainmakersecurities.com"),
    ("Mariano Grosman", "mgrosman@rainmakersecurities.com"),
    ("Mariano Grosman (APAC)", "mgrosman@rainmakerapac.com"),
    ("Mark Caccavo", "mcaccavo@bomboraadvisory.com"),
    ("Mark Sullivan", "msullivan@rainmakersecurities.com"),
    ("Marko Kozul", "marko@catalytic-life.com"),
    ("Michael Beardsley", "beards@internetsec.com"),
    ("Michael Grenier", "michael@ballardcap.com"),
    ("Olga Klimov", "oklimov@rainmakersecurities.com"),
    ("Peter Striebel", "pstriebel@outsetpartners.com"),
    ("Phillip Cooper", "pcooper@rainmakersecurities.com"),
    ("Ronald Chamorro", "rchamorro@rainmakersecurities.com"),
    ("Saeid Hamedanchi", "shamedanchi@rainmakersecurities.com"),
    ("Sean Lavin", "sean@alphalavin.com"),
    ("Sequoia Taylor", "staylor@spry.vc"),
    ("Simon Hanna", "shanna@rainmakersecurities.com"),
    ("Tim Barnes", "tbarnes@axisgroupventures.com"),
    ("Tom Bonfield", "tom@impacts.capital"),
    ("Wayne Platt", "wplatt@marulacap.com"),
    ("Yoram Arbel", "yoram@pacificoakscapital.com"),
    ("Zach Sease", "zsease@rainmakersecurities.com"),
)

OPS_FALLBACK_FIRST = "None"
OPS_FALLBACK_LAST = "Ops"
OPS_FALLBACK_EMAIL = "ops@rainmakersecurities.com"


def split_agent_display_name(display_name):
    name = display_name[: -len(" (APAC)")] if display_name.endswith(" (APAC)") else display_name
    parts = name.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


AGENT_ROSTER_TUPLES = {
    split_agent_display_name(display_name) + (email,) for display_name, email in AGENT_ROSTER
}

INVESTMENT_OBJECTIVE_OPTIONS = (
    "generate income",
    "liquidate assets",
    "preserve capital",
    "growth",
    "accumulate assets for heirs",
    "Other",
)

PREVIOUS_INVESTMENT_OPTIONS = (
    "Corporate Bonds",
    "Municipal Bonds",
    "Hedge Funds",
    "Public Equity",
    "Private Equity",
    "Options Trading",
    "Annuities",
    "Futures/Commodities",
    "Mutual Funds",
    "REITs",
    "Early stage venture capital",
    "Late-stage, Pre-IPO equity",
)

SOPHISTICATION_OPTIONS = (
    "Client has founded or managed a private company",
    "Client has experience evaluating company financial statements",
    "Client has the business acumen to evaluate the merits and risks of an investment",
    "Client has owned a controlling interest in a private company",
    "Client possesses a professional license (attorney, CPA, FINRA, etc.)",
    "Client consults with a professional adviser for financial decisions",
    "Client is a member of a group or club that emphasizes private investments",
    "Other",
)

YES_NO = ("Yes", "No")

COUNTRIES = (
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
    "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan",
    "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria",
    "Burkina Faso", "Burundi", "Cabo Verde", "Cambodia", "Cameroon", "Canada",
    "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros",
    "Congo (Congo-Brazzaville)", "Costa Rica", "Croatia", "Cuba", "Cyprus",
    "Czechia (Czech Republic)", "Democratic Republic of the Congo", "Denmark",
    "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador",
    "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia", "Fiji",
    "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece",
    "Grenada", "Guatemala", "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras",
    "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel",
    "Italy", "Ivory Coast", "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya",
    "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon",
    "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg",
    "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
    "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia",
    "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique",
    "Myanmar (Burma)", "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand",
    "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia", "Norway",
    "Oman", "Pakistan", "Palau", "Palestine State", "Panama", "Papua New Guinea",
    "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania",
    "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino",
    "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles",
    "Sierra Leone", "Singapore", "Slovakia", "Slovenia", "Solomon Islands",
    "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka",
    "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan",
    "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago",
    "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine",
    "United Arab Emirates", "United Kingdom", "United States", "Uruguay",
    "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen",
    "Zambia", "Zimbabwe",
)

FIELD_LABELS = {
    "submission_id": "Submission ID",
    "form_type": "Form Type",
    "status": "Status",
    "last_step": "Last Step Reached",
    "created_at": "Submitted (UTC)",
    "updated_at": "Last Updated (UTC)",
    "ip": "IP Address",
    "confirm_read": "Confirmed Read Disclosures",
    "agent_first_name": "Referring Agent First Name",
    "agent_last_name": "Referring Agent Last Name",
    "agent_email": "Referring Agent Email",
    "client_first_name": "Client First Name",
    "client_last_name": "Client Last Name",
    "address_street": "Street Address",
    "address_street2": "Street Address Line 2",
    "address_city": "City",
    "address_state": "State/Province",
    "address_zip": "Postal/Zip",
    "address_country": "Country",
    "client_phone": "Client Phone",
    "client_email": "Client Email",
    "tax_id": "Tax ID / Government ID",
    "date_of_birth": "Date of Birth",
    "associated_person": "Associated Person Status",
    "crd_number": "CRD#",
    "missing_info_notes": "Missing Information Notes",
    "occupation": "Occupation",
    "occupation_other": "Other Occupation",
    "employer_name": "Employer Name",
    "employer_city": "Employer City",
    "employer_state": "Employer State/Province",
    "employer_zip": "Employer Postal/Zip",
    "employer_country": "Employer Country",
    "retiring_five_years": "Planning to Retire Within 5 Years",
    "net_worth": "Net Worth Excluding Primary Residence",
    "cumulative_investments": "Cumulative Amount of Investments",
    "annual_income": "Annual Income",
    "investment_objectives": "Investment Objectives",
    "other_objective": "Other Investment Objective",
    "previous_investment_types": "Previous Investment Types",
    "years_experience": "Years of Investment Experience",
    "client_sophistication": "Client Sophistication",
    "sophistication_other": "Other Sophistication Factor",
    "q_private_equity_five_years": "Invested in Private Equity/Debt (Past 5 Years)",
    "q_illiquid_investments": "Can Invest with Little/No Liquidity Need",
    "q_risk_tolerance": "Has Risk Tolerance for Private Securities",
    "q_independent_judgement": "Capable of Independent Investment Judgement",
    "attestation": "Attestation",
}

DETAIL_FIELD_ORDER = [
    "submission_id", "form_type", "status", "last_step", "created_at", "updated_at", "ip",
    "agent_first_name", "agent_last_name", "agent_email", "confirm_read",
    "client_first_name", "client_last_name",
    "address_street", "address_street2", "address_city", "address_state",
    "address_zip", "address_country",
    "client_phone", "client_email", "tax_id", "date_of_birth",
    "associated_person", "crd_number", "missing_info_notes",
    "occupation", "occupation_other", "employer_name", "employer_country",
    "employer_city", "employer_state", "employer_zip",
    "retiring_five_years", "net_worth", "cumulative_investments",
    "annual_income", "investment_objectives", "other_objective",
    "previous_investment_types", "years_experience",
    "client_sophistication", "sophistication_other",
    "q_private_equity_five_years", "q_illiquid_investments", "q_risk_tolerance",
    "q_independent_judgement", "attestation",
]


# ---------------------------------------------------------------------------
# HTML builders (shared header/CSS, form page, admin pages)
# ---------------------------------------------------------------------------

SHARED_CSS = """
:root { --navy: #1b2a4a; --gold: #b9975b; --white: #ffffff; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  color: var(--navy);
  background: #ffffff;
}
h1, h2, h3 { font-family: Georgia, 'Times New Roman', serif; color: var(--navy); }
a { color: var(--gold); }
.site-header { background: var(--navy); color: #ffffff; padding: 22px 20px; }
.site-header-inner { max-width: 760px; margin: 0 auto; }
.site-title { font-family: Georgia, 'Times New Roman', serif; font-size: 26px; letter-spacing: 1.5px; }
.site-subtitle { font-size: 13px; color: #d8c9a3; margin-top: 4px; letter-spacing: 0.5px; }
.tab-bar { background: #f6f4ef; border-bottom: 1px solid #e3ded0; }
.tab-bar-inner { max-width: 760px; margin: 0 auto; display: flex; overflow-x: auto; }
.tab {
  padding: 14px 16px; font-size: 12px; letter-spacing: 0.8px; text-transform: uppercase;
  color: #a9a49a; white-space: nowrap; border-bottom: 3px solid transparent;
}
.tab.active { color: var(--navy); border-bottom-color: var(--gold); font-weight: 700; }
.tab.disabled { color: #c7c2b6; cursor: default; }
.container { max-width: 760px; margin: 0 auto; padding: 32px 20px 90px; }
p, li, label, .field-error, td, th, .detail-value { font-size: 15px; line-height: 1.5; }
.btn, button {
  background: #ffffff; border: 1px solid var(--gold); color: var(--gold);
  text-transform: uppercase; letter-spacing: 1.2px; padding: 12px 26px;
  font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.15s ease;
  border-radius: 2px;
}
.btn:hover, button:hover { background: var(--gold); color: #ffffff; }
.btn:disabled, button:disabled { opacity: 0.5; cursor: default; }
input[type=text], input[type=email], input[type=tel], input[type=date],
input[type=number], input[type=password], select, textarea {
  width: 100%; padding: 10px 12px; border: 1px solid #ccc6b8; font-size: 15px;
  font-family: inherit; border-radius: 2px; background: #fff; color: var(--navy);
}
input.locked { background: #f3f1ea; color: #6b6a63; }
textarea { min-height: 90px; resize: vertical; }
label.field-label { display: block; margin: 18px 0 6px; font-weight: 700; font-size: 13px; letter-spacing: 0.2px; }
.helper-text { font-size: 12px; color: #7a7563; margin-top: 4px; }
.label-hint { font-weight: 400; color: #7a7563; font-size: 11.5px; margin-left: 6px; }
.field { margin-bottom: 4px; }
.field.invalid input, .field.invalid select, .field.invalid textarea { border-color: #b23b3b; }
.field-error { color: #b23b3b; font-size: 12px; margin-top: 5px; display: none; }
.field.invalid .field-error { display: block; }
.two-col { display: flex; gap: 16px; flex-wrap: wrap; }
.two-col > .field { flex: 1; min-width: 220px; }
.radio-option, .checkbox-option {
  display: flex; align-items: flex-start; gap: 8px; font-weight: 400; margin: 8px 0; font-size: 14px;
}
.radio-option input, .checkbox-option input { width: auto; margin-top: 3px; }
.radio-option-inline { display: inline-flex; align-items: center; gap: 6px; margin-right: 24px; font-weight: 400; }
.radio-option-inline input { width: auto; }
.lock-icon { margin-left: 6px; font-size: 13px; }
.progress-wrap { margin: 24px 0 8px; }
.progress-track { background: #eee9dd; height: 4px; border-radius: 2px; overflow: hidden; }
.progress-bar { background: var(--gold); height: 100%; width: 33.33%; transition: width 0.2s ease; }
.step-label { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #7a7563; margin-top: 8px; }
.instructions-list { padding-left: 20px; }
.instructions-list li { margin-bottom: 10px; }
details.patriot-section { border: 1px solid #e3ded0; border-radius: 3px; padding: 12px 16px; margin: 20px 0; background: #faf9f5; }
details.patriot-section summary { font-weight: 700; cursor: pointer; color: var(--navy); }
details.patriot-section p { margin: 12px 0; }
.honeypot-field { position: absolute; left: -9999px; top: -9999px; opacity: 0; height: 0; width: 0; }
.id-upload-notice {
  display: flex; align-items: center; gap: 10px; background: #faf6ee;
  border: 1px solid var(--gold); color: var(--navy); border-radius: 4px;
  padding: 14px 16px; margin-top: 14px; font-size: 14px; transition: box-shadow 0.15s ease;
}
.id-upload-notice-icon { font-size: 17px; flex-shrink: 0; }
.id-upload-notice.highlight { box-shadow: 0 0 0 3px rgba(185, 151, 91, 0.35); }
.eligibility-warning {
  border: 1px solid #c0392b; background: #fdecea; color: #c0392b; font-weight: 700;
  padding: 10px 14px; border-radius: 4px; margin-top: 8px; font-size: 13px;
}
.zip-mismatch {
  border: 1px solid #c0392b; background: #fdecea; color: #c0392b; font-weight: 700;
  padding: 10px 14px; border-radius: 4px; margin-top: 8px; font-size: 13px;
}
.zip-autofill-notice {
  border: 1px solid var(--gold); background: #faf6ee; color: var(--navy); font-weight: 600;
  padding: 8px 12px; border-radius: 4px; margin-top: 8px; font-size: 12.5px;
}
@keyframes zipAutofillFlash {
  0% { background-color: #f2dfb8; }
  100% { background-color: transparent; }
}
.zip-flash { animation: zipAutofillFlash 1.5s ease-out; }
.not-eligible-tag { color: #c0392b; font-weight: 700; font-size: 11px; margin-left: 6px; white-space: nowrap; }
.partial-tag {
  color: #8a5a00; background: #fdf3e0; border: 1px solid #c77d00; font-weight: 700;
  font-size: 11px; margin-left: 6px; padding: 1px 6px; border-radius: 3px; white-space: nowrap;
}
#employer-address-block.address-block-disabled { opacity: 0.45; pointer-events: none; }
.agent-typeahead { position: relative; }
.agent-suggestions {
  list-style: none; margin: 4px 0 0; padding: 4px 0; position: absolute; left: 0; right: 0;
  background: #fff; border: 1px solid #ccc6b8; border-radius: 2px; max-height: 220px;
  overflow-y: auto; z-index: 20; box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08);
}
.agent-suggestions li { padding: 8px 12px; font-size: 14px; cursor: pointer; }
.agent-suggestions li:hover, .agent-suggestions li.active { background: #faf6ee; }
.agent-suggestions li.agent-suggestion-special { border-top: 1px solid #e3ded0; color: #7a7563; font-style: italic; }
.agent-email-display { max-width: 300px; background: #f3f1ea; color: #6b6a63; }
.nav-buttons { display: flex; justify-content: space-between; margin-top: 32px; }
.step { display: none; }
.step.active { display: block; }
.success-screen { text-align: center; padding: 60px 20px; }
.success-check { font-size: 56px; color: var(--gold); line-height: 1; margin-bottom: 16px; }
.admin-table { width: 100%; border-collapse: collapse; margin-top: 24px; }
.admin-table th, .admin-table td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #e6e2d8; font-size: 14px; }
.admin-table th { text-transform: uppercase; letter-spacing: 0.5px; font-size: 11px; color: #7a7a7a; }
.detail-link { color: var(--gold); text-decoration: none; font-weight: 700; }
.detail-link:hover { text-decoration: underline; }
.detail-grid { margin-top: 16px; border-top: 1px solid #e6e2d8; }
.detail-row { display: flex; flex-wrap: wrap; border-bottom: 1px solid #eee; padding: 10px 0; }
.detail-label { width: 300px; font-weight: 700; color: #555; }
.detail-value { flex: 1; min-width: 200px; word-break: break-word; }
.id-section { margin: 20px 0; }
.id-preview { max-width: 100%; border: 1px solid #ddd; border-radius: 4px; }
@media (max-width: 600px) {
  .two-col { flex-direction: column; }
  .two-col > .field { min-width: 100%; }
  .detail-label { width: 100%; }
  .nav-buttons .btn { padding: 12px 16px; }
}
"""

SHARED_HEADER = """
<div class="site-header">
  <div class="site-header-inner">
    <div class="site-title">RAINMAKER SECURITIES</div>
    <div class="site-subtitle">Admin Hub</div>
  </div>
</div>
<div class="tab-bar">
  <div class="tab-bar-inner">
    <span class="tab active">Client Engagement Form</span>
    <span class="tab disabled" title="Coming soon">IQF</span>
    <span class="tab disabled" title="Coming soon">Request Agreements</span>
    <span class="tab disabled" title="Coming soon">Invoice</span>
  </div>
</div>
"""


def build_select_options_html(options, placeholder):
    parts = [f"<option value=''>{html.escape(placeholder)}</option>"]
    for opt in options:
        esc = html.escape(opt)
        parts.append(f"<option value='{esc}'>{esc}</option>")
    return "".join(parts)


def build_checkbox_options_html(name, options):
    parts = []
    for i, opt in enumerate(options):
        input_id = f"{name}_{i}"
        esc = html.escape(opt)
        parts.append(
            f"<label class='checkbox-option' for='{input_id}'>"
            f"<input type='checkbox' id='{input_id}' name='{name}' value='{esc}'> {esc}</label>"
        )
    return "".join(parts)


def build_yesno_radios(name, default=None):
    parts = []
    for val in YES_NO:
        input_id = f"{name}_{val.lower()}"
        checked = " checked" if val == default else ""
        parts.append(
            f"<label class='radio-option-inline' for='{input_id}'>"
            f"<input type='radio' id='{input_id}' name='{name}' value='{val}'{checked}> {val}</label>"
        )
    return "".join(parts)


def build_country_options_html(default="United States"):
    parts = []
    for c in COUNTRIES:
        esc = html.escape(c)
        selected = " selected" if c == default else ""
        parts.append(f"<option value='{esc}'{selected}>{esc}</option>")
    return "".join(parts)


def build_agent_roster_js_map():
    entries = {}
    for display_name, email in AGENT_ROSTER:
        first, last = split_agent_display_name(display_name)
        entries[display_name] = {"first": first, "last": last, "email": email}
    entries["__none__"] = {"first": OPS_FALLBACK_FIRST, "last": OPS_FALLBACK_LAST, "email": OPS_FALLBACK_EMAIL}
    return json.dumps(entries)


def build_ops_fallback_js():
    return json.dumps({"first": OPS_FALLBACK_FIRST, "last": OPS_FALLBACK_LAST, "email": OPS_FALLBACK_EMAIL})


NET_WORTH_SELECT_HTML = build_select_options_html(NET_WORTH_OPTIONS, "Select…")
CUMULATIVE_SELECT_HTML = build_select_options_html(NET_WORTH_OPTIONS, "Select…")
ANNUAL_INCOME_SELECT_HTML = build_select_options_html(ANNUAL_INCOME_OPTIONS, "Select…")
OCCUPATION_OPTIONS_HTML = build_select_options_html(OCCUPATION_OPTIONS, "Select occupation…")
INVESTMENT_OBJECTIVES_CHECKS_HTML = build_checkbox_options_html("investment_objectives", INVESTMENT_OBJECTIVE_OPTIONS)
PREVIOUS_INVESTMENT_CHECKS_HTML = build_checkbox_options_html("previous_investment_types", PREVIOUS_INVESTMENT_OPTIONS)
SOPHISTICATION_CHECKS_HTML = build_checkbox_options_html("client_sophistication", SOPHISTICATION_OPTIONS)
COUNTRY_OPTIONS_HTML = build_country_options_html()
AGENT_ROSTER_JS_MAP = build_agent_roster_js_map()
OPS_FALLBACK_JS = build_ops_fallback_js()
RETIRING_RADIOS_HTML = build_yesno_radios("retiring_five_years")
Q1_RADIOS_HTML = build_yesno_radios("q_private_equity_five_years")
Q2_RADIOS_HTML = build_yesno_radios("q_illiquid_investments")
Q3_RADIOS_HTML = build_yesno_radios("q_risk_tolerance")
Q4_RADIOS_HTML = build_yesno_radios("q_independent_judgement")


FORM_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Client Engagement Form — Rainmaker Securities</title>
<style>
__SHARED_CSS__
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<script>
  window.jsPDF = window.jspdf && window.jspdf.jsPDF;
</script>
</head>
<body>
__HEADER__
<div class="container">

  <div id="form-wrap">
    <h1>Client Engagement Form — Natural Persons</h1>

    <div class="progress-wrap">
      <div class="progress-track"><div class="progress-bar" id="progress-bar"></div></div>
      <div class="step-label" id="step-label">Step 1 of 3</div>
    </div>

    <form id="cef-form" novalidate>
      <input type="text" class="honeypot-field" name="website" id="website" tabindex="-1" autocomplete="off" aria-hidden="true">

      <!-- STEP 1 -->
      <div class="step active" data-step="1">
        <ol class="instructions-list">
          <li>This form may be completed by the Client or by the Associated Person on behalf of the Client.</li>
          <li>This form is for Clients that are natural persons. Clients transacting as legal entities should use the <a href="https://www.rainmakersecurities.com/client-engagement-form-for-entity-persons" target="_blank" rel="noopener">Client Information Form for Legal Entities</a>.</li>
          <li>You will be required to upload a photo-copy of a government issued photo identification to complete this form.</li>
          <li>If you need help, please contact your Rainmaker Securities Agent to complete the form.</li>
        </ol>

        <details class="patriot-section">
          <summary>USA PATRIOT Act and SEC Regulations</summary>
          <p>Rainmaker Securities, LLC is a FINRA registered broker-dealer. Broker-dealers are required to collect certain personal information about all clients in order to comply with the USA Patriot Act to help prevent the financing of terrorism. We are also required by the SEC and FINRA to collect certain personal information to ensure we understand the financial circumstances and investment goals of our clients. We are also required by law to conduct an independent background investigation of every client.</p>
          <p>The information entered in this form is protected in transit by TLS and stored with AES-256 encryption at rest.</p>
          <p><a href="https://www.rainmakersecurities.com/privacy-policy" target="_blank" rel="noopener">Privacy Policy</a></p>
        </details>

        <div class="field" data-field="confirm_read">
          <label class="radio-option" for="confirm_read">
            <input type="checkbox" id="confirm_read" name="confirm_read" data-single="true">
            I confirm that I have read and understand the foregoing
          </label>
          <div class="field-error"></div>
        </div>

        <div id="referring-agent-section">
          <div class="field" data-field="agent_roster" id="agent-roster-wrap" style="display:none;">
            <label class="field-label" for="agent_search">Who is the Rainmaker Securities Agent that referred the Client to this form?</label>
            <div class="agent-typeahead">
              <input type="text" id="agent_search" autocomplete="off" placeholder="Type the first or last name of the agent who referred you here.">
              <ul class="agent-suggestions" id="agent_suggestions" style="display:none;"></ul>
            </div>
            <div class="helper-text">Type at least the first three letters of your agent's first or last name.</div>
            <div class="field" id="agent-email-display-wrap" style="display:none; margin-top:10px;">
              <label class="field-label" for="agent_email_display">Agent email (for your records)</label>
              <input type="text" id="agent_email_display" class="agent-email-display" readonly disabled>
            </div>
            <div class="field-error"></div>
          </div>

          <div class="two-col" id="agent-manual-names">
            <div class="field" data-field="agent_first_name">
              <label class="field-label" for="agent_first_name">Referring Agent First Name</label>
              <input type="text" id="agent_first_name" name="agent_first_name">
              <div class="field-error"></div>
            </div>
            <div class="field" data-field="agent_last_name">
              <label class="field-label" for="agent_last_name">Referring Agent Last Name</label>
              <input type="text" id="agent_last_name" name="agent_last_name">
              <div class="field-error"></div>
            </div>
          </div>

          <div class="field" data-field="agent_email" id="agent-email-wrap">
            <label class="field-label" for="agent_email">Referring Agent Email</label>
            <input type="email" id="agent_email" name="agent_email">
            <span class="lock-icon" id="agent_email_lock" style="display:none;">&#128274;</span>
            <div class="helper-text">If no agent referred you to RMS, enter ops@rainmakersecurities.com</div>
            <div class="field-error"></div>
          </div>
        </div>
      </div>

      <!-- STEP 2 -->
      <div class="step" data-step="2">
        <h2>Client Identification</h2>

        <div class="two-col">
          <div class="field" data-field="client_first_name">
            <label class="field-label" for="client_first_name">Client First Name</label>
            <input type="text" id="client_first_name" name="client_first_name">
            <div class="field-error"></div>
          </div>
          <div class="field" data-field="client_last_name">
            <label class="field-label" for="client_last_name">Client Last Name</label>
            <input type="text" id="client_last_name" name="client_last_name">
            <div class="field-error"></div>
          </div>
        </div>

        <h3>Address of Client</h3>
        <div class="field" data-field="address_country">
          <label class="field-label" for="address_country">Country</label>
          <select id="address_country" name="address_country">__COUNTRY_OPTIONS__</select>
          <div class="field-error"></div>
        </div>
        <div class="field" data-field="address_street">
          <label class="field-label" for="address_street">Street Address</label>
          <input type="text" id="address_street" name="address_street">
          <div class="field-error"></div>
        </div>
        <div class="field" data-field="address_street2">
          <label class="field-label" for="address_street2">Street Address Line 2</label>
          <input type="text" id="address_street2" name="address_street2">
        </div>
        <div class="two-col">
          <div class="field" data-field="address_city">
            <label class="field-label" for="address_city">City</label>
            <input type="text" id="address_city" name="address_city">
            <div class="field-error"></div>
          </div>
          <div class="field" data-field="address_state" id="address_state_wrap">
            <label class="field-label" for="address_state">State/Province</label>
            <input type="text" id="address_state" name="address_state">
            <div class="field-error"></div>
          </div>
        </div>
        <div class="field" data-field="address_zip">
          <label class="field-label" for="address_zip">Postal/Zip Code</label>
          <input type="text" id="address_zip" name="address_zip">
          <div class="helper-text" id="zip-helper" style="display:none;">Format: 12345 or 12345-6789</div>
          <div class="zip-autofill-notice" id="address-zip-notice" style="display:none;"></div>
          <div class="zip-mismatch" id="address-zip-mismatch" style="display:none;"></div>
          <div class="field-error"></div>
        </div>

        <div class="two-col">
          <div class="field" data-field="client_phone">
            <label class="field-label" for="client_phone">Client Phone Number</label>
            <input type="tel" id="client_phone" name="client_phone">
            <div class="field-error"></div>
          </div>
          <div class="field" data-field="client_email">
            <label class="field-label" for="client_email">Client Email</label>
            <input type="email" id="client_email" name="client_email">
            <div class="field-error"></div>
          </div>
        </div>

        <div class="field" data-field="tax_id">
          <label class="field-label" for="tax_id">Tax ID for U.S. Client or Government ID for non-US Client</label>
          <input type="text" id="tax_id" name="tax_id">
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="date_of_birth">
          <label class="field-label" for="date_of_birth">Date of Birth<span class="label-hint">(Must be at least 18)</span></label>
          <input type="date" id="date_of_birth" name="date_of_birth">
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="associated_person">
          <label class="field-label">Is the Client an associated person with a registered broker dealer or RIA?</label>
          <label class="radio-option" for="associated_person_no">
            <input type="radio" id="associated_person_no" name="associated_person" value="not_associated" checked>
            Client is not an associated person
          </label>
          <label class="radio-option" for="associated_person_other">
            <input type="radio" id="associated_person_other" name="associated_person" value="other">
            Other
          </label>
          <div class="field-error"></div>
        </div>
        <div class="field" data-field="crd_number" id="crd-field" style="display:none;">
          <label class="field-label" for="crd_number">CRD#</label>
          <input type="text" id="crd_number" name="crd_number" inputmode="numeric">
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="missing_info_notes">
          <label class="field-label" for="missing_info_notes">If needed, explain any missing information or potential inconsistency here</label>
          <textarea id="missing_info_notes" name="missing_info_notes"></textarea>
        </div>
      </div>

      <!-- STEP 3 -->
      <div class="step" data-step="3">
        <h2>Client Profile &amp; Attestation</h2>
        <p class="helper-text">We are required to ask for the following information. Responses to some of the questions below are not required, but may help us provide better service to the Client.</p>

        <div class="field" data-field="occupation">
          <label class="field-label" for="occupation">Occupation</label>
          <select id="occupation" name="occupation">__OCCUPATION_OPTIONS__</select>
          <div id="occupation-other-field" style="display:none; margin-top:10px;">
            <label class="field-label" for="occupation_other">Other Occupation</label>
            <input type="text" id="occupation_other" name="occupation_other">
            <div class="field-error"></div>
          </div>
        </div>
        <div class="field" data-field="employer_name">
          <label class="field-label" for="employer_name">Name of Employer</label>
          <input type="text" id="employer_name" name="employer_name" placeholder="or type NONE">
        </div>

        <div id="employer-address-block">
          <h3>Address of Employer</h3>
          <div class="field" data-field="employer_country">
            <label class="field-label" for="employer_country">Country</label>
            <select id="employer_country" name="employer_country">__COUNTRY_OPTIONS__</select>
          </div>
          <div class="two-col">
            <div class="field" data-field="employer_city">
              <label class="field-label" for="employer_city">City</label>
              <input type="text" id="employer_city" name="employer_city">
            </div>
            <div class="field" data-field="employer_state" id="employer_state_wrap">
              <label class="field-label" for="employer_state">State/Province</label>
              <input type="text" id="employer_state" name="employer_state">
            </div>
          </div>
          <div class="field" data-field="employer_zip" id="employer_zip_wrap">
            <label class="field-label" for="employer_zip">Postal/Zip Code</label>
            <input type="text" id="employer_zip" name="employer_zip">
            <div class="helper-text" id="employer-zip-helper" style="display:none;">Format: 12345 or 12345-6789</div>
            <div class="zip-autofill-notice" id="employer-zip-notice" style="display:none;"></div>
            <div class="zip-mismatch" id="employer-zip-mismatch" style="display:none;"></div>
            <div class="field-error"></div>
          </div>
        </div>

        <div class="field" data-field="retiring_five_years">
          <label class="field-label">Is the Client planning to permanently retire within the next five years?</label>
          __RETIRING_RADIOS__
        </div>

        <div class="field" data-field="net_worth">
          <label class="field-label" for="net_worth">Net Worth Excluding Primary Residence</label>
          <select id="net_worth" name="net_worth">__NET_WORTH_SELECT__</select>
          <div class="eligibility-warning" id="net_worth-warning" style="display:none;">May not be eligible for private secondary transactions.</div>
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="cumulative_investments">
          <label class="field-label" for="cumulative_investments">Cumulative Amount of Investments</label>
          <select id="cumulative_investments" name="cumulative_investments">__CUMULATIVE_SELECT__</select>
          <div class="eligibility-warning" id="cumulative_investments-warning" style="display:none;">May not be eligible for private secondary transactions.</div>
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="annual_income">
          <label class="field-label" for="annual_income">Annual Income</label>
          <select id="annual_income" name="annual_income">__ANNUAL_INCOME_SELECT__</select>
        </div>

        <div class="field" data-field="investment_objectives">
          <label class="field-label">Investment Objectives</label>
          __INVESTMENT_OBJECTIVES_CHECKS__
          <div id="other-objective-field" style="display:none; margin-top:10px;">
            <label class="field-label" for="other_objective">Other Objective</label>
            <input type="text" id="other_objective" name="other_objective">
            <div class="field-error"></div>
          </div>
        </div>

        <div class="field" data-field="previous_investment_types">
          <label class="field-label">Previous Investment Types</label>
          __PREVIOUS_INVESTMENT_CHECKS__
        </div>

        <div class="field" data-field="years_experience">
          <label class="field-label" for="years_experience">Client's years of experience buying and selling securities</label>
          <input type="number" id="years_experience" name="years_experience" min="0" max="80" style="max-width:90px;">
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="client_sophistication">
          <label class="field-label">Client Sophistication</label>
          __SOPHISTICATION_CHECKS__
          <div id="sophistication-other-field" style="display:none; margin-top:10px;">
            <label class="field-label" for="sophistication_other">Other</label>
            <input type="text" id="sophistication_other" name="sophistication_other">
            <div class="field-error"></div>
          </div>
        </div>

        <div class="field" data-field="q_private_equity_five_years">
          <label class="field-label">Client has invested in private equity/debt securities within the past five years.</label>
          __Q1_RADIOS__
          <div class="field-error"></div>
        </div>
        <div class="field" data-field="q_illiquid_investments">
          <label class="field-label">Client can make investments with little to no need for liquidity in the foreseeable future.</label>
          __Q2_RADIOS__
          <div class="field-error"></div>
        </div>
        <div class="field" data-field="q_risk_tolerance">
          <label class="field-label">Client has the risk tolerance to invest in private securities and understands that certain private securities carry significant risk, including the risk of loss of the entire investment.</label>
          __Q3_RADIOS__
          <div class="field-error"></div>
        </div>
        <div class="field" data-field="q_independent_judgement">
          <label class="field-label">The Client is capable of evaluating investment risks independently; and will exercise independent judgement in evaluating any recommendation or transaction.</label>
          __Q4_RADIOS__
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="attestation">
          <label class="field-label">Attestation</label>
          <label class="radio-option" for="attestation_agent">
            <input type="radio" id="attestation_agent" name="attestation" value="agent">
            I attest that I am the Rainmaker Securities Agent indicated on this form or I have the authority to complete the form on behalf of the Rainmaker Securities Agent. I have asked the Client to provide the information input into this form. I have completed this form truthfully and accurately, to the best of my knowledge.
          </label>
          <label class="radio-option" for="attestation_client">
            <input type="radio" id="attestation_client" name="attestation" value="client">
            I attest that I am the Client indicated on this form or I have the authority to complete the form on behalf of the Client. I have completed this form truthfully and accurately, to the best of my knowledge. I have refused to provide any missing information.
          </label>
          <div class="field-error"></div>
        </div>

        <div class="field">
          <label class="field-label">Final Step: Identity Verification</label>
          <p class="helper-text">Upload a government issued photo ID of the Client. If this cannot be obtained, upload a text narrative that documents the circumstances of the Client's refusal, neglect, or inability to provide the requested document. Note that if we cannot verify a Client's identity in some manner, we cannot legally transact with the Client.</p>
          <button type="button" class="btn" id="upload-id-btn">Upload ID Document</button>
          <div class="id-upload-notice" id="id-upload-notice">
            <span class="id-upload-notice-icon">&#128274;</span>
            <span><strong>Identity verification is required by federal law.</strong> Under the USA PATRIOT Act and U.S. Treasury regulations (31 CFR &sect;1023.220), Rainmaker Securities must verify each client's identity against a government-issued photo ID before any transaction. Your document is encrypted in your browser before it leaves this page and can be opened only by Rainmaker Securities compliance. It is stored with AES-256 encryption in accordance with SEC Regulation S-P (the Safeguards Rule), and it is never sent by email &mdash; not to your referring agent, not to anyone.</span>
          </div>
        </div>
      </div>

      <div class="nav-buttons">
        <button type="button" class="btn" id="back-btn" style="visibility:hidden;">Back</button>
        <button type="button" class="btn" id="next-btn">Next</button>
        <button type="button" class="btn" id="submit-btn" style="display:none;">Submit</button>
      </div>
    </form>
  </div>

  <div id="success-screen" class="success-screen" style="display:none;">
    <div class="success-check">&#10003;</div>
    <h2>Thank you. Your Client Engagement Form has been submitted.</h2>
    <p>Reference ID: <span id="success-id"></span></p>
    <p><button type="button" class="btn" id="download-pdf-btn" style="display:none;">Download PDF Copy</button></p>
  </div>

</div>

<script>
(function () {
  "use strict";

  var OPS_FALLBACK = __OPS_FALLBACK_JSON__;

  var STEP_OF_FIELD = {
    confirm_read: 1, agent_roster: 1, agent_first_name: 1, agent_last_name: 1, agent_email: 1,
    client_first_name: 2, client_last_name: 2,
    address_street: 2, address_city: 2, address_state: 2, address_zip: 2, address_country: 2,
    client_phone: 2, client_email: 2, tax_id: 2, date_of_birth: 2,
    associated_person: 2, crd_number: 2,
    net_worth: 3, cumulative_investments: 3, other_objective: 3, sophistication_other: 3,
    occupation_other: 3, employer_zip: 3, years_experience: 3,
    q_private_equity_five_years: 3, q_illiquid_investments: 3, q_risk_tolerance: 3,
    q_independent_judgement: 3, attestation: 3
  };

  var currentStep = 1;
  var totalSteps = 3;
  var agentSelectionKey = null;
  var draftId = null;
  var zipLookupCache = {};
  var addressMismatchState = { address: false, employer: false };

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function goToStep(n) {
    currentStep = n;
    qsa(".step").forEach(function (el) {
      el.classList.toggle("active", parseInt(el.getAttribute("data-step"), 10) === n);
    });
    qs("#progress-bar").style.width = ((n / totalSteps) * 100) + "%";
    qs("#step-label").textContent = "Step " + n + " of " + totalSteps;
    qs("#back-btn").style.visibility = n === 1 ? "hidden" : "visible";
    qs("#next-btn").style.display = n === totalSteps ? "none" : "inline-block";
    qs("#submit-btn").style.display = n === totalSteps ? "inline-block" : "none";
    window.scrollTo({ top: 0, behavior: "smooth" });
    positionGlenNotes();
  }

  // Glen-note desktop margin layout: collision-free vertical placement.
  // Only ?notes=glen renders any .glen-note elements at all, so this is a
  // no-op on the normal client-facing page. On desktop each note starts at
  // its anchor's own (browser-computed) vertical offset; this pass then
  // walks notes top-to-bottom and pushes any note down that would overlap
  // the one above it, drawing a thin connector line back up to the note's
  // true anchor position when it gets pushed.
  var glenNotesPositionScheduled = false;
  function positionGlenNotes() {
    var notes = qsa(".glen-note");
    if (!notes.length) return;
    if (glenNotesPositionScheduled) return;
    glenNotesPositionScheduled = true;
    requestAnimationFrame(function () {
      glenNotesPositionScheduled = false;
      var formWrap = qs("#form-wrap");
      if (!formWrap) return;

      if (window.innerWidth < 1100) {
        // Mobile/inline layout: notes render in normal document flow.
        // Undo any leftover desktop positioning/connectors from a wider
        // viewport so a later resize back up starts clean.
        notes.forEach(function (n) {
          n.style.top = "";
          var connector = n.querySelector(".glen-note-connector");
          if (connector) { connector.parentNode.removeChild(connector); }
        });
        return;
      }

      var visible = notes.filter(function (n) { return n.offsetParent !== null; });
      if (!visible.length) return;

      // Reset to the browser's own hypothetical (anchor-based) position
      // first, so every pass starts from the true, un-pushed anchor offset
      // instead of compounding on top of a previous pass's pushed value.
      visible.forEach(function (n) {
        n.style.top = "";
        var connector = n.querySelector(".glen-note-connector");
        if (connector) { connector.parentNode.removeChild(connector); }
      });

      var formWrapTop = formWrap.getBoundingClientRect().top;
      var items = visible.map(function (n) {
        return { el: n, anchorTop: n.getBoundingClientRect().top - formWrapTop, height: n.offsetHeight };
      });
      items.sort(function (a, b) { return a.anchorTop - b.anchorTop; });

      var prevBottom = null;
      items.forEach(function (item) {
        var top = item.anchorTop;
        if (prevBottom !== null && top < prevBottom + 14) { top = prevBottom + 14; }
        item.el.style.top = top + "px";
        var pushDistance = top - item.anchorTop;
        if (pushDistance > 0.5) {
          var connector = document.createElement("div");
          connector.className = "glen-note-connector";
          connector.style.top = (-pushDistance) + "px";
          connector.style.height = pushDistance + "px";
          item.el.insertBefore(connector, item.el.firstChild);
        }
        prevBottom = top + item.height;
      });
    });
  }
  window.addEventListener("resize", function () { positionGlenNotes(); });
  if (window.document && document.fonts && document.fonts.ready && typeof document.fonts.ready.then === "function") {
    document.fonts.ready.then(function () { positionGlenNotes(); });
  }

  function fieldWrap(name) {
    return qs('.field[data-field="' + name + '"]');
  }

  function showFieldError(name, message) {
    var wrap = fieldWrap(name);
    if (!wrap) return;
    wrap.classList.add("invalid");
    var err = wrap.querySelector(".field-error");
    if (!err) {
      err = document.createElement("div");
      err.className = "field-error";
      wrap.appendChild(err);
    }
    err.textContent = message;
    err.style.display = "block";
  }

  function clearFieldError(name) {
    var wrap = fieldWrap(name);
    if (!wrap) return;
    wrap.classList.remove("invalid");
    var err = wrap.querySelector(".field-error");
    if (err) { err.style.display = "none"; err.textContent = ""; }
  }

  function clearAllErrors() {
    qsa(".field").forEach(function (wrap) {
      wrap.classList.remove("invalid");
      var err = wrap.querySelector(".field-error");
      if (err) { err.style.display = "none"; err.textContent = ""; }
    });
  }

  function focusAndScroll(name) {
    var wrap = fieldWrap(name);
    if (!wrap) return;
    var stepNum = STEP_OF_FIELD[name];
    if (stepNum && stepNum !== currentStep) { goToStep(stepNum); }
    wrap.scrollIntoView({ behavior: "smooth", block: "center" });
    var focusable = wrap.querySelector("input, select, textarea");
    if (focusable) { focusable.focus(); }
  }

  function isEmailValid(v) { return /^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(v); }

  function isManualAgentFieldsVisible() {
    var el = qs("#agent-manual-names");
    return !el || el.style.display !== "none";
  }

  function validateStep1() {
    var ok = true;
    if (!qs("#confirm_read").checked) { showFieldError("confirm_read", "You must confirm you have read and understand this section."); ok = false; } else { clearFieldError("confirm_read"); }

    var rosterWrap = qs("#agent-roster-wrap");
    var rosterVisible = rosterWrap && rosterWrap.style.display !== "none";
    if (rosterVisible && !isManualAgentFieldsVisible()) {
      if (!agentSelectionKey) { showFieldError("agent_roster", "Select your Rainmaker Securities Agent."); ok = false; }
      else { clearFieldError("agent_roster"); }
    }

    if (!rosterVisible || isManualAgentFieldsVisible()) {
      ["agent_first_name", "agent_last_name"].forEach(function (f) {
        var v = qs("#" + f).value.trim();
        if (!v) { showFieldError(f, "This field is required."); ok = false; } else { clearFieldError(f); }
      });
      var email = qs("#agent_email").value.trim();
      if (!email) { showFieldError("agent_email", "This field is required."); ok = false; }
      else if (!isEmailValid(email)) { showFieldError("agent_email", "Enter a valid email address."); ok = false; }
      else { clearFieldError("agent_email"); }
    }
    return ok;
  }

  function isUSCountry(v) { return v === "United States"; }

  function validateStep2() {
    var ok = true;
    ["client_first_name", "client_last_name", "address_country", "address_street", "address_city", "tax_id"].forEach(function (f) {
      var v = qs("#" + f).value.trim();
      if (!v) { showFieldError(f, "This field is required."); ok = false; } else { clearFieldError(f); }
    });

    var country = qs("#address_country").value.trim();

    var stateVal = qs("#address_state").value.trim();
    if (!stateVal) { showFieldError("address_state", "This field is required."); ok = false; } else { clearFieldError("address_state"); }

    var zip = qs("#address_zip").value.trim();
    if (!zip) { showFieldError("address_zip", "This field is required."); ok = false; }
    else if (isUSCountry(country) && !/^\d{5}(-\d{4})?$/.test(zip)) { showFieldError("address_zip", "Enter a valid US zip code (12345 or 12345-6789)."); ok = false; }
    else { clearFieldError("address_zip"); }

    if (addressMismatchState.address) { fieldWrap("address_zip").classList.add("invalid"); ok = false; }

    var phone = qs("#client_phone").value.trim();
    var digits = phone.replace(/\\D/g, "");
    if (!phone) { showFieldError("client_phone", "This field is required."); ok = false; }
    else if (digits.length < 7) { showFieldError("client_phone", "Enter a valid phone number."); ok = false; }
    else { clearFieldError("client_phone"); }

    var cemail = qs("#client_email").value.trim();
    if (!cemail) { showFieldError("client_email", "This field is required."); ok = false; }
    else if (!isEmailValid(cemail)) { showFieldError("client_email", "Enter a valid email address."); ok = false; }
    else { clearFieldError("client_email"); }

    var dob = qs("#date_of_birth").value;
    if (!dob) { showFieldError("date_of_birth", "This field is required."); ok = false; }
    else {
      var d = new Date(dob + "T00:00:00");
      var now = new Date();
      var age = now.getFullYear() - d.getFullYear() - ((now.getMonth() < d.getMonth() || (now.getMonth() === d.getMonth() && now.getDate() < d.getDate())) ? 1 : 0);
      if (isNaN(d.getTime()) || age < 18 || age > 110) { showFieldError("date_of_birth", "Client must be between 18 and 110 years old."); ok = false; }
      else { clearFieldError("date_of_birth"); }
    }

    var assoc = qs('input[name="associated_person"]:checked');
    if (assoc && assoc.value === "other") {
      var crd = qs("#crd_number").value.trim();
      if (!crd) { showFieldError("crd_number", "CRD# is required."); ok = false; }
      else if (!/^\\d+$/.test(crd)) { showFieldError("crd_number", "CRD# must contain digits only."); ok = false; }
      else { clearFieldError("crd_number"); }
    }
    return ok;
  }

  function validateStep3() {
    var ok = true;
    if (!qs("#net_worth").value) { showFieldError("net_worth", "Please select an option."); ok = false; } else { clearFieldError("net_worth"); }
    if (!qs("#cumulative_investments").value) { showFieldError("cumulative_investments", "Please select an option."); ok = false; } else { clearFieldError("cumulative_investments"); }

    if (qs("#occupation").value === "Other" && !qs("#occupation_other").value.trim()) {
      showFieldError("occupation_other", "Describe the Client's occupation."); ok = false;
    } else { clearFieldError("occupation_other"); }

    var employerZipWrap = qs("#employer_zip_wrap");
    var employerZipVisible = employerZipWrap && employerZipWrap.style.display !== "none";
    var employerZip = qs("#employer_zip") ? qs("#employer_zip").value.trim() : "";
    if (employerZipVisible && employerZip && !/^\d{5}(-\d{4})?$/.test(employerZip)) {
      showFieldError("employer_zip", "Enter a valid US zip code (12345 or 12345-6789)."); ok = false;
    } else { clearFieldError("employer_zip"); }

    if (addressMismatchState.employer) { fieldWrap("employer_zip").classList.add("invalid"); ok = false; }

    var objOther = qs('input[name="investment_objectives"][value="Other"]');
    if (objOther && objOther.checked && !qs("#other_objective").value.trim()) {
      showFieldError("other_objective", "Describe the other investment objective."); ok = false;
    } else { clearFieldError("other_objective"); }

    var sophOther = qs('input[name="client_sophistication"][value="Other"]');
    if (sophOther && sophOther.checked && !qs("#sophistication_other").value.trim()) {
      showFieldError("sophistication_other", "Describe the other sophistication factor."); ok = false;
    } else { clearFieldError("sophistication_other"); }

    var yrs = qs("#years_experience").value;
    if (yrs !== "" && (isNaN(yrs) || parseInt(yrs, 10) < 0 || parseInt(yrs, 10) > 80)) {
      showFieldError("years_experience", "Enter a number between 0 and 80."); ok = false;
    } else { clearFieldError("years_experience"); }

    ["q_private_equity_five_years", "q_illiquid_investments", "q_risk_tolerance", "q_independent_judgement"].forEach(function (f) {
      if (!qs('input[name="' + f + '"]:checked')) { showFieldError(f, "This question requires a Yes or No answer."); ok = false; } else { clearFieldError(f); }
    });

    if (!qs('input[name="attestation"]:checked')) { showFieldError("attestation", "Select the applicable attestation."); ok = false; } else { clearFieldError("attestation"); }

    return ok;
  }

  function firstInvalidField() {
    var invalid = qs(".field.invalid");
    return invalid ? invalid.getAttribute("data-field") : null;
  }

  function ensureDraftId() {
    if (!draftId && window.crypto && typeof window.crypto.randomUUID === "function") {
      draftId = window.crypto.randomUUID();
    }
    return draftId;
  }

  function saveDraft(step) {
    // Fire-and-forget partial-submission capture: a failed draft save must
    // never interrupt the client, so every error path here is swallowed.
    if (!ensureDraftId()) { return; }
    try {
      var payload = collectFormData();
      payload.action = "draft";
      payload.draft_id = draftId;
      payload.last_step = step;
      fetch("/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).catch(function () {});
    } catch (e) { /* never interrupt the client */ }
  }

  qs("#next-btn").addEventListener("click", function () {
    var ok = currentStep === 1 ? validateStep1() : (currentStep === 2 ? validateStep2() : true);
    if (!ok) { focusAndScroll(firstInvalidField()); return; }
    goToStep(currentStep + 1);
    saveDraft(currentStep);
  });

  qs("#back-btn").addEventListener("click", function () {
    if (currentStep > 1) { goToStep(currentStep - 1); }
  });

  function collectFormData() {
    var data = {};
    var root = qs("#cef-form");
    var seenCheckbox = {};
    var seenRadio = {};
    qsa("[name]", root).forEach(function (el) {
      var name = el.name;
      if (!name) return;
      if (el.type === "checkbox") {
        if (seenCheckbox[name]) return;
        seenCheckbox[name] = true;
        var group = qsa('input[type="checkbox"][name="' + name + '"]', root);
        if (group.length === 1 && group[0].getAttribute("data-single") === "true") {
          data[name] = group[0].checked;
        } else {
          data[name] = group.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
        }
        return;
      }
      if (el.type === "radio") {
        if (seenRadio[name]) return;
        seenRadio[name] = true;
        var checked = qs('input[type="radio"][name="' + name + '"]:checked', root);
        data[name] = checked ? checked.value : "";
        return;
      }
      data[name] = el.value;
    });
    return data;
  }

  // ---------------------------------------------------------------------
  // Client-side PDF generation (jsPDF) -- one-page A4 Client Engagement
  // Form summary, built entirely in the browser. Two variants: "rms" (the
  // complete record) and "broker" (identical, Tax ID masked to its last 4
  // digits). Never allowed to block a submission: every call site wraps
  // this in a try/catch and treats a missing/failed jsPDF as "no PDF".
  // ---------------------------------------------------------------------
  var PDF_NAVY = [27, 42, 74];
  var PDF_GOLD = [185, 151, 91];
  var PDF_RED = [192, 57, 43];
  var PDF_GREY = [90, 90, 90];
  var PDF_MARGIN = 40;
  var PDF_HEADER_H = 68;
  var PDF_FOOTER_RESERVE = 50;
  var PDF_MIN_FONT = 7;
  var PDF_MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  function pdfJoinList(values, extra) {
    var text = (Array.isArray(values) && values.length) ? values.join(", ") : "-";
    if (extra) { text = (text !== "-") ? (text + " (Other: " + extra + ")") : ("Other: " + extra); }
    return text;
  }
  function pdfYesNoVal(v) { return (v === "Yes" || v === "No") ? v : "-"; }
  function maskTaxIdVal(taxId) {
    var s = String(taxId || "").trim();
    if (!s) return "-";
    if (s.length <= 4) return s;
    return new Array(s.length - 3).join("•") + s.slice(-4);
  }
  function arrayBufferToBase64(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = "";
    var chunkSize = 0x8000;
    for (var i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  }

  function sanitizeFilenamePart(s) {
    // Strips filesystem-unsafe characters by char code (forward slash,
    // backslash, colon, asterisk, question mark, double quote, angle
    // brackets, pipe) rather than a regex literal, since embedding a
    // literal backslash in a Python-templated JS string is error-prone.
    var badCodes = [47, 92, 58, 42, 63, 34, 60, 62, 124];
    var str = String(s || "");
    var out = "";
    for (var i = 0; i < str.length; i++) {
      if (badCodes.indexOf(str.charCodeAt(i)) === -1) { out += str.charAt(i); }
    }
    return out.trim();
  }

  function formatPdfDate(isoStr) {
    var d = new Date(isoStr);
    if (!isoStr || isNaN(d.getTime())) { return String(isoStr || "-"); }
    var hh = ("0" + d.getUTCHours()).slice(-2);
    var mm = ("0" + d.getUTCMinutes()).slice(-2);
    return PDF_MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear() +
      " — " + hh + ":" + mm + " UTC";
  }

  function formatPdfDob(dateStr) {
    if (!dateStr) { return "-"; }
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) { return String(dateStr); }
    return PDF_MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear();
  }

  function formatPdfPhone(phone) {
    var raw = String(phone || "").trim();
    if (!raw) { return "-"; }
    var digits = raw.replace(/\D/g, "");
    if (digits.length === 11 && digits.charAt(0) === "1") { digits = digits.slice(1); }
    if (digits.length === 10) {
      return "(" + digits.slice(0, 3) + ") " + digits.slice(3, 6) + "-" + digits.slice(6);
    }
    return raw;
  }

  function omitEmptyRows(rows) {
    return rows.filter(function (row) { return row[1] !== "-"; });
  }

  // Splits a value at natural break characters (@ . -), keeping each break
  // character attached to the end of the chunk before it, so a long email
  // or reference never gets cut mid-word. Only used as a last resort, when
  // the value still doesn't fit even at PDF_MIN_FONT.
  function splitAtNaturalBreaks(str) {
    var tokens = [];
    var current = "";
    for (var i = 0; i < str.length; i++) {
      current += str.charAt(i);
      if (str.charAt(i) === "@" || str.charAt(i) === "." || str.charAt(i) === "-") {
        tokens.push(current);
        current = "";
      }
    }
    if (current) { tokens.push(current); }
    return tokens;
  }

  function wrapAtNaturalBreaks(doc, value, maxWidth, fontSize) {
    doc.setFontSize(fontSize);
    var tokens = splitAtNaturalBreaks(String(value));
    var lines = [];
    var current = "";
    tokens.forEach(function (tok) {
      var candidate = current + tok;
      if (current && doc.getTextWidth(candidate) > maxWidth) {
        lines.push(current);
        current = tok;
      } else {
        current = candidate;
      }
    });
    if (current) { lines.push(current); }
    // A single token can still overflow (e.g. a long unbroken domain) --
    // hard-wrap it character by character as the very last resort.
    var finalLines = [];
    lines.forEach(function (line) {
      if (doc.getTextWidth(line) <= maxWidth) { finalLines.push(line); return; }
      var chunk = "";
      for (var i = 0; i < line.length; i++) {
        var next = chunk + line.charAt(i);
        if (chunk && doc.getTextWidth(next) > maxWidth) {
          finalLines.push(chunk);
          chunk = line.charAt(i);
        } else {
          chunk = next;
        }
      }
      if (chunk) { finalLines.push(chunk); }
    });
    return finalLines;
  }

  // jsPDF's own splitTextToSize force-wraps an overlong unspaced run (an
  // email, a reference code) at arbitrary character boundaries so the
  // *result* always fits maxWidth -- checking the wrapped lines' widths
  // afterward can never detect that. So instead check up front whether any
  // individual word (space-separated token) is too wide to fit on its own
  // line at this font size; only that condition means normal word-wrap
  // would have to cut a word apart.
  function longestWordWidth(doc, str, fontSize) {
    doc.setFontSize(fontSize);
    var words = String(str).split(" ");
    var max = 0;
    words.forEach(function (w) {
      var width = doc.getTextWidth(w);
      if (width > max) { max = width; }
    });
    return max;
  }

  // Renders `value` to fit maxWidth without ever breaking mid-word: tries
  // the base font size first (normal space-wrapping), then shrinks down to
  // PDF_MIN_FONT if any word is still too wide, then falls back to
  // wrapping at natural break characters (@ . -) if it's still too wide.
  function fitPdfValueLines(doc, value, maxWidth, baseFontSize) {
    var str = String(value);
    doc.setFont("helvetica", "normal");

    var fontSize = baseFontSize;
    if (longestWordWidth(doc, str, fontSize) > maxWidth) {
      fontSize = Math.min(PDF_MIN_FONT, baseFontSize);
    }

    if (longestWordWidth(doc, str, fontSize) > maxWidth) {
      // Even at the smallest font, at least one unbroken run of characters
      // still doesn't fit -- only now is it safe to break it, and only at
      // natural break characters (@ . -), never mid-word.
      var brokenLines = wrapAtNaturalBreaks(doc, str, maxWidth, fontSize);
      return { lines: brokenLines, fontSize: fontSize };
    }

    doc.setFontSize(fontSize);
    var lines = doc.splitTextToSize(str, maxWidth);
    lines = Array.isArray(lines) ? lines : [lines];
    return { lines: lines, fontSize: fontSize };
  }

  function buildCefPdfSections(data, submissionId, createdAtStr, maskTax) {
    var clientName = ((data.client_first_name || "") + " " + (data.client_last_name || "")).trim() || "-";
    var agentName = ((data.agent_first_name || "") + " " + (data.agent_last_name || "")).trim() || "-";

    var addrLine1Parts = [data.address_street, data.address_street2].filter(Boolean);
    var addrLine1 = addrLine1Parts.length ? addrLine1Parts.join(", ") : "-";
    var cityStateZip = [data.address_city, data.address_state, data.address_zip].filter(Boolean).join(", ") || "-";

    var associatedYes = data.associated_person === "other";
    var associatedText = associatedYes ? "Yes" : "No";
    if (associatedYes && data.crd_number) { associatedText += " (CRD# " + data.crd_number + ")"; }

    var occupation = data.occupation || "-";
    if (data.occupation === "Other" && data.occupation_other) { occupation = "Other (" + data.occupation_other + ")"; }

    var hasEmployer = !!(data.employer_name && data.employer_name.trim() && data.employer_name.trim().toUpperCase() !== "NONE");
    var employerAddr = hasEmployer
      ? ([data.employer_city, data.employer_state, data.employer_zip, data.employer_country].filter(Boolean).join(", ") || "-")
      : "-";

    var attestationText = { agent: "Attested by Agent", client: "Attested by Client" }[data.attestation] || "-";
    var refShort = submissionId ? String(submissionId).slice(0, 8) : "-";

    // Employment gets its own layout, not the generic omit-if-empty rule:
    // no employer collapses to a single "Employer: None" line, dropping
    // the Employer Address row entirely and the Occupation row unless it
    // actually has a value.
    var employmentRows;
    if (hasEmployer) {
      employmentRows = omitEmptyRows([
        ["Occupation", occupation],
        ["Employer", data.employer_name],
        ["Employer Address", employerAddr]
      ]);
    } else {
      employmentRows = [];
      if (occupation !== "-") { employmentRows.push(["Occupation", occupation]); }
      employmentRows.push(["Employer", "None"]);
    }

    var topRow = [
      ["Reference & Date", omitEmptyRows([
        ["Reference", refShort],
        ["Date", formatPdfDate(createdAtStr)]
      ])],
      ["Referring Agent", omitEmptyRows([
        ["Name", agentName],
        ["Email", data.agent_email || "-"]
      ])]
    ];

    var stacked = [
      ["Client", omitEmptyRows([
        ["Name", clientName],
        ["Email", data.client_email || "-"],
        ["Phone", formatPdfPhone(data.client_phone)],
        ["DOB", formatPdfDob(data.date_of_birth)]
      ])],
      ["Address", omitEmptyRows([
        ["Street", addrLine1],
        ["City / State / Zip", cityStateZip],
        ["Country", data.address_country || "-"]
      ])],
      ["Identification", omitEmptyRows([
        ["Tax ID / Gov't ID", maskTax ? maskTaxIdVal(data.tax_id) : (data.tax_id || "-")],
        ["Associated Person", associatedText]
      ])],
      ["Employment", employmentRows],
      ["Financial Profile", omitEmptyRows([
        ["Net Worth", data.net_worth || "-"],
        ["Cumulative Investments", data.cumulative_investments || "-"],
        ["Annual Income", data.annual_income || "-"],
        ["Years Experience", (data.years_experience !== undefined && data.years_experience !== "") ? data.years_experience : "-"]
      ])],
      ["Investment Profile", omitEmptyRows([
        ["Objectives", pdfJoinList(data.investment_objectives, data.other_objective)],
        ["Previous Investment Types", pdfJoinList(data.previous_investment_types)],
        ["Sophistication", pdfJoinList(data.client_sophistication, data.sophistication_other)]
      ])],
      ["Suitability", omitEmptyRows([
        ["Private equity/debt (past 5 yrs)", pdfYesNoVal(data.q_private_equity_five_years)],
        ["Can invest w/ limited liquidity need", pdfYesNoVal(data.q_illiquid_investments)],
        ["Risk tolerance for private securities", pdfYesNoVal(data.q_risk_tolerance)],
        ["Capable of independent judgement", pdfYesNoVal(data.q_independent_judgement)]
      ])],
      ["Attestation", omitEmptyRows([
        ["Attestation", attestationText]
      ])]
    ];
    return { topRow: topRow, stacked: stacked };
  }

  function drawPdfBlock(doc, x, y, w, title, rows, fontSize) {
    var labelW = w * 0.40;
    var valueW = w - labelW - 4;
    var titleSize = fontSize + 1;
    doc.setFont("helvetica", "bold");
    doc.setFontSize(titleSize);
    doc.setTextColor(PDF_NAVY[0], PDF_NAVY[1], PDF_NAVY[2]);
    doc.text(String(title).toUpperCase(), x, y + titleSize * 0.8);
    y += titleSize * 0.8 + 3;
    doc.setDrawColor(PDF_GOLD[0], PDF_GOLD[1], PDF_GOLD[2]);
    doc.setLineWidth(0.6);
    doc.line(x, y, x + w, y);
    y += fontSize * 0.6;
    rows.forEach(function (row) {
      var label = row[0], value = row[1];
      doc.setFont("helvetica", "bold");
      doc.setFontSize(fontSize);
      doc.setTextColor(PDF_GREY[0], PDF_GREY[1], PDF_GREY[2]);
      doc.text(String(label), x, y + fontSize * 0.8);

      var fit = fitPdfValueLines(doc, value, valueW, fontSize);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(fit.fontSize);
      doc.setTextColor(PDF_NAVY[0], PDF_NAVY[1], PDF_NAVY[2]);
      doc.text(fit.lines, x + labelW, y + fontSize * 0.8);
      var lineCount = fit.lines.length;
      y += Math.max(fontSize * 0.8 + 2, lineCount * (fit.fontSize * 1.15)) + 3;
    });
    return y;
  }

  function renderCefPdf(data, submissionId, createdAtStr, maskTax, fontSize) {
    var doc = new window.jsPDF({ orientation: "p", unit: "pt", format: "a4" });
    var pageW = doc.internal.pageSize.getWidth();
    var pageH = doc.internal.pageSize.getHeight();
    var contentW = pageW - 2 * PDF_MARGIN;
    var sections = buildCefPdfSections(data, submissionId, createdAtStr, maskTax);

    doc.setFillColor(PDF_NAVY[0], PDF_NAVY[1], PDF_NAVY[2]);
    doc.rect(0, 0, pageW, PDF_HEADER_H, "F");
    doc.setTextColor(255, 255, 255);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(17);
    doc.text("RAINMAKER SECURITIES", PDF_MARGIN, 30);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(11);
    doc.text("Client Engagement Form — Natural Person", PDF_MARGIN, 48);

    doc.setFillColor(PDF_GOLD[0], PDF_GOLD[1], PDF_GOLD[2]);
    doc.rect(0, PDF_HEADER_H, pageW, 3, "F");

    var y = PDF_HEADER_H + 20;
    var eligibilityWarning = data.net_worth === "NONE OF THE ABOVE" || data.cumulative_investments === "NONE OF THE ABOVE";
    if (eligibilityWarning) {
      doc.setFont("helvetica", "bold");
      doc.setFontSize(10.5);
      doc.setTextColor(PDF_RED[0], PDF_RED[1], PDF_RED[2]);
      doc.text("May not be eligible for private secondary transactions.", PDF_MARGIN, y);
      y += 16;
    }

    var colW = (contentW - 12) / 2;
    var yLeft = drawPdfBlock(doc, PDF_MARGIN, y, colW, sections.topRow[0][0], sections.topRow[0][1], fontSize);
    var yRight = drawPdfBlock(doc, PDF_MARGIN + colW + 12, y, colW, sections.topRow[1][0], sections.topRow[1][1], fontSize);
    y = Math.max(yLeft, yRight) + 6;

    sections.stacked.forEach(function (block) {
      y = drawPdfBlock(doc, PDF_MARGIN, y, contentW, block[0], block[1], fontSize) + 6;
    });

    var fits = y <= (pageH - PDF_FOOTER_RESERVE);

    var footerY = pageH - PDF_FOOTER_RESERVE + 10;
    doc.setDrawColor(PDF_GOLD[0], PDF_GOLD[1], PDF_GOLD[2]);
    doc.setLineWidth(1);
    doc.line(PDF_MARGIN, footerY, pageW - PDF_MARGIN, footerY);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(PDF_NAVY[0], PDF_NAVY[1], PDF_NAVY[2]);
    doc.text("Rainmaker Securities, LLC — Member FINRA/SIPC — Confidential", PDF_MARGIN, footerY + 12);

    return { doc: doc, fits: fits };
  }

  function buildCefPdf(data, submissionId, createdAtStr, maskTax) {
    var sizes = [9.5, 9, 8.5, 8, 7.5, 7, 6.5];
    var result = null;
    for (var i = 0; i < sizes.length; i++) {
      result = renderCefPdf(data, submissionId, createdAtStr, maskTax, sizes[i]);
      if (result.fits) { break; }
    }
    return result.doc;
  }

  function buildCefPdfFilename(data) {
    var clientName = sanitizeFilenamePart(((data.client_first_name || "") + " " + (data.client_last_name || "")).trim()) || "Client";
    var agentName = sanitizeFilenamePart(((data.agent_first_name || "") + " " + (data.agent_last_name || "")).trim()) || "Agent";
    var employer = String(data.employer_name || "").trim();
    var hasEmployer = !!(employer && employer.toUpperCase() !== "NONE");
    var segments = ["CEF-" + clientName];
    if (hasEmployer) { segments.push(sanitizeFilenamePart(employer)); }
    segments.push(agentName);
    return segments.join(" - ") + ".pdf";
  }

  qs("#submit-btn").addEventListener("click", function () {
    if (!validateStep3()) { focusAndScroll(firstInvalidField()); return; }
    var btn = qs("#submit-btn");
    btn.disabled = true;
    btn.textContent = "Submitting...";
    var payload = collectFormData();
    payload.action = "submit";
    ensureDraftId();
    if (draftId) { payload.draft_id = draftId; }

    var brokerPdfDoc = null;
    var brokerPdfFilename = null;
    if (typeof window.jsPDF === "function") {
      try {
        var nowStr = new Date().toISOString();
        var rmsDoc = buildCefPdf(payload, draftId, nowStr, false);
        var brokerDoc = buildCefPdf(payload, draftId, nowStr, true);
        payload.pdf_rms_b64 = arrayBufferToBase64(rmsDoc.output("arraybuffer"));
        payload.pdf_broker_b64 = arrayBufferToBase64(brokerDoc.output("arraybuffer"));
        brokerPdfDoc = brokerDoc;
        brokerPdfFilename = buildCefPdfFilename(payload);
      } catch (e) {
        brokerPdfDoc = null;
        brokerPdfFilename = null;
      }
    }

    fetch("/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json().then(function (body) { return { status: r.status, body: body }; }); })
      .then(function (res) {
        if (res.body && res.body.ok) {
          qs("#form-wrap").style.display = "none";
          qs("#success-screen").style.display = "block";
          qs("#success-id").textContent = res.body.submission_id;
          if (brokerPdfDoc) {
            var downloadBtn = qs("#download-pdf-btn");
            downloadBtn.style.display = "inline-block";
            downloadBtn.onclick = function () {
              brokerPdfDoc.save(brokerPdfFilename || "CEF.pdf");
            };
          }
          window.scrollTo({ top: 0, behavior: "smooth" });
        } else if (res.body && res.body.errors && Object.keys(res.body.errors).length) {
          clearAllErrors();
          var firstField = null;
          Object.keys(res.body.errors).forEach(function (f) {
            if (!firstField) firstField = f;
            showFieldError(f, res.body.errors[f]);
          });
          if (firstField) { focusAndScroll(firstField); }
          btn.disabled = false;
          btn.textContent = "Submit";
        } else if (res.body && res.body.message) {
          alert(res.body.message);
          btn.disabled = false;
          btn.textContent = "Submit";
        } else {
          alert("Something went wrong. Please try again.");
          btn.disabled = false;
          btn.textContent = "Submit";
        }
      }).catch(function () {
        alert("Network error. Please try again.");
        btn.disabled = false;
        btn.textContent = "Submit";
      });
  });

  function bindOtherToggle(groupName, fieldId) {
    qsa('input[type="checkbox"][name="' + groupName + '"]').forEach(function (cb) {
      if (cb.value === "Other") {
        cb.addEventListener("change", function () {
          var wrap = qs("#" + fieldId);
          wrap.style.display = cb.checked ? "block" : "none";
          if (!cb.checked) {
            var input = wrap.querySelector("input, textarea");
            if (input) input.value = "";
          }
        });
      }
    });
  }
  bindOtherToggle("investment_objectives", "other-objective-field");
  bindOtherToggle("client_sophistication", "sophistication-other-field");

  qsa('input[name="associated_person"]').forEach(function (r) {
    r.addEventListener("change", function () {
      var crdField = qs("#crd-field");
      var isOther = qs('input[name="associated_person"]:checked').value === "other";
      crdField.style.display = isOther ? "block" : "none";
      if (!isOther) { qs("#crd_number").value = ""; clearFieldError("crd_number"); }
    });
  });

  function initReferringAgent() {
    var params = new URLSearchParams(window.location.search);
    var agent = params.get("agent");
    var agentName = params.get("agent_name");
    if (agent) {
      var emailField = qs("#agent_email");
      emailField.value = agent + "@rainmakersecurities.com";
      emailField.readOnly = true;
      emailField.classList.add("locked");
      qs("#agent_email_lock").style.display = "inline";
    }
    if (agentName) {
      var parts = agentName.split(" ").filter(Boolean);
      if (parts.length) {
        var first = qs("#agent_first_name");
        first.value = parts[0];
        first.readOnly = true;
        first.classList.add("locked");
        if (parts.length > 1) {
          var last = qs("#agent_last_name");
          last.value = parts.slice(1).join(" ");
          last.readOnly = true;
          last.classList.add("locked");
        }
      }
    }

    // ?agent= present: same exact lock behavior as before, roster stays hidden.
    if (agent) { return; }

    // Raw link (no ?agent=): show the type-ahead agent search, hide the
    // free-text fields until "My agent is not listed" is chosen or a
    // roster match is picked. The roster itself never ships to the
    // browser — matches are looked up from the server as the client
    // types (debounced, 3+ characters only).
    var rosterWrap = qs("#agent-roster-wrap");
    var manualNames = qs("#agent-manual-names");
    var emailWrap = qs("#agent-email-wrap");
    var searchInput = qs("#agent_search");
    var suggestionsList = qs("#agent_suggestions");
    var emailDisplayWrap = qs("#agent-email-display-wrap");
    var emailDisplay = qs("#agent_email_display");

    rosterWrap.style.display = "block";
    manualNames.style.display = "none";
    emailWrap.style.display = "none";

    var searchDebounceTimer = null;
    var searchRequestId = 0;
    var lastFetchedQuery = null;
    var lastFetchedMatches = null;

    function hideSuggestions() { suggestionsList.style.display = "none"; }

    function appendSpecialOptions() {
      var noneLi = document.createElement("li");
      noneLi.className = "agent-suggestion-special";
      noneLi.textContent = "No referring agent";
      noneLi.addEventListener("mousedown", function (e) { e.preventDefault(); selectAgent("__none__"); });
      suggestionsList.appendChild(noneLi);
      var manualLi = document.createElement("li");
      manualLi.className = "agent-suggestion-special";
      manualLi.textContent = "My agent is not listed (enter manually)";
      manualLi.addEventListener("mousedown", function (e) { e.preventDefault(); selectAgent("__manual__"); });
      suggestionsList.appendChild(manualLi);
    }

    function renderSuggestions(matches) {
      suggestionsList.innerHTML = "";
      matches.forEach(function (match) {
        var li = document.createElement("li");
        li.textContent = match.name;
        li.addEventListener("mousedown", function (e) { e.preventDefault(); selectAgent(match); });
        suggestionsList.appendChild(li);
      });
      appendSpecialOptions();
      suggestionsList.style.display = "block";
    }

    function renderSearching() {
      suggestionsList.innerHTML = "";
      var li = document.createElement("li");
      li.className = "agent-suggestion-special";
      li.textContent = "Searching…";
      suggestionsList.appendChild(li);
      suggestionsList.style.display = "block";
    }

    function fetchMatches(q, requestId) {
      renderSearching();
      fetch("/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "agent_search", q: q, website: qs("#website").value })
      }).then(function (r) { return r.json(); })
        .then(function (data) {
          if (requestId !== searchRequestId) { return; }
          var matches = (data && data.matches) || [];
          lastFetchedQuery = q;
          lastFetchedMatches = matches;
          renderSuggestions(matches);
        })
        .catch(function () {
          if (requestId !== searchRequestId) { return; }
          lastFetchedQuery = q;
          lastFetchedMatches = [];
          renderSuggestions([]);
        });
    }

    function scheduleSearch() {
      if (searchDebounceTimer) { clearTimeout(searchDebounceTimer); searchDebounceTimer = null; }
      searchRequestId++;
      var q = searchInput.value.trim().toLowerCase();
      if (q.length < 3) {
        lastFetchedQuery = null;
        lastFetchedMatches = null;
        renderSuggestions([]);
        return;
      }
      if (lastFetchedQuery === q && lastFetchedMatches) {
        renderSuggestions(lastFetchedMatches);
        return;
      }
      var requestId = searchRequestId;
      searchDebounceTimer = setTimeout(function () { fetchMatches(q, requestId); }, 250);
    }

    function selectAgent(selection) {
      clearFieldError("agent_roster");
      hideSuggestions();
      if (selection === "__manual__") {
        agentSelectionKey = "__manual__";
        searchInput.value = "";
        emailDisplayWrap.style.display = "none";
        manualNames.style.display = "flex";
        emailWrap.style.display = "block";
        qs("#agent_first_name").value = "";
        qs("#agent_last_name").value = "";
        qs("#agent_email").value = "";
        clearFieldError("agent_first_name");
        clearFieldError("agent_last_name");
        clearFieldError("agent_email");
        return;
      }
      manualNames.style.display = "none";
      emailWrap.style.display = "none";
      if (selection === "__none__") {
        agentSelectionKey = "__none__";
        searchInput.value = "No referring agent";
        qs("#agent_first_name").value = OPS_FALLBACK.first;
        qs("#agent_last_name").value = OPS_FALLBACK.last;
        qs("#agent_email").value = OPS_FALLBACK.email;
        emailDisplay.value = OPS_FALLBACK.email;
        emailDisplayWrap.style.display = "block";
        return;
      }
      agentSelectionKey = selection.name;
      searchInput.value = selection.name;
      qs("#agent_first_name").value = selection.first;
      qs("#agent_last_name").value = selection.last;
      qs("#agent_email").value = selection.email;
      emailDisplay.value = selection.email;
      emailDisplayWrap.style.display = "block";
    }

    searchInput.addEventListener("input", function () {
      agentSelectionKey = null;
      emailDisplayWrap.style.display = "none";
      manualNames.style.display = "none";
      emailWrap.style.display = "none";
      qs("#agent_first_name").value = "";
      qs("#agent_last_name").value = "";
      qs("#agent_email").value = "";
      scheduleSearch();
    });
    searchInput.addEventListener("focus", function () { scheduleSearch(); });
    searchInput.addEventListener("blur", function () { hideSuggestions(); });
  }
  initReferringAgent();

  var uploadIdBtn = qs("#upload-id-btn");
  var idUploadNotice = qs("#id-upload-notice");
  if (uploadIdBtn && idUploadNotice) {
    uploadIdBtn.addEventListener("click", function () {
      idUploadNotice.classList.add("highlight");
      idUploadNotice.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setTimeout(function () { idUploadNotice.classList.remove("highlight"); }, 1200);
    });
  }

  var US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
    "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"
  ];

  // ZIP <-> City/State mismatch check (US only): a successful zippopotam
  // lookup for a 5-digit zip is cached here (keyed by zip, shared across
  // both address blocks) so re-checks as the client edits city/state never
  // refetch. A failed/timed-out/never-attempted lookup simply means no
  // cached entry -- the check silently stays out of the way; it is a
  // convenience layered on the API, never a hard dependency on it.
  function flashField(el) {
    if (!el) return;
    el.classList.remove("zip-flash");
    void el.offsetWidth; // force reflow so a re-triggered flash restarts the animation
    el.classList.add("zip-flash");
  }

  function hideZipNotice(cfg) {
    if (!cfg.noticeBoxId) return;
    var notice = qs("#" + cfg.noticeBoxId);
    if (notice) { notice.style.display = "none"; notice.textContent = ""; }
  }

  function applyAutofillFromLookup(cfg, lookup, zip) {
    var cityEl = cfg.cityId ? qs("#" + cfg.cityId) : null;
    var stateEl = qs("#" + cfg.stateId);
    var oldCity = cityEl ? cityEl.value.trim() : "";
    var newCity = lookup.cities[0] || "";
    var newState = lookup.state || "";
    var cityChanged = !!(newCity && oldCity && oldCity.toLowerCase() !== newCity.toLowerCase());

    if (newCity && cityEl) {
      cityEl.value = newCity;
      clearFieldError(cfg.cityId);
      flashField(cityEl);
    }
    if (newState && stateEl) {
      stateEl.value = newState;
      clearFieldError(cfg.stateId);
      flashField(stateEl);
    }

    if (cfg.noticeBoxId && (newCity || newState)) {
      var notice = qs("#" + cfg.noticeBoxId);
      if (notice) {
        var message = cityChanged
          ? "ZIP " + zip + ": city updated from '" + oldCity + "' to '" + newCity + "', state set to " + newState + "."
          : "ZIP " + zip + ": city and state set to " + newCity + ", " + newState + ".";
        notice.textContent = message;
        notice.style.display = "block";
      }
    }
  }

  function checkZipMismatch(cfg) {
    if (!cfg.zipId || !cfg.mismatchBoxId || !cfg.stateKey) return;
    var box = qs("#" + cfg.mismatchBoxId);
    if (!box) return;
    var zip = qs("#" + cfg.zipId).value.trim();
    var lookup = /^\d{5}$/.test(zip) ? zipLookupCache[zip] : null;
    var country = qs("#" + cfg.countryId).value;
    var cityVal = (cfg.cityId && qs("#" + cfg.cityId)) ? qs("#" + cfg.cityId).value.trim() : "";
    var stateVal = qs("#" + cfg.stateId).value.trim();

    var isMismatch = false;
    if (isUSCountry(country) && lookup && (cityVal || stateVal)) {
      var stateMatches = stateVal.toLowerCase() === (lookup.state || "").toLowerCase();
      var cityMatches = lookup.cities.some(function (c) { return c.toLowerCase() === cityVal.toLowerCase(); });
      isMismatch = !(stateMatches && cityMatches);
    }

    if (isMismatch) {
      box.textContent = "ZIP code " + zip + " corresponds to " + lookup.cities[0] + ", " + lookup.state +
        " — this doesn't match the city/state you entered. Please correct one of them.";
      box.style.display = "block";
      addressMismatchState[cfg.stateKey] = true;
      fieldWrap(cfg.zipId).classList.add("invalid");
    } else {
      box.style.display = "none";
      addressMismatchState[cfg.stateKey] = false;
      fieldWrap(cfg.zipId).classList.remove("invalid");
    }
    positionGlenNotes();
  }

  function autofillFromZip(zip, cfg) {
    var hasAbort = typeof AbortController !== "undefined";
    var controller = hasAbort ? new AbortController() : null;
    var timeoutId = setTimeout(function () { if (controller) controller.abort(); }, 3000);
    fetch("https://api.zippopotam.us/us/" + zip, controller ? { signal: controller.signal } : {})
      .then(function (r) { if (!r.ok) throw new Error("lookup failed"); return r.json(); })
      .then(function (data) {
        clearTimeout(timeoutId);
        var places = (data && data.places) || [];
        if (!places.length) return;
        var lookup = {
          cities: places.map(function (p) { return p["place name"]; }).filter(Boolean),
          state: places[0]["state"] || ""
        };
        zipLookupCache[zip] = lookup;
        applyAutofillFromLookup(cfg, lookup, zip);
        checkZipMismatch(cfg);
      })
      .catch(function () { clearTimeout(timeoutId); });
  }

  // Reusable country/state/zip behavior, shared by the client address (Step 2)
  // and the employer address (Step 3): US selection turns State into a
  // 50-state+DC dropdown and validates Zip as 5 or 5+4 digits, with a
  // best-effort City/State autofill from a 5-digit zip; non-US keeps free
  // text (and, when configured, hides the Zip field entirely).
  function setupAddressBlock(cfg) {
    function rebuildState(preserveValue) {
      var old = qs("#" + cfg.stateId);
      var val = preserveValue !== undefined ? preserveValue : old.value;
      var country = qs("#" + cfg.countryId).value;
      var el;
      if (isUSCountry(country)) {
        el = document.createElement("select");
        var optsHtml = '<option value="">Select State</option>';
        US_STATES.forEach(function (s) {
          optsHtml += "<option value='" + s + "'" + (s === val ? " selected" : "") + ">" + s + "</option>";
        });
        el.innerHTML = optsHtml;
      } else {
        el = document.createElement("input");
        el.type = "text";
        el.value = US_STATES.indexOf(val) === -1 ? val : "";
      }
      el.id = cfg.stateId;
      el.name = cfg.stateId;
      old.parentNode.replaceChild(el, old);
      el.addEventListener("input", function () { checkZipMismatch(cfg); });
      el.addEventListener("change", function () { checkZipMismatch(cfg); });
    }

    function updateZipVisibility() {
      var isUS = isUSCountry(qs("#" + cfg.countryId).value);
      if (cfg.zipHelperId) {
        var helper = qs("#" + cfg.zipHelperId);
        if (helper) { helper.style.display = isUS ? "block" : "none"; }
      }
      if (cfg.hideZipWhenNonUS && cfg.zipWrapId) {
        var wrap = qs("#" + cfg.zipWrapId);
        if (wrap) {
          wrap.style.display = isUS ? "block" : "none";
          if (!isUS && cfg.zipId) { qs("#" + cfg.zipId).value = ""; clearFieldError(cfg.zipId); }
        }
      }
    }

    qs("#" + cfg.countryId).addEventListener("change", function () {
      rebuildState();
      updateZipVisibility();
      clearFieldError(cfg.stateId);
      if (cfg.zipId) { clearFieldError(cfg.zipId); }
      if (!isUSCountry(qs("#" + cfg.countryId).value)) { hideZipNotice(cfg); }
      checkZipMismatch(cfg);
    });

    if (cfg.cityId) {
      var cityEl = qs("#" + cfg.cityId);
      if (cityEl) { cityEl.addEventListener("input", function () { checkZipMismatch(cfg); }); }
    }

    if (cfg.zipId) {
      var zipDebounce = null;
      qs("#" + cfg.zipId).addEventListener("input", function () {
        // Any edit to the zip itself retires whatever autofill notice was
        // showing for the previous value -- it persists only "until the
        // next zip change".
        hideZipNotice(cfg);
        if (!isUSCountry(qs("#" + cfg.countryId).value)) { checkZipMismatch(cfg); return; }
        var val = qs("#" + cfg.zipId).value.trim();
        if (!/^\d{5}$/.test(val)) { checkZipMismatch(cfg); return; }
        var cached = zipLookupCache[val];
        if (cached) {
          applyAutofillFromLookup(cfg, cached, val);
          checkZipMismatch(cfg);
          return;
        }
        checkZipMismatch(cfg);
        if (zipDebounce) { clearTimeout(zipDebounce); }
        zipDebounce = setTimeout(function () { autofillFromZip(val, cfg); }, 300);
      });
    }

    rebuildState();
    updateZipVisibility();
  }

  var addressCfg = {
    countryId: "address_country", stateId: "address_state", zipId: "address_zip",
    zipHelperId: "zip-helper", cityId: "address_city", hideZipWhenNonUS: false,
    mismatchBoxId: "address-zip-mismatch", noticeBoxId: "address-zip-notice", stateKey: "address"
  };
  var employerCfg = {
    countryId: "employer_country", stateId: "employer_state", zipId: "employer_zip",
    zipWrapId: "employer_zip_wrap", zipHelperId: "employer-zip-helper", cityId: "employer_city",
    hideZipWhenNonUS: true, mismatchBoxId: "employer-zip-mismatch", noticeBoxId: "employer-zip-notice",
    stateKey: "employer"
  };
  setupAddressBlock(addressCfg);
  setupAddressBlock(employerCfg);

  function hasEmployerName() {
    var v = qs("#employer_name").value.trim();
    return v !== "" && v.toUpperCase() !== "NONE";
  }

  function updateEmployerAddressState() {
    var block = qs("#employer-address-block");
    var enabled = hasEmployerName();
    block.classList.toggle("address-block-disabled", !enabled);
    qsa("input, select", block).forEach(function (el) { el.disabled = !enabled; });
    if (!enabled) {
      qs("#employer_city").value = "";
      var stateEl = qs("#employer_state");
      if (stateEl) { stateEl.value = ""; }
      qs("#employer_zip").value = "";
      clearFieldError("employer_state");
      clearFieldError("employer_zip");
      hideZipNotice(employerCfg);
      checkZipMismatch(employerCfg);
    }
  }
  qs("#employer_name").addEventListener("input", updateEmployerAddressState);
  updateEmployerAddressState();

  var occupationSelect = qs("#occupation");
  if (occupationSelect) {
    occupationSelect.addEventListener("change", function () {
      var wrap = qs("#occupation-other-field");
      var isOther = occupationSelect.value === "Other";
      wrap.style.display = isOther ? "block" : "none";
      if (!isOther) { qs("#occupation_other").value = ""; clearFieldError("occupation_other"); }
    });
  }

  function bindEligibilityWarning(selectId) {
    var select = qs("#" + selectId);
    var warning = qs("#" + selectId + "-warning");
    if (!select || !warning) return;
    select.addEventListener("change", function () {
      warning.style.display = select.value === "NONE OF THE ABOVE" ? "block" : "none";
    });
  }
  bindEligibilityWarning("net_worth");
  bindEligibilityWarning("cumulative_investments");

  // DOB calendar range: no default value, and the picker can't navigate
  // to or select a date younger than 18 or older than 110 years ago,
  // computed fresh on every page load rather than hardcoded.
  var dobInput = qs("#date_of_birth");
  if (dobInput) {
    var dobToday = new Date();
    var isoDateYearsAgo = function (years) {
      var d = new Date(dobToday.getFullYear() - years, dobToday.getMonth(), dobToday.getDate());
      var mm = ("0" + (d.getMonth() + 1)).slice(-2);
      var dd = ("0" + d.getDate()).slice(-2);
      return d.getFullYear() + "-" + mm + "-" + dd;
    };
    dobInput.max = isoDateYearsAgo(18);
    dobInput.min = isoDateYearsAgo(110);
  }

  goToStep(1);
})();
</script>
</body>
</html>
"""

ADMIN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rainmaker Securities — Admin Hub</title>
<style>
__SHARED_CSS__
</style>
</head>
<body>
__HEADER__
<div class="container">
__CONTENT__
</div>
</body>
</html>
"""


def render_form_page():
    page = FORM_PAGE_TEMPLATE
    page = page.replace("__SHARED_CSS__", SHARED_CSS)
    page = page.replace("__HEADER__", SHARED_HEADER)
    page = page.replace("__COUNTRY_OPTIONS__", COUNTRY_OPTIONS_HTML)
    page = page.replace("__OPS_FALLBACK_JSON__", OPS_FALLBACK_JS)
    page = page.replace("__RETIRING_RADIOS__", RETIRING_RADIOS_HTML)
    page = page.replace("__OCCUPATION_OPTIONS__", OCCUPATION_OPTIONS_HTML)
    page = page.replace("__NET_WORTH_SELECT__", NET_WORTH_SELECT_HTML)
    page = page.replace("__CUMULATIVE_SELECT__", CUMULATIVE_SELECT_HTML)
    page = page.replace("__ANNUAL_INCOME_SELECT__", ANNUAL_INCOME_SELECT_HTML)
    page = page.replace("__INVESTMENT_OBJECTIVES_CHECKS__", INVESTMENT_OBJECTIVES_CHECKS_HTML)
    page = page.replace("__PREVIOUS_INVESTMENT_CHECKS__", PREVIOUS_INVESTMENT_CHECKS_HTML)
    page = page.replace("__SOPHISTICATION_CHECKS__", SOPHISTICATION_CHECKS_HTML)
    page = page.replace("__Q1_RADIOS__", Q1_RADIOS_HTML)
    page = page.replace("__Q2_RADIOS__", Q2_RADIOS_HTML)
    page = page.replace("__Q3_RADIOS__", Q3_RADIOS_HTML)
    page = page.replace("__Q4_RADIOS__", Q4_RADIOS_HTML)
    return page


FORM_HTML = render_form_page()
ADMIN_PAGE_RENDERED = ADMIN_PAGE_TEMPLATE.replace("__SHARED_CSS__", SHARED_CSS).replace("__HEADER__", SHARED_HEADER)


# ---------------------------------------------------------------------------
# Reviewer margin notes ("Glen notes") — only ever rendered when the request
# carries ?notes=glen. FORM_HTML above is untouched by any of this; the
# annotated page is a separate string built from it, so a request without
# that query param gets back byte-for-byte the exact same page it always did.
# ---------------------------------------------------------------------------

GLEN_NOTE_TEXTS = {
    1: "Glen: You're viewing the annotated version — these margin notes appear only on this private preview link. Clients see a clean form with none of this.",
    2: "Glen: Two paths, both foolproof: each agent gets a personal link that pre-fills and locks their name — and if a raw link circulates, the client picks the agent from the official roster instead of typing. Either way, every submission arrives correctly attributed. No more blank, misspelled, or unknown agent fields. If the client selects 'No referring agent', the form auto-routes to ops@rainmakersecurities.com — nothing lands unassigned. One more safeguard: the agent roster never travels to the client's browser. The page asks our server only after three letters are typed and returns a handful of matches — a client can find their own agent, but can never browse or extract our broker list.",
    3: "Glen: I've researched the actual requirements — the PATRIOT Act CIP rule (31 CFR §1023.220) and SEC Reg S-P safeguards — and built the flow to adhere to them. The ID ask sits deliberately last: clients invest ten minutes first, so completion goes up, and step-by-step saving shows us exactly where anyone drops off before it. Nothing here is live: uploads stay off until RMS says where documents should live, and the data moves wherever you, the CTO, and Kirat want it to. My standing recommendation: brokers get the form, never the ID.",
    4: "Glen: Country now comes first and drives the rest: US clients get a proper state dropdown, the form pre-populates city and state from the zip code, and zip code errors are disallowed at entry. Bad addresses can no longer reach us.",
    5: "Glen: Every identification field is now required and format-checked before the client can advance — fewer incomplete forms, fewer repeat requests back to the client.",
    6: "Glen: Occupation is now a standardized dropdown with an Other option. Today we get free text — real examples from our files: 'VC', 'Real Estate', 'N/A' — which makes the data inconsistent and hard to use.",
    7: "Glen: It's not possible to have more than one net worth, so I switched these from check-all-that-apply to single-choice dropdowns. Same for annual income. One clean answer per question.",
    8: "Glen: If a client selects NONE OF THE ABOVE for net worth or investments, they see an immediate eligibility warning and the submission is flagged in the admin view — we catch unqualified clients at the door instead of after the paperwork.",
    9: "Glen: Currently, the system just generates a PDF for Kirat. But it would be possible to have it send an email copy to anyone you like, especially the broker, with the ID stripped out. I'd be happy to work with Ken to get this to write directly to your CRM after Kirat reviews/approves and hits 'Post to CRM'.",
    10: "Glen: On submit: the broker instantly gets a clean one-page PDF (sensitive identifiers masked, no ID), the RMS team gets the complete PDF plus any identity documents, and nobody retypes anything. For this demo the PDF also downloads right here so you can see it immediately. Want a client signature on it? I can add a draw-to-sign box at the attestation — or a simpler typed-name signature. Say the word.",
}

# Each entry: (note number, unique anchor substring already present in
# FORM_HTML, "before"/"after" the anchor). Anchors are existing markup, not
# markers added to the template, so the base page never carries any trace.
GLEN_NOTE_ANCHORS = [
    (1, '<ol class="instructions-list">', "before"),
    (2, '<div id="referring-agent-section">', "before"),
    (3, '<label class="field-label">Final Step: Identity Verification</label>', "before"),
    (4, '<h3>Address of Client</h3>', "after"),
    (5, '<div class="two-col">\n          <div class="field" data-field="client_phone">', "before"),
    (6, '<div class="field" data-field="occupation">', "before"),
    (7, '<div class="field" data-field="net_worth">', "before"),
    (8, '<div class="field" data-field="cumulative_investments">', "before"),
    (9, '<div class="field" data-field="attestation">', "before"),
    (10, 'I have refused to provide any missing information.\n          </label>', "after"),
]

GLEN_NOTES_CSS = """
#form-wrap { position: relative; }
.glen-note {
  background: #faf6ee; border: 1px solid #b9975b; color: #1b2a4a;
  font-size: 12.5px; line-height: 1.5; padding: 10px 12px; border-radius: 4px;
  margin: 14px 0; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.glen-note-label {
  display: block; font-size: 10px; font-weight: 700; letter-spacing: 0.6px;
  color: #b9975b; text-transform: uppercase; margin-bottom: 4px;
}
@media (min-width: 1100px) {
  .container { max-width: 1040px; }
  #form-wrap { max-width: 760px; }
  .glen-note { position: absolute; left: 100%; margin-left: 24px; width: 260px; margin-top: -4px; }
  .glen-note-connector { position: absolute; left: 0; width: 1px; background: rgba(185, 151, 91, 0.4); }
}
"""


def build_glen_note_html(note_number):
    text = html.escape(GLEN_NOTE_TEXTS[note_number])
    return (
        f"<div class='glen-note' data-glen-note='{note_number}'>"
        f"<span class='glen-note-label'>GLEN &mdash;</span>{text}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Feature-request panel: rendered only on the ?notes=glen view (bottom of
# every step -- one persistent panel, not duplicated per step) and the
# admin submissions list (top). Never present on the clean client page.
# ---------------------------------------------------------------------------

FEATURE_REQUEST_PANEL_CSS = """
.fr-panel {
  border: 1px solid var(--gold); border-radius: 6px; background: #fffdf8;
  padding: 16px 18px; margin: 28px 0; max-width: 760px;
}
.fr-panel-title {
  font-family: Georgia, 'Times New Roman', serif; font-weight: 700; color: var(--navy);
  font-size: 15px; margin-bottom: 10px;
}
.fr-input {
  width: 100%; box-sizing: border-box; padding: 9px 12px; border: 1px solid #d8cfba;
  border-radius: 4px; font-size: 14px; font-family: inherit; color: var(--navy);
}
.fr-input:focus { outline: none; border-color: var(--gold); }
.fr-status { color: #c0392b; font-size: 12px; margin-top: 6px; min-height: 14px; }
.fr-open-list, .fr-done-list { list-style: none; margin: 10px 0 0; padding: 0; }
.fr-item {
  display: flex; align-items: baseline; gap: 8px; padding: 8px 0;
  border-bottom: 1px solid #eee2c9; font-size: 13.5px; color: var(--navy);
}
.fr-item:last-child { border-bottom: none; }
.fr-item-text { flex: 1 1 auto; }
.fr-item-date { color: #8a7f63; font-size: 11.5px; white-space: nowrap; }
.fr-complete-btn {
  background: #fff; border: 1px solid var(--gold); color: var(--gold); border-radius: 3px;
  font-size: 11px; letter-spacing: 0.3px; text-transform: uppercase; padding: 3px 8px;
  cursor: pointer; white-space: nowrap;
}
.fr-complete-btn:hover { background: var(--gold); color: #fff; }
.fr-item-done .fr-item-text { text-decoration: line-through; color: #9a9a9a; }
.fr-undo-link { font-size: 11.5px; color: var(--gold); white-space: nowrap; cursor: pointer; }
.fr-done-section { margin-top: 10px; }
.fr-done-toggle {
  background: none; border: none; color: #8a7f63; font-size: 12px; cursor: pointer;
  padding: 0; text-decoration: underline;
}
"""

FEATURE_REQUEST_PANEL_JS = """
(function () {
  "use strict";
  var panel = document.getElementById("feature-request-panel");
  if (!panel) { return; }

  var mode = panel.getAttribute("data-mode");
  var adminKey = panel.getAttribute("data-admin-key") || "";

  var input = panel.querySelector(".fr-input");
  var statusEl = panel.querySelector(".fr-status");
  var openList = panel.querySelector(".fr-open-list");
  var doneSection = panel.querySelector(".fr-done-section");
  var doneList = panel.querySelector(".fr-done-list");
  var doneToggle = panel.querySelector(".fr-done-toggle");
  var doneCount = panel.querySelector(".fr-done-count");

  function currentPageLabel() {
    if (mode === "admin") { return "admin"; }
    var activeStep = document.querySelector(".step.active");
    return activeStep ? "Step " + activeStep.getAttribute("data-step") : "";
  }

  function authPayload(extra) {
    var payload = {};
    for (var k in extra) { if (Object.prototype.hasOwnProperty.call(extra, k)) { payload[k] = extra[k]; } }
    if (mode === "admin") { payload.key = adminKey; } else { payload.notes = "glen"; }
    return payload;
  }

  function postJson(payload) {
    return fetch("/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json().then(function (data) { return { status: r.status, body: data }; }); });
  }

  function formatDate(iso) {
    if (!iso) { return ""; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return iso; }
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function render(requests) {
    var open = requests.filter(function (r) { return !r.done; })
      .sort(function (a, b) { return (a.created_at || "").localeCompare(b.created_at || ""); });
    var done = requests.filter(function (r) { return r.done; })
      .sort(function (a, b) { return (a.created_at || "").localeCompare(b.created_at || ""); });

    openList.innerHTML = "";
    open.forEach(function (r) {
      var li = document.createElement("li");
      li.className = "fr-item";
      var text = document.createElement("span");
      text.className = "fr-item-text";
      text.textContent = r.text;
      var date = document.createElement("span");
      date.className = "fr-item-date";
      date.textContent = formatDate(r.created_at);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "fr-complete-btn";
      btn.textContent = "\\u2713 Mark complete";
      btn.addEventListener("click", function () { toggle(r.id, true); });
      li.appendChild(text);
      li.appendChild(date);
      li.appendChild(btn);
      openList.appendChild(li);
    });

    doneList.innerHTML = "";
    done.forEach(function (r) {
      var li = document.createElement("li");
      li.className = "fr-item fr-item-done";
      var text = document.createElement("span");
      text.className = "fr-item-text";
      text.textContent = r.text;
      var undo = document.createElement("a");
      undo.className = "fr-undo-link";
      undo.textContent = "undo";
      undo.addEventListener("click", function (e) { e.preventDefault(); toggle(r.id, false); });
      li.appendChild(text);
      li.appendChild(undo);
      doneList.appendChild(li);
    });
    doneCount.textContent = String(done.length);
    doneSection.style.display = done.length ? "block" : "none";
  }

  function loadList() {
    postJson(authPayload({ action: "feature_request_list" })).then(function (res) {
      if (res.body && res.body.ok) { render(res.body.requests || []); }
    }).catch(function () {});
  }

  function toggle(id, done) {
    postJson(authPayload({ action: "feature_request", id: id, done: done })).then(function (res) {
      if (res.body && res.body.ok) { loadList(); }
    }).catch(function () {});
  }

  if (doneToggle) {
    doneToggle.addEventListener("click", function () {
      doneList.style.display = doneList.style.display === "none" ? "block" : "none";
    });
  }

  if (input) {
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") { return; }
      var text = input.value.trim();
      if (!text) { return; }
      input.disabled = true;
      statusEl.textContent = "";
      postJson(authPayload({ action: "feature_request", text: text, page: currentPageLabel(), website: "" }))
        .then(function (res) {
          input.disabled = false;
          if (res.body && res.body.ok) {
            input.value = "";
            loadList();
          } else {
            statusEl.textContent = (res.body && res.body.message) || "Couldn't save that just now.";
          }
        })
        .catch(function () {
          input.disabled = false;
          statusEl.textContent = "Network error. Please try again.";
        });
    });
  }

  loadList();
})();
"""

# ADMIN_PAGE_RENDERED was computed before these constants existed; patch the
# feature-request panel's CSS/JS into it now (module load only runs once, so
# this is a one-time fixup, not a per-request cost).
ADMIN_PAGE_RENDERED = ADMIN_PAGE_RENDERED.replace(
    "</style>", FEATURE_REQUEST_PANEL_CSS + "</style>", 1
).replace(
    "</body>", f"<script>{FEATURE_REQUEST_PANEL_JS}</script>\n</body>", 1
)


def build_feature_request_panel_html(mode, admin_key=None):
    admin_key_attr = f" data-admin-key='{html.escape(admin_key, quote=True)}'" if admin_key else ""
    return (
        f"<div class='fr-panel' id='feature-request-panel' data-mode='{mode}'{admin_key_attr}>"
        "<div class='fr-panel-title'>Feature requests</div>"
        "<input type='text' class='fr-input' maxlength='2000' autocomplete='off' "
        "placeholder=\"Spot something to fix or add? Type it here and hit Enter — "
        "it stays on this list until Chad ships it.\">"
        "<div class='fr-status'></div>"
        "<ul class='fr-open-list'></ul>"
        "<div class='fr-done-section' style='display:none;'>"
        "<button type='button' class='fr-done-toggle'>Done (<span class='fr-done-count'>0</span>)</button>"
        "<ul class='fr-done-list' style='display:none;'></ul>"
        "</div>"
        "</div>"
    )


def build_form_html_with_glen_notes():
    page = FORM_HTML
    for number, anchor, position in GLEN_NOTE_ANCHORS:
        if page.count(anchor) != 1:
            continue
        note_html = build_glen_note_html(number)
        replacement = (note_html + anchor) if position == "before" else (anchor + note_html)
        page = page.replace(anchor, replacement, 1)

    # Feature-request panel: one persistent instance inside #form-wrap, right
    # after the form (so it trails whichever step is currently visible), fed
    # live via the shared JS module below.
    fr_anchor = "    </form>\n  </div>"
    if page.count(fr_anchor) == 1:
        panel_html = build_feature_request_panel_html("glen")
        page = page.replace(fr_anchor, fr_anchor + "\n\n  " + panel_html, 1)
        page = page.replace(
            "</body>", f"<script>{FEATURE_REQUEST_PANEL_JS}</script>\n</body>", 1
        )

    if "</style>" in page:
        page = page.replace("</style>", GLEN_NOTES_CSS + FEATURE_REQUEST_PANEL_CSS + "</style>", 1)
    return page


FORM_HTML_WITH_GLEN_NOTES = build_form_html_with_glen_notes()


def wrap_admin_page(content):
    return ADMIN_PAGE_RENDERED.replace("__CONTENT__", content)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def response_html(body, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
        "isBase64Encoded": False,
    }


def response_text(body, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/plain; charset=utf-8"},
        "body": body,
        "isBase64Encoded": False,
    }


def response_json(obj, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(obj),
        "isBase64Encoded": False,
    }


def get_json_body(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception:
            return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def get_client_ip(headers):
    xff = headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return "unknown"


def is_blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def dynamodb_safe(value):
    """Recursively guarantee a value contains no Python float -- DynamoDB's
    put_item/update_item reject the float type outright (it must be int,
    Decimal, or str). Applied once at the item-construction boundary right
    before every put_item call, so no upstream field-handling code has to
    individually guarantee this on every future field it adds."""
    if isinstance(value, float):
        return int(value) if value.is_integer() else str(value)
    if isinstance(value, list):
        return [dynamodb_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: dynamodb_safe(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Presign
# ---------------------------------------------------------------------------

def handle_presign(body):
    filename = str(body.get("filename", ""))
    content_type = str(body.get("content_type", "")).lower().strip()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    valid_types = ALLOWED_UPLOAD_EXTENSIONS.get(ext)
    if not valid_types:
        print("route=presign status=400")
        return response_json({"ok": False, "error": "invalid_file_type"}, 400)
    if content_type not in valid_types:
        print("route=presign status=400")
        return response_json({"ok": False, "error": "content_type_mismatch"}, 400)

    now = datetime.datetime.utcnow()
    key = f"cef-natural/{now:%Y}/{now:%m}/{uuid.uuid4()}.{ext}"
    try:
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": BUCKET_NAME, "Key": key, "ContentType": content_type},
            ExpiresIn=900,
        )
    except ClientError:
        print("route=presign status=500")
        return response_json({"ok": False, "error": "presign_failed"}, 500)

    print("route=presign status=200")
    return response_json({"ok": True, "url": url, "key": key})


# ---------------------------------------------------------------------------
# Submit validation
# ---------------------------------------------------------------------------

def validate_submission(raw):
    errors = {}
    data = {}

    def req_text(field, label, max_len=500):
        val = raw.get(field)
        if is_blank(val):
            errors[field] = f"{label} is required."
            return ""
        val = str(val).strip()[:max_len]
        data[field] = val
        return val

    def opt_text(field, max_len=1000):
        val = raw.get(field)
        if not is_blank(val):
            data[field] = str(val).strip()[:max_len]

    def opt_checklist(field, allowed):
        vals = raw.get(field)
        if isinstance(vals, list):
            cleaned = [str(v).strip() for v in vals if str(v).strip() in allowed]
            if cleaned:
                data[field] = cleaned

    # Step 1
    if not raw.get("confirm_read"):
        errors["confirm_read"] = "You must confirm you have read and understand this section."

    agent_first = str(raw.get("agent_first_name", "")).strip()
    agent_last = str(raw.get("agent_last_name", "")).strip()
    agent_email = str(raw.get("agent_email", "")).strip()

    if (agent_first, agent_last, agent_email) in AGENT_ROSTER_TUPLES or (
        agent_first == OPS_FALLBACK_FIRST and agent_last == OPS_FALLBACK_LAST and agent_email == OPS_FALLBACK_EMAIL
    ):
        # Known roster entry (or the "No referring agent" fallback) submitted
        # by the Step 1 dropdown — trust the resolved triple as-is.
        data["agent_first_name"] = agent_first[:500]
        data["agent_last_name"] = agent_last[:500]
        data["agent_email"] = agent_email[:500]
    else:
        # Manual entry: either the ?agent= URL-locked fields, or "My agent
        # is not listed" — validated the same way this always has been.
        if not agent_first:
            errors["agent_first_name"] = "Referring Agent First Name is required."
        else:
            data["agent_first_name"] = agent_first[:500]
        if not agent_last:
            errors["agent_last_name"] = "Referring Agent Last Name is required."
        else:
            data["agent_last_name"] = agent_last[:500]
        if not agent_email:
            errors["agent_email"] = "Referring Agent Email is required."
        elif not EMAIL_RE.match(agent_email):
            errors["agent_email"] = "Enter a valid email address."
        else:
            data["agent_email"] = agent_email[:500]

    # Step 2
    data["id_upload_status"] = ID_UPLOAD_STATUS_DEFERRED
    req_text("client_first_name", "Client First Name")
    req_text("client_last_name", "Client Last Name")
    country = req_text("address_country", "Country")
    req_text("address_street", "Street Address")
    opt_text("address_street2")
    req_text("address_city", "City")

    state = req_text("address_state", "State/Province")
    if state and country == "United States" and state not in US_STATES:
        errors["address_state"] = "Select a valid US state."

    zip_code = req_text("address_zip", "Postal/Zip Code")
    if zip_code and country == "United States" and not US_ZIP_RE.match(zip_code):
        errors["address_zip"] = "Enter a valid US zip code (12345 or 12345-6789)."

    phone = req_text("client_phone", "Client Phone Number")
    if phone and len(re.sub(r"\D", "", phone)) < 7:
        errors["client_phone"] = "Enter a valid phone number."

    client_email = req_text("client_email", "Client Email")
    if client_email and not EMAIL_RE.match(client_email):
        errors["client_email"] = "Enter a valid email address."

    req_text("tax_id", "Tax ID / Government ID")

    dob_raw = raw.get("date_of_birth")
    if is_blank(dob_raw):
        errors["date_of_birth"] = "Date of birth is required."
    else:
        try:
            dob = datetime.date.fromisoformat(str(dob_raw).strip())
            today = datetime.date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if dob > today:
                errors["date_of_birth"] = "Enter a valid date of birth."
            elif age < 18 or age > 110:
                errors["date_of_birth"] = "Client must be between 18 and 110 years old."
            else:
                data["date_of_birth"] = dob.isoformat()
        except ValueError:
            errors["date_of_birth"] = "Enter a valid date of birth."

    associated_person = str(raw.get("associated_person", "not_associated")).strip()
    if associated_person not in ("not_associated", "other"):
        associated_person = "not_associated"
    data["associated_person"] = associated_person
    if associated_person == "other":
        crd = str(raw.get("crd_number", "")).strip()
        if not crd:
            errors["crd_number"] = "CRD# is required."
        elif not re.match(r"^\d+$", crd):
            errors["crd_number"] = "CRD# must contain digits only."
        else:
            data["crd_number"] = crd

    opt_text("missing_info_notes", max_len=4000)

    # Step 3
    occupation = str(raw.get("occupation", "")).strip()
    if occupation:
        if occupation not in OCCUPATION_OPTIONS:
            errors["occupation"] = "Select a valid occupation."
        else:
            data["occupation"] = occupation
            if occupation == "Other":
                occupation_other = str(raw.get("occupation_other", "")).strip()
                if not occupation_other:
                    errors["occupation_other"] = "Describe the Client's occupation."
                else:
                    data["occupation_other"] = occupation_other[:200]

    opt_text("employer_name")
    employer_name_val = str(raw.get("employer_name", "")).strip()
    has_employer = bool(employer_name_val) and employer_name_val.upper() != "NONE"

    # Address of Employer fields are ignored entirely (never stored, never
    # format-checked) when there is no employer name — matches the client
    # collapsing/disabling that block in that case.
    employer_country = None
    if has_employer and not is_blank(raw.get("employer_country")):
        employer_country = str(raw.get("employer_country")).strip()
        data["employer_country"] = employer_country

    if has_employer:
        opt_text("employer_city")

    employer_state = str(raw.get("employer_state", "")).strip()
    if has_employer and employer_state:
        if employer_country == "United States" and employer_state not in US_STATES:
            errors["employer_state"] = "Select a valid US state."
        else:
            data["employer_state"] = employer_state

    if has_employer and employer_country == "United States":
        employer_zip = str(raw.get("employer_zip", "")).strip()
        if employer_zip:
            if not US_ZIP_RE.match(employer_zip):
                errors["employer_zip"] = "Enter a valid US zip code (12345 or 12345-6789)."
            else:
                data["employer_zip"] = employer_zip
    # employer_zip is never stored when the employer's country is not US.

    retiring = raw.get("retiring_five_years")
    if retiring in YES_NO:
        data["retiring_five_years"] = retiring

    net_worth = str(raw.get("net_worth", "")).strip()
    if net_worth not in NET_WORTH_OPTIONS:
        errors["net_worth"] = "Select the Client's net worth."
    else:
        data["net_worth"] = net_worth

    cumulative = str(raw.get("cumulative_investments", "")).strip()
    if cumulative not in NET_WORTH_OPTIONS:
        errors["cumulative_investments"] = "Select the Client's cumulative amount of investments."
    else:
        data["cumulative_investments"] = cumulative

    if data.get("net_worth") == "NONE OF THE ABOVE" or data.get("cumulative_investments") == "NONE OF THE ABOVE":
        data["eligibility_warning"] = True

    annual_income = str(raw.get("annual_income", "")).strip()
    if annual_income:
        if annual_income not in ANNUAL_INCOME_OPTIONS:
            errors["annual_income"] = "Select a valid annual income range."
        else:
            data["annual_income"] = annual_income

    opt_checklist("investment_objectives", INVESTMENT_OBJECTIVE_OPTIONS)
    if "Other" in data.get("investment_objectives", []):
        other_obj = str(raw.get("other_objective", "")).strip()
        if not other_obj:
            errors["other_objective"] = "Describe the other investment objective."
        else:
            data["other_objective"] = other_obj[:500]

    opt_checklist("previous_investment_types", PREVIOUS_INVESTMENT_OPTIONS)

    years_exp = raw.get("years_experience")
    if not is_blank(years_exp):
        try:
            years_exp_int = int(years_exp)
            if 0 <= years_exp_int <= 80:
                data["years_experience"] = years_exp_int
            else:
                errors["years_experience"] = "Enter a number between 0 and 80."
        except (ValueError, TypeError):
            errors["years_experience"] = "Enter a whole number between 0 and 80."

    opt_checklist("client_sophistication", SOPHISTICATION_OPTIONS)
    if "Other" in data.get("client_sophistication", []):
        soph_other = str(raw.get("sophistication_other", "")).strip()
        if not soph_other:
            errors["sophistication_other"] = "Describe the other sophistication factor."
        else:
            data["sophistication_other"] = soph_other[:500]

    for field in (
        "q_private_equity_five_years",
        "q_illiquid_investments",
        "q_risk_tolerance",
        "q_independent_judgement",
    ):
        val = raw.get(field)
        if val not in YES_NO:
            errors[field] = "This question requires a Yes or No answer."
        else:
            data[field] = val

    attestation = str(raw.get("attestation", "")).strip()
    if attestation not in ("agent", "client"):
        errors["attestation"] = "Select the applicable attestation."
    else:
        data["attestation"] = attestation

    return errors, data


def extract_draft_data(raw):
    """Lenient counterpart to validate_submission(): keeps whatever provided
    fields pass FORMAT checks, silently drops anything malformed, and never
    requires a field to be present. Used for action=draft partial saves."""
    data = {}

    def opt(field, max_len=500):
        val = raw.get(field)
        if not is_blank(val):
            data[field] = str(val).strip()[:max_len]

    def opt_checklist(field, allowed):
        vals = raw.get(field)
        if isinstance(vals, list):
            cleaned = [str(v).strip() for v in vals if str(v).strip() in allowed]
            if cleaned:
                data[field] = cleaned

    opt("agent_first_name")
    opt("agent_last_name")
    agent_email = str(raw.get("agent_email", "")).strip()
    if agent_email and EMAIL_RE.match(agent_email):
        data["agent_email"] = agent_email[:500]

    opt("client_first_name")
    opt("client_last_name")

    country = str(raw.get("address_country", "")).strip()
    if country:
        data["address_country"] = country[:500]
    opt("address_street")
    opt("address_street2")
    opt("address_city")

    state = str(raw.get("address_state", "")).strip()
    if state:
        if country == "United States":
            if state in US_STATES:
                data["address_state"] = state
        else:
            data["address_state"] = state[:500]

    zip_code = str(raw.get("address_zip", "")).strip()
    if zip_code:
        if country == "United States":
            if US_ZIP_RE.match(zip_code):
                data["address_zip"] = zip_code
        else:
            data["address_zip"] = zip_code[:20]

    phone = str(raw.get("client_phone", "")).strip()
    if phone and len(re.sub(r"\D", "", phone)) >= 7:
        data["client_phone"] = phone[:500]

    client_email = str(raw.get("client_email", "")).strip()
    if client_email and EMAIL_RE.match(client_email):
        data["client_email"] = client_email[:500]

    opt("tax_id")

    dob_raw = raw.get("date_of_birth")
    if not is_blank(dob_raw):
        try:
            dob = datetime.date.fromisoformat(str(dob_raw).strip())
            today = datetime.date.today()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if dob <= today and 18 <= age <= 110:
                data["date_of_birth"] = dob.isoformat()
        except ValueError:
            pass

    associated_person = str(raw.get("associated_person", "")).strip()
    if associated_person in ("not_associated", "other"):
        data["associated_person"] = associated_person
        if associated_person == "other":
            crd = str(raw.get("crd_number", "")).strip()
            if crd and re.match(r"^\d+$", crd):
                data["crd_number"] = crd

    opt("missing_info_notes", max_len=4000)

    occupation = str(raw.get("occupation", "")).strip()
    if occupation in OCCUPATION_OPTIONS:
        data["occupation"] = occupation
        if occupation == "Other":
            occupation_other = str(raw.get("occupation_other", "")).strip()
            if occupation_other:
                data["occupation_other"] = occupation_other[:200]

    opt("employer_name")
    employer_name_val = str(raw.get("employer_name", "")).strip()
    has_employer = bool(employer_name_val) and employer_name_val.upper() != "NONE"
    if has_employer:
        employer_country = str(raw.get("employer_country", "")).strip()
        if employer_country:
            data["employer_country"] = employer_country[:500]
        opt("employer_city")
        employer_state = str(raw.get("employer_state", "")).strip()
        if employer_state:
            if employer_country == "United States":
                if employer_state in US_STATES:
                    data["employer_state"] = employer_state
            else:
                data["employer_state"] = employer_state[:500]
        if employer_country == "United States":
            employer_zip = str(raw.get("employer_zip", "")).strip()
            if employer_zip and US_ZIP_RE.match(employer_zip):
                data["employer_zip"] = employer_zip

    retiring = raw.get("retiring_five_years")
    if retiring in YES_NO:
        data["retiring_five_years"] = retiring

    net_worth = str(raw.get("net_worth", "")).strip()
    if net_worth in NET_WORTH_OPTIONS:
        data["net_worth"] = net_worth
    cumulative = str(raw.get("cumulative_investments", "")).strip()
    if cumulative in NET_WORTH_OPTIONS:
        data["cumulative_investments"] = cumulative
    if data.get("net_worth") == "NONE OF THE ABOVE" or data.get("cumulative_investments") == "NONE OF THE ABOVE":
        data["eligibility_warning"] = True

    annual_income = str(raw.get("annual_income", "")).strip()
    if annual_income in ANNUAL_INCOME_OPTIONS:
        data["annual_income"] = annual_income

    opt_checklist("investment_objectives", INVESTMENT_OBJECTIVE_OPTIONS)
    if "Other" in data.get("investment_objectives", []):
        other_obj = str(raw.get("other_objective", "")).strip()
        if other_obj:
            data["other_objective"] = other_obj[:500]

    opt_checklist("previous_investment_types", PREVIOUS_INVESTMENT_OPTIONS)

    years_exp = raw.get("years_experience")
    if not is_blank(years_exp):
        try:
            years_exp_int = int(years_exp)
            if 0 <= years_exp_int <= 80:
                data["years_experience"] = years_exp_int
        except (ValueError, TypeError):
            pass

    opt_checklist("client_sophistication", SOPHISTICATION_OPTIONS)
    if "Other" in data.get("client_sophistication", []):
        soph_other = str(raw.get("sophistication_other", "")).strip()
        if soph_other:
            data["sophistication_other"] = soph_other[:500]

    for field in (
        "q_private_equity_five_years",
        "q_illiquid_investments",
        "q_risk_tolerance",
        "q_independent_judgement",
    ):
        val = raw.get(field)
        if val in YES_NO:
            data[field] = val

    attestation = str(raw.get("attestation", "")).strip()
    if attestation in ("agent", "client"):
        data["attestation"] = attestation

    return data


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(ip):
    hour_bucket = datetime.datetime.utcnow().strftime("%Y%m%d%H")
    rl_key = f"rl#{ip}#{hour_bucket}"
    try:
        resp = table.update_item(
            Key={"submission_id": rl_key},
            UpdateExpression="ADD submission_count :incr",
            ExpressionAttributeValues={":incr": 1},
            ReturnValues="UPDATED_NEW",
        )
        count = int(resp["Attributes"]["submission_count"])
    except ClientError:
        return True
    return count <= RATE_LIMIT_PER_HOUR


# In-memory, per-warm-container counter for the agent-search rate limit
# (module-level state persists across invocations of the same Lambda
# execution environment, the same way the boto3 clients below do). This is
# intentionally NOT backed by DynamoDB: unlike the submission rate limit,
# which guards against real spam records, this one only throttles a
# read-only lookup endpoint -- a generous, convenience-only budget, not a
# security boundary. Every keystroke-driven search used to add a DynamoDB
# write that didn't exist before the type-ahead was moved server-side,
# competing for the same table's write capacity as actual submissions;
# keeping this counter in memory removes that added write pressure
# entirely while still doing its job within a given warm container.
_agent_search_counts = {}


def check_agent_search_rate_limit(ip):
    hour_bucket = datetime.datetime.utcnow().strftime("%Y%m%d%H")
    key = (ip, hour_bucket)
    for stale_key in [k for k in _agent_search_counts if k[1] != hour_bucket]:
        # The hour rolled over -- drop stale buckets so a long-lived warm
        # container doesn't accumulate counters forever.
        del _agent_search_counts[stale_key]
    count = _agent_search_counts.get(key, 0) + 1
    _agent_search_counts[key] = count
    return count <= AGENT_SEARCH_RATE_LIMIT_PER_HOUR


# ---------------------------------------------------------------------------
# Agent search (server-side type-ahead — the roster never ships to the
# client; only the handful of entries matching a 3+ character query do)
# ---------------------------------------------------------------------------

def handle_agent_search(body, ip):
    if str(body.get("website", "")).strip():
        print("route=agent_search status=honeypot")
        return response_json({"matches": []})

    if not check_agent_search_rate_limit(ip):
        print("route=agent_search status=429")
        return response_json({"ok": False, "error": "rate_limited", "matches": []}, 429)

    q = str(body.get("q", "")).strip()
    if len(q) < AGENT_SEARCH_MIN_QUERY_LEN:
        print("route=agent_search status=200")
        return response_json({"matches": []})

    q_lower = q.lower()
    matches = []
    for display_name, email in AGENT_ROSTER:
        first, last = split_agent_display_name(display_name)
        if first.lower().startswith(q_lower) or last.lower().startswith(q_lower):
            matches.append({"name": display_name, "first": first, "last": last, "email": email})
            if len(matches) >= AGENT_SEARCH_MAX_RESULTS:
                break

    print("route=agent_search status=200")
    return response_json({"matches": matches})


# ---------------------------------------------------------------------------
# Admin link helper (shared by the submit notification and the sweep)
# ---------------------------------------------------------------------------

def build_admin_link(submission_id=None, host=None):
    admin_key = os.environ.get("ADMIN_KEY", "")
    resolved_host = host or os.environ.get(FORM_HOST_ENV_VAR, "")
    if not resolved_host:
        return ""
    link = f"https://{resolved_host}/?view=admin&key={admin_key}"
    if submission_id:
        link += f"&id={submission_id}"
    return link


# ---------------------------------------------------------------------------
# Feature requests: a lightweight standing punch list, stored in the same
# table under its own form_type so the CEF admin scan (filtered to
# FORM_TYPE_CEF_NATURAL) and the sweep scan (filtered on a "status"
# attribute these items never carry) both naturally skip these rows.
# Visible only on the ?notes=glen view and the admin view -- never on the
# clean client-facing page.
# ---------------------------------------------------------------------------

def _feature_request_caller_authorized(body):
    admin_key = os.environ.get("ADMIN_KEY", "")
    supplied_key = str(body.get("key", ""))
    return bool(admin_key) and supplied_key == admin_key or body.get("notes") == "glen"


def handle_feature_request_create(body, ip, host):
    if str(body.get("website", "")).strip():
        print("route=feature_request status=honeypot")
        return response_json({"ok": True})

    text = str(body.get("text", "")).strip()
    if not text:
        print("route=feature_request status=400")
        return response_json(
            {"ok": False, "error": "empty_text", "message": "Type something before hitting Enter."}, 400
        )
    text = text[:FEATURE_REQUEST_MAX_LEN]

    # Shared with the submission/draft rate limit -- this box is a
    # convenience, not something worth a separate budget.
    if not check_rate_limit(ip):
        print("route=feature_request status=429")
        return response_json(
            {
                "ok": False,
                "error": "rate_limited",
                "message": "Too many requests from this connection right now. Please try again in a few minutes.",
            },
            429,
        )

    page = str(body.get("page", "")).strip()[:100]
    request_id = "fr#" + str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat() + "Z"
    item = dynamodb_safe({
        "submission_id": request_id,
        "form_type": FORM_TYPE_FEATURE_REQUEST,
        "text": text,
        "page": page,
        "created_at": now,
        "done": False,
    })
    try:
        table.put_item(Item=item)
    except ClientError as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        print(f"route=feature_request status=500 error={type(e).__name__}:{error_code}")
        return response_json(
            {"ok": False, "error": "storage_failed", "message": "Couldn't save that just now. Please try again."},
            500,
        )

    if not EMAILS_ENABLED:
        print("route=feature_request status=emails_disabled")
    else:
        try:
            admin_link = build_admin_link(host=host)
            lines = [text, "", f"Page: {page or '-'}"]
            if admin_link:
                lines.append("")
                lines.append(admin_link)
            ses.send_email(
                Source=SES_SENDER,
                Destination={"ToAddresses": [SES_REPLY_TO]},
                Message={
                    "Subject": {"Data": "RMS Forms feature request"},
                    "Body": {"Text": {"Data": "\n".join(lines) + "\n"}},
                },
                ReplyToAddresses=[SES_REPLY_TO],
            )
        except Exception:
            print(f"route=feature_request status=email_failed request_id={request_id}")

    print(f"route=feature_request status=200 request_id={request_id}")
    return response_json({"ok": True, "id": request_id})


def handle_feature_request_toggle(body, host):
    if not _feature_request_caller_authorized(body):
        print("route=feature_request_toggle status=403")
        return response_text("Forbidden", 403)

    request_id = str(body.get("id", "")).strip()
    if not request_id:
        return response_json({"ok": False, "error": "missing_id"}, 400)

    try:
        existing = table.get_item(Key={"submission_id": request_id}).get("Item")
    except ClientError:
        existing = None
    if not existing or existing.get("form_type") != FORM_TYPE_FEATURE_REQUEST:
        print("route=feature_request_toggle status=404")
        return response_json({"ok": False, "error": "not_found"}, 404)

    done = bool(body.get("done"))
    now = datetime.datetime.utcnow().isoformat() + "Z"
    try:
        if done:
            table.update_item(
                Key={"submission_id": request_id},
                UpdateExpression="SET #done = :done, #done_at = :done_at",
                ExpressionAttributeNames={"#done": "done", "#done_at": "done_at"},
                ExpressionAttributeValues={":done": True, ":done_at": now},
            )
        else:
            table.update_item(
                Key={"submission_id": request_id},
                UpdateExpression="SET #done = :done REMOVE #done_at",
                ExpressionAttributeNames={"#done": "done", "#done_at": "done_at"},
                ExpressionAttributeValues={":done": False},
            )
    except ClientError as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        print(f"route=feature_request_toggle status=500 error={type(e).__name__}:{error_code}")
        return response_json({"ok": False, "error": "storage_failed"}, 500)

    print(f"route=feature_request_toggle status=200 request_id={request_id} done={done}")
    return response_json({"ok": True})


def handle_feature_request_list(body):
    try:
        resp = table.scan(FilterExpression=Attr("form_type").eq(FORM_TYPE_FEATURE_REQUEST))
        items = resp.get("Items", [])
        while "LastEvaluatedKey" in resp:
            resp = table.scan(
                FilterExpression=Attr("form_type").eq(FORM_TYPE_FEATURE_REQUEST),
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            items.extend(resp.get("Items", []))
    except ClientError:
        items = []

    requests = [
        {
            "id": it.get("submission_id"),
            "text": it.get("text", ""),
            "page": it.get("page", ""),
            "created_at": it.get("created_at", ""),
            "done": bool(it.get("done")),
        }
        for it in items
    ]
    return response_json({"ok": True, "requests": requests})


# ---------------------------------------------------------------------------
# Submission emails (Part C): broker copy (masked) + RMS copy (full + ID)
# ---------------------------------------------------------------------------

def _fetch_s3_bytes(s3_key):
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        return resp["Body"].read()
    except ClientError:
        return None


def pdf_attachment_filename(item, submission_id, variant):
    client_ref = item.get("client_last_name") or item.get("client_first_name") or "Client"
    client_ref = re.sub(r"[^A-Za-z0-9_-]+", "", str(client_ref)) or "Client"
    return f"CEF-{client_ref}-{str(submission_id)[:8]}-{variant}.pdf"


def send_submission_emails(item, submission_id, host):
    if not EMAILS_ENABLED:
        print("route=submit status=emails_disabled")
        return

    client_first = item.get("client_first_name", "")
    client_last = item.get("client_last_name", "")
    client_ref = client_last or client_first or "Client"
    agent_email = str(item.get("agent_email", "")).strip()
    admin_link = build_admin_link(submission_id, host)

    # 1) Broker copy: only to a whitelisted RMS-domain agent, Tax ID masked
    #    (the browser generated it that way), no ID documents. The PDF is
    #    fetched from S3 by the key handle_submit recorded on the item; if
    #    the client never sent a valid PDF (or storing it failed), the
    #    email still goes out with unchanged body text, just no attachment.
    if agent_email and agent_email.lower().endswith(AGENT_EMAIL_DOMAIN):
        try:
            msg = MIMEMultipart()
            msg["Subject"] = f"New Client Engagement Form: {client_first} {client_last}".strip()
            msg["From"] = SES_SENDER
            msg["To"] = agent_email
            msg["Reply-To"] = SES_REPLY_TO
            body_text = (
                "A new Client Engagement Form has been submitted.\n\n"
                f"Client: {client_first} {client_last}\n"
                "A one-page summary is attached as a PDF. Sensitive identifiers are masked.\n\n"
                "View full submission:\n"
                f"{admin_link if admin_link else '(admin link unavailable)'}\n"
            )
            msg.attach(MIMEText(body_text, "plain"))
            broker_pdf_key = item.get("pdf_broker_s3_key")
            if broker_pdf_key:
                broker_pdf_bytes = _fetch_s3_bytes(broker_pdf_key)
                if broker_pdf_bytes:
                    attachment = MIMEApplication(broker_pdf_bytes, _subtype="pdf")
                    attachment.add_header(
                        "Content-Disposition", "attachment",
                        filename=pdf_attachment_filename(item, submission_id, "broker"),
                    )
                    msg.attach(attachment)
            ses.send_raw_email(RawMessage={"Data": msg.as_bytes()})
            print(f"route=submit status=broker_email_sent submission_id={submission_id}")
        except Exception:
            print(f"route=submit status=broker_email_failed submission_id={submission_id}")

    # 2) RMS copy: always to RMS_TEAM_EMAIL, full (unmasked) PDF fetched from
    #    S3 (if one was stored), plus any ID document already uploaded.
    try:
        msg = MIMEMultipart()
        msg["Subject"] = f"[RMS Copy] New Client Engagement Form: {client_first} {client_last}".strip()
        msg["From"] = SES_SENDER
        msg["To"] = RMS_TEAM_EMAIL
        msg["Reply-To"] = SES_REPLY_TO
        body_text = (
            "A new Client Engagement Form has been submitted.\n\n"
            f"Client: {client_first} {client_last}\n"
            f"Referring Agent: {item.get('agent_first_name', '')} {item.get('agent_last_name', '')}\n"
            "The complete one-page PDF is attached, plus any identity document on file.\n\n"
            "View full submission:\n"
            f"{admin_link if admin_link else '(admin link unavailable)'}\n"
        )
        msg.attach(MIMEText(body_text, "plain"))

        rms_pdf_key = item.get("pdf_rms_s3_key")
        if rms_pdf_key:
            rms_pdf_bytes = _fetch_s3_bytes(rms_pdf_key)
            if rms_pdf_bytes:
                attachment = MIMEApplication(rms_pdf_bytes, _subtype="pdf")
                attachment.add_header(
                    "Content-Disposition", "attachment",
                    filename=pdf_attachment_filename(item, submission_id, "rms"),
                )
                msg.attach(attachment)

        s3_key = item.get("id_upload_s3_key")
        if s3_key:
            id_bytes = _fetch_s3_bytes(s3_key)
            if id_bytes:
                ext = str(s3_key).rsplit(".", 1)[-1].lower()
                id_attachment = MIMEApplication(id_bytes, _subtype=ext or "octet-stream")
                id_attachment.add_header(
                    "Content-Disposition", "attachment", filename=f"ID-{client_ref}-{submission_id[:8]}.{ext}"
                )
                msg.attach(id_attachment)

        ses.send_raw_email(RawMessage={"Data": msg.as_bytes()})
        print(f"route=submit status=rms_email_sent submission_id={submission_id}")
    except Exception:
        print(f"route=submit status=rms_email_failed submission_id={submission_id}")


# ---------------------------------------------------------------------------
# Draft (partial submission capture)
# ---------------------------------------------------------------------------

def handle_draft(body, ip):
    if str(body.get("website", "")).strip():
        print("route=draft status=honeypot")
        return response_json({"ok": True})

    try:
        body_size = len(json.dumps(body).encode("utf-8"))
    except (TypeError, ValueError):
        body_size = 0
    if body_size > DRAFT_MAX_BYTES:
        print("route=draft status=413")
        return response_json({"ok": False, "error": "draft_too_large"}, 413)

    if not check_rate_limit(ip):
        print("route=draft status=429")
        return response_json(
            {
                "ok": False,
                "error": "rate_limited",
                "message": "Submission limit reached for this hour. Please wait a few minutes and try again.",
            },
            429,
        )

    if not body.get("confirm_read"):
        print("route=draft status=400")
        return response_json({"ok": False, "error": "confirm_read_required"}, 400)

    draft_id = str(body.get("draft_id", "")).strip()
    if not draft_id:
        print("route=draft status=400")
        return response_json({"ok": False, "error": "missing_draft_id"}, 400)

    try:
        last_step = int(body.get("last_step"))
    except (TypeError, ValueError):
        last_step = 1
    last_step = max(1, min(3, last_step))

    data = extract_draft_data(body)
    now = datetime.datetime.utcnow().isoformat() + "Z"

    try:
        existing = table.get_item(Key={"submission_id": draft_id}).get("Item")
    except ClientError:
        existing = None
    created_at = (existing or {}).get("created_at") or now

    item = {
        "submission_id": draft_id,
        "form_type": FORM_TYPE_CEF_NATURAL,
        "status": DRAFT_STATUS_PARTIAL,
        "last_step": last_step,
        "created_at": created_at,
        "updated_at": now,
        "ip": ip,
    }
    item.update(data)
    item = dynamodb_safe(item)

    try:
        table.put_item(Item=item)
    except ClientError as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        print(f"route=draft status=500 draft_id={draft_id} error={type(e).__name__}:{error_code}")
        return response_json({"ok": False, "error": "storage_failed"}, 500)

    print(f"route=draft status=200 draft_id={draft_id} last_step={last_step}")
    return response_json({"ok": True, "draft_id": draft_id})


# ---------------------------------------------------------------------------
# Sweep (EventBridge Scheduler invocation: notify + delete stale partials)
# ---------------------------------------------------------------------------

def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def send_partial_notification_email(item):
    if not EMAILS_ENABLED:
        print("route=sweep status=emails_disabled")
        return
    agent_email = str(item.get("agent_email", "")).strip()
    if not agent_email or not agent_email.lower().endswith(AGENT_EMAIL_DOMAIN):
        return
    client_name = f"{item.get('client_first_name', '')} {item.get('client_last_name', '')}".strip() or "Unknown client"
    submission_id = item.get("submission_id", "")
    try:
        admin_link = build_admin_link(submission_id)
        subject = f"Form partially completed: {client_name}"
        lines = [
            "A Client Engagement Form was started but not completed.",
            "",
            f"Client Name: {client_name}",
        ]
        if item.get("client_email"):
            lines.append(f"Client Email: {item['client_email']}")
        if item.get("client_phone"):
            lines.append(f"Client Phone: {item['client_phone']}")
        lines.append(f"Step Reached: {item.get('last_step', '?')} of 3")
        lines.append(f"Last Active (UTC): {item.get('updated_at', '')}")
        lines.append("")
        if admin_link:
            lines.append("View full submission:")
            lines.append(admin_link)
        else:
            lines.append(
                f"Admin link unavailable: set the {FORM_HOST_ENV_VAR} environment variable "
                "on the rms-forms Lambda to enable links in these emails."
            )
        body_text = "\n".join(lines) + "\n"
        ses.send_email(
            Source=SES_SENDER,
            Destination={"ToAddresses": [agent_email]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body_text}}},
            ReplyToAddresses=[SES_REPLY_TO],
        )
    except Exception:
        print(f"route=sweep status=email_failed submission_id={submission_id}")


def handle_sweep():
    now = datetime.datetime.utcnow()
    notified = 0
    deleted = 0
    errors = 0

    try:
        items = []
        resp = table.scan(FilterExpression=Attr("status").eq(DRAFT_STATUS_PARTIAL))
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = table.scan(
                FilterExpression=Attr("status").eq(DRAFT_STATUS_PARTIAL),
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            items.extend(resp.get("Items", []))
    except ClientError:
        items = []

    for item in items:
        submission_id = item.get("submission_id", "")
        try:
            created_dt = _parse_iso_datetime(item.get("created_at"))
            updated_dt = _parse_iso_datetime(item.get("updated_at"))

            if created_dt and (now - created_dt) > DRAFT_DELETE_AFTER:
                try:
                    table.delete_item(Key={"submission_id": submission_id})
                    deleted += 1
                except ClientError:
                    errors += 1
                continue

            if item.get("notified"):
                continue
            if not updated_dt or (now - updated_dt) < DRAFT_NOTIFY_AFTER:
                continue
            if not created_dt or (now - created_dt) > DRAFT_NOTIFY_WITHIN:
                continue

            # Emails disabled: leave this partial un-notified (no send, no
            # notified flag) so it's picked up for real once EMAILS_ENABLED
            # is turned back on. The 30-day delete branch above is
            # unaffected -- cleanup still runs either way.
            if not EMAILS_ENABLED:
                continue

            send_partial_notification_email(item)
            try:
                table.update_item(
                    Key={"submission_id": submission_id},
                    UpdateExpression="SET notified = :t",
                    ExpressionAttributeValues={":t": True},
                )
            except ClientError:
                pass
            notified += 1
        except Exception:
            errors += 1
            continue

    if not EMAILS_ENABLED:
        print("route=sweep status=emails_disabled")
    print(f"route=sweep status=200 notified={notified} deleted={deleted} errors={errors}")
    return {"ok": True, "notified": notified, "deleted": deleted, "errors": errors}


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def decode_valid_pdf(b64_value):
    """Best-effort decode of a client-supplied base64 PDF: must actually be
    base64, decode to <= PDF_MAX_BYTES, and start with the %PDF- magic
    bytes. Returns the decoded bytes, or None if anything about it is
    invalid -- the caller treats None as "no PDF" and never fails the
    submission over it."""
    if not b64_value or not isinstance(b64_value, str):
        return None
    try:
        decoded = base64.b64decode(b64_value, validate=True)
    except Exception:
        return None
    if not decoded or len(decoded) > PDF_MAX_BYTES:
        return None
    if not decoded.startswith(b"%PDF-"):
        return None
    return decoded


def sanitize_pdf_filename_part(value):
    return re.sub(r'[\\/:*?"<>|]+', "", str(value or "")).strip()


def build_pdf_friendly_filename(item):
    client_name = sanitize_pdf_filename_part(
        f"{item.get('client_first_name', '')} {item.get('client_last_name', '')}".strip()
    ) or "Client"
    agent_name = sanitize_pdf_filename_part(
        f"{item.get('agent_first_name', '')} {item.get('agent_last_name', '')}".strip()
    ) or "Agent"
    employer = str(item.get("employer_name", "")).strip()
    has_employer = bool(employer) and employer.upper() != "NONE"
    segments = [f"CEF-{client_name}"]
    if has_employer:
        segments.append(sanitize_pdf_filename_part(employer))
    segments.append(agent_name)
    return " - ".join(segments) + ".pdf"


def store_submission_pdf(submission_id, variant, pdf_bytes, friendly_filename):
    key = f"pdfs/{submission_id}-{variant}.pdf"
    try:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            ContentDisposition=f'attachment; filename="{friendly_filename}"',
        )
        return key
    except ClientError:
        print(f"route=submit status=pdf_s3_failed submission_id={submission_id} variant={variant}")
        return None


def handle_submit(body, ip, host):
    if str(body.get("website", "")).strip():
        print("route=submit status=honeypot")
        return response_json({"ok": True, "submission_id": str(uuid.uuid4())})

    if not check_rate_limit(ip):
        print("route=submit status=429")
        return response_json(
            {
                "ok": False,
                "error": "rate_limited",
                "message": "Submission limit reached for this hour. Please wait a few minutes and try again.",
            },
            429,
        )

    errors, data = validate_submission(body)
    if errors:
        print("route=submit status=400")
        return response_json({"ok": False, "errors": errors}, 400)

    now = datetime.datetime.utcnow().isoformat() + "Z"
    draft_id = str(body.get("draft_id", "")).strip()
    created_at = None
    if draft_id:
        try:
            existing = table.get_item(Key={"submission_id": draft_id}).get("Item")
        except ClientError:
            existing = None
        if existing:
            created_at = existing.get("created_at")
        submission_id = draft_id
    else:
        submission_id = str(uuid.uuid4())

    if not created_at:
        created_at = now

    item = {
        "submission_id": submission_id,
        "form_type": FORM_TYPE_CEF_NATURAL,
        "status": SUBMISSION_STATUS_COMPLETE,
        "created_at": created_at,
        "updated_at": now,
        "ip": ip,
    }
    item.update(data)
    item = dynamodb_safe(item)

    try:
        table.put_item(Item=item)
    except ClientError as e:
        # AWS's structured Error Code (e.g. "ValidationException",
        # "ProvisionedThroughputExceededException") is a fixed, known-safe
        # vocabulary -- unlike str(e), it never echoes request/field values,
        # so it's always safe to log alongside the exception class name and
        # submission_id. No form field values are ever logged here.
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        print(f"route=submit status=500 submission_id={submission_id} error={type(e).__name__}:{error_code}")
        ref = submission_id[:8]
        return response_json(
            {
                "ok": False,
                "error": "storage_failed",
                "message": f"We couldn't save your submission just now. Please try again in a moment. (ref: {ref})",
            },
            500,
        )

    # PDF handling comes strictly after the DynamoDB write above: the
    # submission itself must never be lost over a PDF/S3/email problem.
    # The client already generated both PDFs in the browser (jsPDF); this
    # only validates and stores what it sent, never regenerates anything.
    pdf_broker_bytes = decode_valid_pdf(body.get("pdf_broker_b64"))
    pdf_rms_bytes = decode_valid_pdf(body.get("pdf_rms_b64"))
    pdf_friendly_filename = build_pdf_friendly_filename(item)

    pdf_keys = {}
    if pdf_broker_bytes:
        key = store_submission_pdf(submission_id, "broker", pdf_broker_bytes, pdf_friendly_filename)
        if key:
            pdf_keys["pdf_broker_s3_key"] = key
    if pdf_rms_bytes:
        key = store_submission_pdf(submission_id, "rms", pdf_rms_bytes, pdf_friendly_filename)
        if key:
            pdf_keys["pdf_rms_s3_key"] = key

    if pdf_keys:
        try:
            table.update_item(
                Key={"submission_id": submission_id},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in pdf_keys),
                ExpressionAttributeNames={f"#{k}": k for k in pdf_keys},
                ExpressionAttributeValues={f":{k}": v for k, v in pdf_keys.items()},
            )
            item.update(pdf_keys)
        except ClientError:
            print(f"route=submit status=pdf_keys_save_failed submission_id={submission_id}")

    send_submission_emails(item, submission_id, host)

    print(f"route=submit status=200 submission_id={submission_id}")
    return response_json({"ok": True, "submission_id": submission_id})


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------

def format_field_value(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "":
        return "—"
    return str(value)


def render_admin_list(admin_key):
    items = []
    try:
        resp = table.scan(FilterExpression=Attr("form_type").eq(FORM_TYPE_CEF_NATURAL))
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = table.scan(
                FilterExpression=Attr("form_type").eq(FORM_TYPE_CEF_NATURAL),
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            items.extend(resp.get("Items", []))
    except ClientError:
        items = []

    items.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)

    rows = []
    for it in items:
        sid = str(it.get("submission_id", ""))
        date_str = str(it.get("created_at", ""))[:19].replace("T", " ")
        name = f"{it.get('client_first_name', '')} {it.get('client_last_name', '')}".strip()
        name_html = html.escape(name)
        if it.get("eligibility_warning"):
            name_html += " <span class='not-eligible-tag'>&#9888; NOT ELIGIBLE</span>"
        if it.get("status") == DRAFT_STATUS_PARTIAL:
            step = html.escape(str(it.get("last_step", "?")))
            name_html += f" <span class='partial-tag'>PARTIAL &mdash; reached step {step}</span>"
        email = str(it.get("client_email", ""))
        agent = str(it.get("agent_email", ""))
        detail_url = f"/?view=admin&key={quote(admin_key)}&id={quote(sid)}"
        rows.append(
            "<tr>"
            f"<td>{html.escape(date_str)}</td>"
            f"<td>{name_html}</td>"
            f"<td>{html.escape(email)}</td>"
            f"<td>{html.escape(agent)}</td>"
            f"<td><a class='detail-link' href='{html.escape(detail_url)}'>Detail</a></td>"
            "</tr>"
        )

    rows_html = "".join(rows) if rows else "<tr><td colspan='5'>No submissions yet.</td></tr>"
    table_html = (
        "<table class='admin-table'><thead><tr>"
        "<th>Date</th><th>Client Name</th><th>Client Email</th><th>Referring Agent</th><th></th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table>"
    )

    fr_panel_html = build_feature_request_panel_html("admin", admin_key=admin_key)
    content = f"{fr_panel_html}<h1>Client Engagement Form Submissions</h1>{table_html}"
    return response_html(wrap_admin_page(content))


def render_admin_detail(submission_id, admin_key):
    try:
        resp = table.get_item(Key={"submission_id": submission_id})
    except ClientError:
        resp = {}
    item = resp.get("Item")
    if not item or item.get("form_type") != FORM_TYPE_CEF_NATURAL:
        return response_html(wrap_admin_page("<h1>Not Found</h1><p>No submission matches that ID.</p>"), 404)

    rows_html = []
    seen = set()
    for key in DETAIL_FIELD_ORDER:
        if key not in item:
            continue
        seen.add(key)
        label = FIELD_LABELS.get(key, key.replace("_", " ").title())
        value_str = format_field_value(item.get(key))
        rows_html.append(
            f"<div class='detail-row'><div class='detail-label'>{html.escape(label)}</div>"
            f"<div class='detail-value'>{html.escape(value_str)}</div></div>"
        )
    for key, value in item.items():
        if key in seen or key in (
            "id_upload_s3_key", "id_upload_status", "eligibility_warning",
            "pdf_broker_s3_key", "pdf_rms_s3_key",
        ):
            continue
        label = FIELD_LABELS.get(key, key.replace("_", " ").title())
        rows_html.append(
            f"<div class='detail-row'><div class='detail-label'>{html.escape(label)}</div>"
            f"<div class='detail-value'>{html.escape(format_field_value(value))}</div></div>"
        )

    upload_html = "<p>No identity document on file.</p>"
    s3_key = item.get("id_upload_s3_key")
    if s3_key:
        try:
            get_url = s3.generate_presigned_url(
                "get_object", Params={"Bucket": BUCKET_NAME, "Key": s3_key}, ExpiresIn=900
            )
            ext = str(s3_key).rsplit(".", 1)[-1].lower()
            if ext in ("pdf", "heic"):
                upload_html = (
                    f"<p><a class='detail-link' href='{html.escape(get_url)}' target='_blank' "
                    f"rel='noopener'>Download Identity Document ({html.escape(ext.upper())})</a></p>"
                )
            else:
                upload_html = f"<img class='id-preview' src='{html.escape(get_url)}' alt='Identity Document'>"
        except ClientError:
            upload_html = "<p>Unable to generate document link.</p>"
    elif item.get("id_upload_status") == ID_UPLOAD_STATUS_DEFERRED:
        upload_html = "<p>ID document: handled via RMS secure transfer</p>"

    warning_html = ""
    if item.get("eligibility_warning"):
        warning_html = f"<div class='eligibility-warning'>&#9888; {html.escape(ELIGIBILITY_WARNING_TEXT)}</div>"

    back_url = f"/?view=admin&key={quote(admin_key)}"
    pdf_link_parts = []
    for pdf_key_field, label in (
        ("pdf_broker_s3_key", "Download PDF (Broker copy)"),
        ("pdf_rms_s3_key", "Download PDF (RMS copy)"),
    ):
        pdf_s3_key = item.get(pdf_key_field)
        if not pdf_s3_key:
            continue
        try:
            pdf_url = s3.generate_presigned_url(
                "get_object", Params={"Bucket": BUCKET_NAME, "Key": pdf_s3_key}, ExpiresIn=900
            )
            pdf_link_parts.append(f"<a class='detail-link' href='{html.escape(pdf_url)}'>{label}</a>")
        except ClientError:
            pass
    pdf_links_html = f"<p>{' &nbsp;|&nbsp; '.join(pdf_link_parts)}</p>" if pdf_link_parts else ""
    content = (
        f"<p><a class='detail-link' href='{html.escape(back_url)}'>&larr; Back to submissions</a></p>"
        "<h1>Submission Detail</h1>"
        f"{pdf_links_html}"
        f"{warning_html}"
        f"<div class='id-section'><h2>Identity Document</h2>{upload_html}</div>"
        f"<div class='detail-grid'>{''.join(rows_html)}</div>"
    )
    return response_html(wrap_admin_page(content))


def handle_admin(params, host):
    admin_key = os.environ.get("ADMIN_KEY", "")
    supplied_key = params.get("key", "")
    if not admin_key or supplied_key != admin_key:
        print("route=admin status=403")
        return response_text("Forbidden", 403)

    submission_id = params.get("id")
    if submission_id:
        print("route=admin_detail status=200")
        return render_admin_detail(submission_id, supplied_key)
    print("route=admin_list status=200")
    return render_admin_list(supplied_key)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    try:
        # Non-HTTP invocation from EventBridge Scheduler: {"action": "sweep"}
        if isinstance(event, dict) and event.get("action") == "sweep" and "requestContext" not in event:
            return handle_sweep()

        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        raw_qs = event.get("rawQueryString", "")
        params = {k: v[0] for k, v in parse_qs(raw_qs).items()}
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        host = headers.get("host", "")

        if method == "GET":
            if params.get("view") == "admin":
                return handle_admin(params, host)
            print("route=form status=200")
            if params.get("notes") == "glen":
                return response_html(FORM_HTML_WITH_GLEN_NOTES)
            return response_html(FORM_HTML)

        if method == "POST":
            body = get_json_body(event)
            action = body.get("action")
            ip = get_client_ip(headers)
            if action == "presign":
                return handle_presign(body)
            if action == "submit":
                return handle_submit(body, ip, host)
            if action == "draft":
                return handle_draft(body, ip)
            if action == "agent_search":
                return handle_agent_search(body, ip)
            if action == "feature_request":
                if "id" in body:
                    return handle_feature_request_toggle(body, host)
                return handle_feature_request_create(body, ip, host)
            if action == "feature_request_list":
                return handle_feature_request_list(body)
            print("route=unknown_action status=400")
            return response_json({"ok": False, "error": "unknown_action"}, 400)

        print(f"route=method_not_allowed status=405")
        return response_json({"ok": False, "error": "method_not_allowed"}, 405)
    except Exception as e:
        print(f"route=error status=500 error={type(e).__name__}")
        return response_json(
            {
                "ok": False,
                "error": "server_error",
                "message": "We hit an unexpected error processing your request. Please try again in a moment.",
            },
            500,
        )
