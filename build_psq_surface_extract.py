#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse


TAG_RE = re.compile(r"<(?:[^<>\"']|\"[^\"]*\"|'[^']*')+>", re.S)
WHITESPACE_RE = re.compile(r"\s+")
ATTR_FRAGMENT = r"""(?:[^>"']|"[^"]*"|'[^']*')*"""
LEGEND_RE = re.compile(r"<legend[^>]*>(.*?)</legend>", re.S | re.I)
HELPINFO_RE = re.compile(
    rf'class="[^"]*\bhelpinfo\b[^"]*"{ATTR_FRAGMENT}data-title="([^"]*)"{ATTR_FRAGMENT}data-content="([^"]*)"',
    re.S | re.I,
)
TR_RE = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
LABEL_RE = re.compile(r"<label(?P<attrs>[^>]*)>(?P<body>.*?)</label>", re.S | re.I)
DEREG_LABEL_RE = re.compile(
    rf'<label(?P<attrs>{ATTR_FRAGMENT})for="(?P<field>cxDeRegistration_[^"]+)"{ATTR_FRAGMENT}>(?P<body>.*?)</label>',
    re.S | re.I,
)
HIDDEN_ROW_RE = re.compile(r"<s-row(?P<attrs>[^>]*)>", re.I)
WARNING_RE = re.compile(r"<strong>Warning:</strong>\s*(.*?)</p>", re.S | re.I)
EXPORT_FORM_RE = re.compile(r"location\.href='([^']*/Home/ExportForm[^']*)'", re.I)
ATTACHMENT_RE = re.compile(
    r'<a href="([^"]*/GoogleDrive/DownloadDriveFile[^"]*)"[^>]*class="doc-name"',
    re.I,
)
JURISDICTION_ID_RE = re.compile(r"JurisdictionId\"[^>]*value=\"([^\"]+)\"", re.I)
JURISDICTION_NAME_RE = re.compile(r"JurisdictionName\"[^>]*value=\"([^\"]+)\"", re.I)
FIELD_NAME_RE = re.compile(r'AccountInformationModel\[\d+\]\.(\w+)')
JS_ENDPOINT_RE = re.compile(r"/Questionnaire/[A-Za-z0-9?=]+")


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    value = value.replace("\xa0", " ")
    return WHITESPACE_RE.sub(" ", value).strip()


def dedupe_preserve(values: Iterable[str]) -> list[str]:
    items = OrderedDict()
    for value in values:
        if value:
            items[value] = None
    return list(items.keys())


def parse_attr(attrs: str, name: str) -> str | None:
    match = re.search(rf'{re.escape(name)}="([^"]*)"', attrs, re.I)
    if not match:
        return None
    return html.unescape(match.group(1))


def load_project_metadata(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "customer_project_id": payload.get("CustomerProjectId"),
        "financial_force_project_id": payload.get("FinancialForceProjectId"),
        "stage": payload.get("Stage"),
    }


def extract_general_sections(text: str) -> list[str]:
    sections = []
    for match in LEGEND_RE.finditer(text):
        section = clean_text(match.group(1))
        if section:
            sections.append(section)
    return dedupe_preserve(sections)


def extract_help_items(text: str, source_name: str) -> list[dict[str, str]]:
    items = []
    for match in HELPINFO_RE.finditer(text):
        title = clean_text(match.group(1))
        content = clean_text(match.group(2))
        if not title and not content:
            continue
        items.append(
            {
                "source": source_name,
                "title": title,
                "content": content,
            }
        )
    return items


def extract_state_requirements(text: str) -> list[dict[str, object]]:
    requirements = []
    for match in TR_RE.finditer(text):
        row = match.group(1)
        if "AccountInformationModel" not in row or "JurisdictionId" not in row:
            continue

        state = None
        label_entries = []
        for label_match in LABEL_RE.finditer(row):
            label_text = clean_text(label_match.group("body"))
            if label_text:
                label_entries.append(label_text)

        if label_entries:
            state = label_entries[0]

        jurisdiction_id = None
        jurisdiction_match = JURISDICTION_ID_RE.search(row)
        if jurisdiction_match:
            jurisdiction_id = jurisdiction_match.group(1)

        jurisdiction_name = None
        jurisdiction_name_match = JURISDICTION_NAME_RE.search(row)
        if jurisdiction_name_match:
            jurisdiction_name = jurisdiction_name_match.group(1)

        field_names = set(FIELD_NAME_RE.findall(row))
        required_fields = []
        if "AccountNumber" in field_names:
            required_fields.append("Account Number")
        if "UserName" in field_names:
            required_fields.append("User Name")
        if "PlainPasswordHash" in row or "PasswordHash" in field_names:
            required_fields.append("Password")
        if "SignInId" in field_names:
            required_fields.append("Sign In ID")
        if "AccessCode" in field_names:
            required_fields.append("Access Code")
        if "BusinessPartnerNumber" in field_names:
            required_fields.append("Business Partner Number")
        if "CertificateNumber" in field_names:
            required_fields.append("Certificate Number")
        if "Pin" in field_names:
            required_fields.append("PIN")
        if "TaxpayerId" in field_names:
            required_fields.append("Taxpayer ID")

        visible_extra_labels = label_entries[1:]
        for label in visible_extra_labels:
            if label not in required_fields and label != state:
                required_fields.append(label)

        requirements.append(
            {
                "state": state or jurisdiction_name,
                "jurisdiction_id": jurisdiction_id,
                "required_fields": dedupe_preserve(required_fields),
                "has_account_number": "Account Number" in required_fields,
                "has_username": "User Name" in required_fields,
                "has_password": "Password" in required_fields,
            }
        )
    return requirements


