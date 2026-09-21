import base64
import datetime
import html
import json
import os
import re
import uuid
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
FORM_TYPE_CEF_NATURAL = "cef-natural"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
RATE_LIMIT_PER_HOUR = 5

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

ELIGIBILITY_WARNING_TEXT = "Not eligible for private secondary transactions."

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
    "created_at": "Submitted (UTC)",
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
    "submission_id", "form_type", "created_at", "ip",
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
.not-eligible-tag { color: #c0392b; font-weight: 700; font-size: 11px; margin-left: 6px; white-space: nowrap; }
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


def build_agent_roster_options_html():
    parts = ["<option value=''>Select your Rainmaker Securities Agent&#8230;</option>"]
    for display_name, _email in AGENT_ROSTER:
        esc = html.escape(display_name)
        parts.append(f"<option value='{esc}'>{esc}</option>")
    parts.append("<option value='__none__'>No referring agent</option>")
    parts.append("<option value='__manual__'>My agent is not listed (enter manually)</option>")
    return "".join(parts)


def build_agent_roster_js_map():
    entries = {}
    for display_name, email in AGENT_ROSTER:
        first, last = split_agent_display_name(display_name)
        entries[display_name] = {"first": first, "last": last, "email": email}
    entries["__none__"] = {"first": OPS_FALLBACK_FIRST, "last": OPS_FALLBACK_LAST, "email": OPS_FALLBACK_EMAIL}
    return json.dumps(entries)


NET_WORTH_SELECT_HTML = build_select_options_html(NET_WORTH_OPTIONS, "Select…")
CUMULATIVE_SELECT_HTML = build_select_options_html(NET_WORTH_OPTIONS, "Select…")
ANNUAL_INCOME_SELECT_HTML = build_select_options_html(ANNUAL_INCOME_OPTIONS, "Select…")
OCCUPATION_OPTIONS_HTML = build_select_options_html(OCCUPATION_OPTIONS, "Select occupation…")
INVESTMENT_OBJECTIVES_CHECKS_HTML = build_checkbox_options_html("investment_objectives", INVESTMENT_OBJECTIVE_OPTIONS)
PREVIOUS_INVESTMENT_CHECKS_HTML = build_checkbox_options_html("previous_investment_types", PREVIOUS_INVESTMENT_OPTIONS)
SOPHISTICATION_CHECKS_HTML = build_checkbox_options_html("client_sophistication", SOPHISTICATION_OPTIONS)
COUNTRY_OPTIONS_HTML = build_country_options_html()
AGENT_ROSTER_OPTIONS_HTML = build_agent_roster_options_html()
AGENT_ROSTER_JS_MAP = build_agent_roster_js_map()
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
            <label class="field-label" for="agent_roster">Who is the Rainmaker Securities Agent that referred the Client to this form?</label>
            <select id="agent_roster">__AGENT_ROSTER_OPTIONS__</select>
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

        <div class="field">
          <label class="field-label">Identity Verification Upload</label>
          <p class="helper-text">Upload a government issued photo ID of the Client. If this cannot be obtained, upload a text narrative that documents the circumstances of the Client's refusal, neglect, or inability to provide the requested document. Note that if we cannot verify a Client's identity in some manner, we cannot legally transact with the Client.</p>
          <button type="button" class="btn" id="upload-id-btn">Upload ID Document</button>
          <div class="id-upload-notice" id="id-upload-notice">
            <span class="id-upload-notice-icon">&#128274;</span>
            <span><strong>Identity verification is required by federal law.</strong> Under the USA PATRIOT Act and U.S. Treasury regulations (31 CFR &sect;1023.220), Rainmaker Securities must verify each client's identity against a government-issued photo ID before any transaction. Your document is encrypted in your browser before it leaves this page and can be opened only by Rainmaker Securities compliance. It is stored with AES-256 encryption in accordance with SEC Regulation S-P (the Safeguards Rule), and it is never sent by email &mdash; not to your referring agent, not to anyone.</span>
          </div>
        </div>

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
          <label class="field-label" for="date_of_birth">Date of Birth</label>
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
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="retiring_five_years">
          <label class="field-label">Is the Client planning to permanently retire within the next five years?</label>
          __RETIRING_RADIOS__
        </div>

        <div class="field" data-field="net_worth">
          <label class="field-label" for="net_worth">Net Worth Excluding Primary Residence</label>
          <select id="net_worth" name="net_worth">__NET_WORTH_SELECT__</select>
          <div class="eligibility-warning" id="net_worth-warning" style="display:none;">Not eligible for private secondary transactions.</div>
          <div class="field-error"></div>
        </div>

        <div class="field" data-field="cumulative_investments">
          <label class="field-label" for="cumulative_investments">Cumulative Amount of Investments</label>
          <select id="cumulative_investments" name="cumulative_investments">__CUMULATIVE_SELECT__</select>
          <div class="eligibility-warning" id="cumulative_investments-warning" style="display:none;">Not eligible for private secondary transactions.</div>
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
          <input type="number" id="years_experience" name="years_experience" min="0" max="80">
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
  </div>

</div>

<script>
(function () {
  "use strict";

  var AGENT_ROSTER_MAP = __AGENT_ROSTER_JS_MAP__;

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
    if (rosterVisible) {
      if (!qs("#agent_roster").value) { showFieldError("agent_roster", "Select your Rainmaker Securities Agent."); ok = false; }
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

  qs("#next-btn").addEventListener("click", function () {
    var ok = currentStep === 1 ? validateStep1() : (currentStep === 2 ? validateStep2() : true);
    if (!ok) { focusAndScroll(firstInvalidField()); return; }
    goToStep(currentStep + 1);
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

  qs("#submit-btn").addEventListener("click", function () {
    if (!validateStep3()) { focusAndScroll(firstInvalidField()); return; }
    var btn = qs("#submit-btn");
    btn.disabled = true;
    btn.textContent = "Submitting...";
    var payload = collectFormData();
    payload.action = "submit";
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
          window.scrollTo({ top: 0, behavior: "smooth" });
        } else if (res.status === 429) {
          alert(res.body.message || "Too many submissions. Please try again later.");
          btn.disabled = false;
          btn.textContent = "Submit";
        } else if (res.body && res.body.errors) {
          clearAllErrors();
          var firstField = null;
          Object.keys(res.body.errors).forEach(function (f) {
            if (!firstField) firstField = f;
            showFieldError(f, res.body.errors[f]);
          });
          if (firstField) { focusAndScroll(firstField); }
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

    // Raw link (no ?agent=): show the roster dropdown, hide the free-text
    // fields until "My agent is not listed" is chosen.
    var rosterWrap = qs("#agent-roster-wrap");
    var manualNames = qs("#agent-manual-names");
    var emailWrap = qs("#agent-email-wrap");
    rosterWrap.style.display = "block";
    manualNames.style.display = "none";
    emailWrap.style.display = "none";

    qs("#agent_roster").addEventListener("change", function () {
      var value = this.value;
      clearFieldError("agent_roster");
      if (value === "__manual__") {
        manualNames.style.display = "flex";
        emailWrap.style.display = "block";
        qs("#agent_first_name").value = "";
        qs("#agent_last_name").value = "";
        qs("#agent_email").value = "";
        clearFieldError("agent_first_name");
        clearFieldError("agent_last_name");
        clearFieldError("agent_email");
      } else {
        manualNames.style.display = "none";
        emailWrap.style.display = "none";
        var resolved = AGENT_ROSTER_MAP[value];
        qs("#agent_first_name").value = resolved ? resolved.first : "";
        qs("#agent_last_name").value = resolved ? resolved.last : "";
        qs("#agent_email").value = resolved ? resolved.email : "";
      }
    });
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

  function autofillFromZip(zip, cfg) {
    var hasAbort = typeof AbortController !== "undefined";
    var controller = hasAbort ? new AbortController() : null;
    var timeoutId = setTimeout(function () { if (controller) controller.abort(); }, 3000);
    fetch("https://api.zippopotam.us/us/" + zip, controller ? { signal: controller.signal } : {})
      .then(function (r) { if (!r.ok) throw new Error("lookup failed"); return r.json(); })
      .then(function (data) {
        clearTimeout(timeoutId);
        var place = data && data.places && data.places[0];
        if (!place) return;
        if (place["place name"] && cfg.cityId) {
          var cityEl = qs("#" + cfg.cityId);
          if (cityEl) { cityEl.value = place["place name"]; clearFieldError(cfg.cityId); }
        }
        if (place["state"]) {
          var stateEl = qs("#" + cfg.stateId);
          if (stateEl) { stateEl.value = place["state"]; clearFieldError(cfg.stateId); }
        }
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
    });

    if (cfg.zipId) {
      var zipDebounce = null;
      qs("#" + cfg.zipId).addEventListener("input", function () {
        if (!isUSCountry(qs("#" + cfg.countryId).value)) return;
        var val = qs("#" + cfg.zipId).value.trim();
        if (!/^\d{5}$/.test(val)) return;
        if (zipDebounce) { clearTimeout(zipDebounce); }
        zipDebounce = setTimeout(function () { autofillFromZip(val, cfg); }, 300);
      });
    }

    rebuildState();
    updateZipVisibility();
  }

  setupAddressBlock({
    countryId: "address_country", stateId: "address_state", zipId: "address_zip",
    zipHelperId: "zip-helper", cityId: "address_city", hideZipWhenNonUS: false
  });
  setupAddressBlock({
    countryId: "employer_country", stateId: "employer_state", zipId: "employer_zip",
    zipWrapId: "employer_zip_wrap", zipHelperId: "employer-zip-helper", cityId: "employer_city",
    hideZipWhenNonUS: true
  });

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
    page = page.replace("__AGENT_ROSTER_OPTIONS__", AGENT_ROSTER_OPTIONS_HTML)
    page = page.replace("__AGENT_ROSTER_JS_MAP__", AGENT_ROSTER_JS_MAP)
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
    2: "Glen: Two paths, both foolproof: each agent gets a personal link that pre-fills and locks their name — and if a raw link circulates, the client picks the agent from the official roster instead of typing. Either way, every submission arrives correctly attributed. No more blank, misspelled, or unknown agent fields.",
    3: "Glen: The text at left describes the end state; the upload itself is switched off until RMS confirms where these documents should live. The design is worth the wait: the ID gets encrypted in the client's browser with an RMS-held key, so it lands in storage as ciphertext that only RMS compliance can open — I can't see it, and no other agent can either. My recommendation: we stop emailing ID copies to referring brokers entirely, as happens today. The broker receives the engagement form data; the ID goes to RMS only. Fewer copies of passports sitting in fewer inboxes.",
    4: "Glen: Country now comes first and drives the rest: US clients get a proper state dropdown, the form pre-populates city and state from the zip code, and zip code errors are disallowed at entry. Bad addresses can no longer reach us.",
    5: "Glen: Every identification field is now required and format-checked before the client can advance — fewer incomplete forms, fewer repeat requests back to the client.",
    6: "Glen: Occupation is now a standardized dropdown with an Other option. Today we get free text — real examples from our files: 'VC', 'Real Estate', 'N/A' — which makes the data inconsistent and hard to use.",
    7: "Glen: It's not possible to have more than one net worth, so I switched these from check-all-that-apply to single-choice dropdowns. Same for annual income. One clean answer per question.",
    8: "Glen: If a client selects NONE OF THE ABOVE for net worth or investments, they see an immediate eligibility warning and the submission is flagged in the admin view — we catch unqualified clients at the door instead of after the paperwork.",
    9: "Glen: Everything on this form is validated the moment it's typed, and the whole thing is re-checked on our server before it's stored — so what lands in the database is complete, consistent, and correctly attributed on the first pass.",
}

# Each entry: (note number, unique anchor substring already present in
# FORM_HTML, "before"/"after" the anchor). Anchors are existing markup, not
# markers added to the template, so the base page never carries any trace.
GLEN_NOTE_ANCHORS = [
    (1, '<ol class="instructions-list">', "before"),
    (2, '<div id="referring-agent-section">', "before"),
    (3, '<div class="field">\n          <label class="field-label">Identity Verification Upload</label>', "before"),
    (4, '<h3>Address of Client</h3>', "after"),
    (5, '<div class="two-col">\n          <div class="field" data-field="client_phone">', "before"),
    (6, '<div class="field" data-field="occupation">', "before"),
    (7, '<div class="field" data-field="net_worth">', "before"),
    (8, '<div class="field" data-field="cumulative_investments">', "before"),
    (9, '<div class="field" data-field="attestation">', "before"),
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
}
"""


def build_glen_note_html(note_number):
    text = html.escape(GLEN_NOTE_TEXTS[note_number])
    return (
        f"<div class='glen-note' data-glen-note='{note_number}'>"
        f"<span class='glen-note-label'>GLEN &mdash;</span>{text}"
        f"</div>"
    )


def build_form_html_with_glen_notes():
    page = FORM_HTML
    for number, anchor, position in GLEN_NOTE_ANCHORS:
        if page.count(anchor) != 1:
            continue
        note_html = build_glen_note_html(number)
        replacement = (note_html + anchor) if position == "before" else (anchor + note_html)
        page = page.replace(anchor, replacement, 1)
    if "</style>" in page:
        page = page.replace("</style>", GLEN_NOTES_CSS + "</style>", 1)
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

    employer_country = None
    if not is_blank(raw.get("employer_country")):
        employer_country = str(raw.get("employer_country")).strip()
        data["employer_country"] = employer_country

    opt_text("employer_city")

    employer_state = str(raw.get("employer_state", "")).strip()
    if employer_state:
        if employer_country == "United States" and employer_state not in US_STATES:
            errors["employer_state"] = "Select a valid US state."
        else:
            data["employer_state"] = employer_state

    if employer_country == "United States":
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


# ---------------------------------------------------------------------------
# Notification email
# ---------------------------------------------------------------------------

def send_notification_email(agent_email, item, submission_id, host):
    if not agent_email or not agent_email.lower().endswith(AGENT_EMAIL_DOMAIN):
        return
    try:
        admin_key = os.environ.get("ADMIN_KEY", "")
        admin_link = f"https://{host}/?view=admin&key={admin_key}&id={submission_id}"
        subject = f"New Client Engagement Form: {item.get('client_first_name', '')} {item.get('client_last_name', '')}"
        body_text = (
            "A new Client Engagement Form has been submitted.\n\n"
            f"Client Name: {item.get('client_first_name', '')} {item.get('client_last_name', '')}\n"
            f"Client Email: {item.get('client_email', '')}\n"
            f"Client Phone: {item.get('client_phone', '')}\n"
            f"Referring Agent: {item.get('agent_first_name', '')} {item.get('agent_last_name', '')}\n"
            f"Submitted (UTC): {item.get('created_at', '')}\n\n"
            "View full submission:\n"
            f"{admin_link}\n"
        )
        ses.send_email(
            Source=SES_SENDER,
            Destination={"ToAddresses": [agent_email]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body_text}}},
            ReplyToAddresses=[SES_REPLY_TO],
        )
    except Exception:
        print(f"route=submit status=email_failed submission_id={submission_id}")


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

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
                "message": "Too many submissions have been received from this connection. Please try again later.",
            },
            429,
        )

    errors, data = validate_submission(body)
    if errors:
        print("route=submit status=400")
        return response_json({"ok": False, "errors": errors}, 400)

    submission_id = str(uuid.uuid4())
    created_at = datetime.datetime.utcnow().isoformat() + "Z"
    item = {
        "submission_id": submission_id,
        "form_type": FORM_TYPE_CEF_NATURAL,
        "created_at": created_at,
        "ip": ip,
    }
    item.update(data)

    try:
        table.put_item(Item=item)
    except ClientError:
        print(f"route=submit status=500 submission_id={submission_id}")
        return response_json({"ok": False, "error": "storage_failed"}, 500)

    send_notification_email(data.get("agent_email", ""), item, submission_id, host)

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

    content = f"<h1>Client Engagement Form Submissions</h1>{table_html}"
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
        if key in seen or key in ("id_upload_s3_key", "id_upload_status", "eligibility_warning"):
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
    content = (
        f"<p><a class='detail-link' href='{html.escape(back_url)}'>&larr; Back to submissions</a></p>"
        "<h1>Submission Detail</h1>"
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
            print("route=unknown_action status=400")
            return response_json({"ok": False, "error": "unknown_action"}, 400)

        print(f"route=method_not_allowed status=405")
        return response_json({"ok": False, "error": "method_not_allowed"}, 405)
    except Exception as e:
        print(f"route=error status=500 error={type(e).__name__}")
        return response_json({"ok": False, "error": "server_error"}, 500)