def infer_hidden_by_default(full_text: str, match_start: int) -> bool:
    row_start = full_text.rfind("<s-row", 0, match_start)
    if row_start == -1:
        return False
    row_tag_end = full_text.find(">", row_start)
    if row_tag_end == -1:
        return False
    row_tag = full_text[row_start:row_tag_end]
    return "hidden" in row_tag.lower()


def extract_jid_state_map(requirements: list[dict[str, object]]) -> dict[str, str]:
    mapping = {}
    for row in requirements:
        jurisdiction_id = row.get("jurisdiction_id")
        state = row.get("state")
        if jurisdiction_id and state:
            mapping[str(jurisdiction_id)] = str(state)
    return mapping


def parse_export_form_states(paths: list[str], jid_to_state: dict[str, str]) -> list[str]:
    states = []
    for path in paths:
        parsed = urlparse(path)
        params = parse_qs(parsed.query)
        for jid in params.get("jId", []):
            state = jid_to_state.get(jid)
            if state:
                states.append(state)
    return dedupe_preserve(states)


def extract_dereg_questions(text: str, jid_to_state: dict[str, str]) -> list[dict[str, object]]:
    questions = []
    matches = list(DEREG_LABEL_RE.finditer(text))
    for order, match in enumerate(matches, start=1):
        attrs = match.group("attrs")
        next_start = matches[order].start() if order < len(matches) else len(text)
        window = text[match.start():next_start]
        help_text = clean_text(parse_attr(attrs, "data-content") or "")
        export_paths = dedupe_preserve(EXPORT_FORM_RE.findall(window))
        warnings = dedupe_preserve(clean_text(warning) for warning in WARNING_RE.findall(window))
        linked_attachment = bool(ATTACHMENT_RE.search(window))

        field_name = match.group("field").removeprefix("cxDeRegistration_")
        prompt = clean_text(match.group("body"))

        questions.append(
            {
                "order": order,
                "field_name": field_name,
                "prompt": prompt,
                "hidden_by_default": infer_hidden_by_default(text, match.start()),
                "help_text": help_text or None,
                "warning_texts": warnings,
                "download_paths": export_paths,
                "download_states": parse_export_form_states(export_paths, jid_to_state),
                "has_uploaded_attachment": linked_attachment,
            }
        )
    return questions


def first_match_line(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.M)
    if not match:
        return None
    return text.count("\n", 0, match.start()) + 1


def build_js_findings(js_sources: dict[str, str]) -> dict[str, object]:
    joined = "\n".join(js_sources.values())
    masked_endpoints = dedupe_preserve(JS_ENDPOINT_RE.findall(joined))

    rules = []
    candidates = [
        (
            "q1_checkbox_controls_q47_per_state",
            "Q1 checkboxes toggle Q1 date collection and per-state Q47 blocks using state abbreviations.",
            r"\.chk_Q1",
            "/tmp/psq-genInfo.js",
        ),
        (
            "project_type_6_changes_q47_behavior",
            "Project type 6 adds/removes Q47 and renumbers later questions when the related yes/no answer changes.",
            r"ProjectTypeId'\)\.val\(\) == \"6\"",
            "/tmp/psq-genInfo.js",
        ),
        (
            "q48_ohio_special_case",
            "Q48 has an Ohio-specific branch that forces Ohio visibility/selection even when other states are hidden.",
            r"stateAbbrev == \"OH\"",
            "/tmp/psq-genInfo.js",
        ),
        (
            "llc_tax_purpose_branching",
            "Legal entity type drives LLC tax-purpose options and related federal-tax sections.",
            r"case \"6\":|case \"7\":",
            "/tmp/psq-genInfo.js",
        ),
        (
            "second_owner_branching",
            "Second-owner help/visibility depends on partnership-style entity types and one LLC tax-purpose path.",
            r"toggle2ndOwnerHelpInfo",
            "/tmp/psq-genInfo.js",
        ),
        (
            "business_location_subquestions",
            "Business-location subquestions are shown per jurisdiction via dynamic business-location blocks.",
            r"bussiness_loc",
            "/tmp/psq-genInfo.js",
        ),
        (
            "masked_value_fetch_endpoints",
            "Preview mode can fetch masked field values and masked registration credentials through authenticated questionnaire endpoints.",
            r"GetMasked(Field|RegField)Value",
            "/tmp/psq-enablePreviewInputs.js",
        ),
        (
            "owner_passport_driver_license_branching",
            "Owner identity fields switch between passport and driver-license sections, with extra rules for owner two.",
            r"owner1PassportDLSelection|owner2PassportDLSelection",
            "/tmp/psq-ownerCompanyInfo.js",
        ),
    ]

    for rule_id, description, pattern, source_file in candidates:
        source_text = js_sources.get(source_file)
        if not source_text or not re.search(pattern, source_text, re.M):
            continue
        line = first_match_line(source_text, pattern)
        rules.append(
            {
                "rule_id": rule_id,
                "description": description,
                "source_file": source_file,
                "line": line,
            }
        )

    return {
        "masked_endpoints": masked_endpoints,
        "conditional_rules": rules,
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def write_guidance_csv(path: Path, project_id: str, questions: list[dict[str, object]]) -> None:
    fieldnames = [
        "project_id",
        "question_order",
        "field_name",
        "prompt",
        "question_type",
        "hidden_by_default",
        "download_states",
        "download_paths",
        "help_text",
        "warning_texts",
        "has_uploaded_attachment",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in questions:
            writer.writerow(
                {
                    "project_id": project_id,
                    "question_order": item["order"],
                    "field_name": item["field_name"],
                    "prompt": item["prompt"],
                    "question_type": "conditional" if item["hidden_by_default"] else "visible",
                    "hidden_by_default": item["hidden_by_default"],
                    "download_states": " | ".join(item["download_states"]),
                    "download_paths": " | ".join(item["download_paths"]),
                    "help_text": item["help_text"] or "",
                    "warning_texts": " | ".join(item["warning_texts"]),
                    "has_uploaded_attachment": item["has_uploaded_attachment"],
                }
            )


def write_state_requirements_csv(path: Path, project_id: str, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "project_id",
        "state",
        "jurisdiction_id",
        "required_fields",
        "has_account_number",
        "has_username",
        "has_password",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "project_id": project_id,
                    "state": row["state"],
                    "jurisdiction_id": row["jurisdiction_id"],
                    "required_fields": " | ".join(row["required_fields"]),
                    "has_account_number": row["has_account_number"],
                    "has_username": row["has_username"],
                    "has_password": row["has_password"],
                }
            )


def write_js_rules_csv(path: Path, rules: list[dict[str, object]]) -> None:
    fieldnames = ["rule_id", "description", "source_file", "line"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rule in rules:
            writer.writerow(rule)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a sanitized PSQ extract from captured Avalara questionnaire artifacts."
    )
    parser.add_argument("--project-json", type=Path, required=True)
    parser.add_argument("--general-html", type=Path, required=True)
    parser.add_argument("--dereg-html", type=Path, required=True)
    parser.add_argument("--geninfo-js", type=Path, required=True)
    parser.add_argument("--owner-js", type=Path, required=True)
    parser.add_argument("--preview-js", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    project_meta = load_project_metadata(args.project_json)
    general_html = args.general_html.read_text()
    dereg_html = args.dereg_html.read_text()
    js_sources = {
        str(args.geninfo_js): args.geninfo_js.read_text(),
        str(args.owner_js): args.owner_js.read_text(),
        str(args.preview_js): args.preview_js.read_text(),
    }

    state_requirements = extract_state_requirements(dereg_html)
    jid_to_state = extract_jid_state_map(state_requirements)
    guidance_questions = extract_dereg_questions(dereg_html, jid_to_state)
    general_sections = extract_general_sections(general_html)
    general_help = extract_help_items(general_html, "general_information")
    dereg_help = extract_help_items(dereg_html, "de_registration")
    js_findings = build_js_findings(js_sources)

    project_id = str(project_meta.get("customer_project_id") or "unknown-project")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sanitized": True,
        "source_files": {
            "project_json": str(args.project_json),
            "general_html": str(args.general_html),
            "dereg_html": str(args.dereg_html),
            "geninfo_js": str(args.geninfo_js),
            "owner_js": str(args.owner_js),
            "preview_js": str(args.preview_js),
        },
        "project": project_meta,
        "general_information": {
            "sections": general_sections,
            "help_items": general_help,
        },
        "de_registration": {
            "question_records": guidance_questions,
            "help_items": dereg_help,
            "state_requirements": state_requirements,
        },
        "js_findings": js_findings,
    }

    write_json(output_dir / f"psq-project-{project_id}-sanitized.json", payload)
    write_guidance_csv(output_dir / f"psq-project-{project_id}-guidance.csv", project_id, guidance_questions)
    write_state_requirements_csv(
        output_dir / f"psq-project-{project_id}-state-requirements.csv",
        project_id,
        state_requirements,
    )
    write_js_rules_csv(
        output_dir / f"psq-project-{project_id}-js-rules.csv",
        js_findings["conditional_rules"],
    )

    summary = {
        "project_id": project_id,
        "general_section_count": len(general_sections),
        "general_help_count": len(general_help),
        "dereg_question_count": len(guidance_questions),
        "dereg_help_count": len(dereg_help),
        "state_requirement_count": len(state_requirements),
        "js_rule_count": len(js_findings["conditional_rules"]),
        "masked_endpoint_count": len(js_findings["masked_endpoints"]),
    }
    write_json(output_dir / "psq-surface-summary.json", summary)


if __name__ == "__main__":
    main()
