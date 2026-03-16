#!/usr/bin/env python3

import argparse
import csv
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple


BASE_HEADERS = {
    "X-Ava-App": "true",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://app.avalara.com",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0",
}

REG_STATUSES_URL = (
    "https://www.businesslicenses.com/filingassist/api/orders/"
    "regStatuses?page={page}&perPage={per_page}&filter=all&search="
)
DELIVERABLE_URL = (
    "https://www.businesslicenses.com/filingassist/api/registration/"
    "{registration_id}/deliverable"
)

BLOCK_TAG_RE = re.compile(r"</?(?:p|div|li|ul|ol|h[1-6]|br)\b[^>]*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
LABEL_LINE_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9 #&()/.,'_-]{0,120}?):\s*(.+?)\s*$")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Avalara registration statuses and deliverables to CSV."
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("AVALARA_REGISTRATIONS_TOKEN"),
        help="Bearer token for the businesslicenses.com API. "
        "Defaults to AVALARA_REGISTRATIONS_TOKEN.",
    )
    parser.add_argument(
        "--cutoff-date",
        default="2025-10-01",
        help="Inclusive cutoff for the migration-focused CSV in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--output-prefix",
        default="registration-statuses",
        help="Prefix for generated files.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=100,
        help="Rows per page when pulling the statuses API.",
    )
    return parser.parse_args()


def api_get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={**BASE_HEADERS, "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def progress_key(row: Dict[str, str]) -> str:
    return "|".join(
        (
            str(row.get("registration_id") or ""),
            str(row.get("order_number") or ""),
            str(row.get("jurisdiction") or ""),
            str(row.get("registration_type") or ""),
            str(row.get("date_added") or ""),
        )
    )


def load_existing_progress(path: str) -> Dict[str, Dict[str, str]]:
    if not os.path.exists(path):
        return {}

    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    progress: Dict[str, Dict[str, str]] = {}
    for row in rows:
        progress[progress_key(row)] = {
            "ai_completion": row.get("ai_completion", ""),
            "ai_notes": row.get("ai_notes", ""),
        }
    return progress


def fetch_status_rows(token: str, per_page: int) -> List[dict]:
    page = 1
    rows: List[dict] = []

    while True:
        payload = api_get_json(REG_STATUSES_URL.format(page=page, per_page=per_page), token)
        statuses = payload["reg_statuses"]
        rows.extend(statuses["data"])
        if page >= statuses["last_page"]:
            break
        page += 1

    return rows


def fetch_deliverable(
    token: str, registration_id: Optional[int]
) -> Tuple[Optional[dict], Optional[str]]:
    if not registration_id:
        return None, None

    url = DELIVERABLE_URL.format(registration_id=registration_id)
    try:
        return api_get_json(url, token), None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = body[:500].strip()
        error = f"HTTP {exc.code}"
        if detail:
            error = f"{error}: {detail}"
        return None, error
    except Exception as exc:  # pragma: no cover - defensive for transient network issues
        return None, str(exc)


def html_to_text(html_content: Optional[str]) -> str:
    if not html_content:
        return ""
    text = BLOCK_TAG_RE.sub("\n", html_content)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = []
    for raw_line in text.splitlines():
        normalized = re.sub(r"\s+", " ", raw_line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def extract_urls(html_content: Optional[str]) -> List[str]:
    if not html_content:
        return []
    urls: List[str] = []
    for match in HREF_RE.findall(html_content):
        if match not in urls:
            urls.append(html.unescape(match))
    return urls


def normalize_label(label: str) -> str:
    normalized = NON_ALNUM_RE.sub("_", label.lower()).strip("_")
    return normalized[:120]


def extract_labeled_fields(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    if not text:
        return fields

    for line in text.splitlines():
        match = LABEL_LINE_RE.match(line)
        if not match:
            continue
        label = match.group(1).strip()
        value = match.group(2).strip()
        if not value:
            continue
        if label in fields and value not in fields[label].split(" | "):
            fields[label] = f"{fields[label]} | {value}"
        elif label not in fields:
            fields[label] = value
    return fields


def field_value(fields: Dict[str, str], *candidates: str) -> str:
    lowered = {normalize_label(k): v for k, v in fields.items()}
    for candidate in candidates:
        key = normalize_label(candidate)
        if key in lowered:
            return lowered[key]
    return ""


def pick_state_portal_url(urls: List[str]) -> str:
    for url in urls:
        host = urllib.parse.urlparse(url).netloc.lower()
        if not host:
            continue
        if any(
            excluded in host
            for excluded in (
                "avalara.com",
                "businesslicenses.com",
            )
        ):
            continue
        return url
    return ""


def summarize_confirmation_fields(
    fields: Optional[List[dict]],
) -> Tuple[str, Dict[str, str]]:
    if not fields:
        return "", {}

    normalized: Dict[str, str] = {}
    parts: List[str] = []

    for item in fields:
        if not isinstance(item, dict):
            continue
        key = ""
        value = ""
        for key_name in ("label", "name", "field", "title"):
            if item.get(key_name):
                key = str(item[key_name]).strip()
                break
        for value_name in ("value", "answer", "displayValue", "text"):
            if item.get(value_name) is not None:
                value = str(item[value_name]).strip()
                break
        if key:
            normalized[key] = value
            parts.append(f"{key}={value}")
        else:
            parts.append(json.dumps(item, sort_keys=True))

    return " | ".join(parts), normalized


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def make_export_row(row: dict, deliverable: Optional[dict], cutoff: date) -> Dict[str, str]:
    confirmation_summary, confirmation_map = summarize_confirmation_fields(
        row.get("confirmation_fields")
    )

    deliverable_html = ""
    attachment_records: List[dict] = []
    deliverable_error = ""
    if deliverable:
        deliverable_html = deliverable.get("deliverable_content") or ""
        for attachment in deliverable.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            attachment_records.append(
                {
                    "id": attachment.get("id"),
                    "name": attachment.get("name"),
                    "file_extension": attachment.get("file_extension"),
                    "created_at": attachment.get("created_at"),
                    "src": attachment.get("src"),
                }
            )

    text = html_to_text(deliverable_html)
    labeled_fields = extract_labeled_fields(text)
    urls = extract_urls(deliverable_html)
    state_portal_url = pick_state_portal_url(urls)
    created_at = parse_iso_datetime(row.get("date_added"))
    created_on = created_at.date().isoformat() if created_at else ""
    is_focus = bool(created_at and created_at.date() >= cutoff)

    merged_fields = dict(labeled_fields)
    for key, value in confirmation_map.items():
        merged_fields.setdefault(key, value)

    license_number = field_value(
        merged_fields,
        "License Number",
        "Permit Number",
        "Seller's Permit Number",
    )
    account_number = field_value(
        merged_fields,
        "Account Number",
        "Tax Account Number",
        "Sales Tax Account Number",
        "Registration Number",
    )
    registration_number = field_value(
        merged_fields,
        "Registration Number",
        "Registration ID",
        "Registration Number/ID",
    )
    business_number = field_value(
        merged_fields,
        "Business Number",
        "BN",
        "Canada Business Number",
    )
    confirmation_number = field_value(
        merged_fields,
        "Confirmation Number",
        "Confirmation Code",
        "Application Number",
        "Reference Number",
        "Tracking Number",
    )
    username = field_value(merged_fields, "Username", "User Name", "User ID", "Userid")
    password = field_value(merged_fields, "Password", "Passcode")
    secret_question = field_value(merged_fields, "Secret Question", "Security Question")
    secret_answer = field_value(merged_fields, "Secret Answer", "Security Answer")
    ein_or_tax_id = field_value(
        merged_fields,
        "Federal Tax ID",
        "Federal Tax ID Number",
        "EIN",
        "FEIN",
        "Tax ID",
    )

    combined_text = "\n".join(part for part in (text, json.dumps(merged_fields)) if part)

    attachment_names = [item.get("name", "") for item in attachment_records]
    attachment_urls = [item.get("src", "") for item in attachment_records]

    return {
        "ai_completion": "",
        "ai_notes": "",
        "migration_focus_guess": "true" if is_focus else "false",
        "migration_focus_basis": (
            f"date_added>={cutoff.isoformat()}" if is_focus else f"date_added<{cutoff.isoformat()}"
        ),
        "order_number": str(row.get("order_number") or ""),
        "date_added": str(row.get("date_added") or ""),
        "date_added_date": created_on,
        "date_in_process": str(row.get("date_in_process") or ""),
        "sub_package_created_date": str(row.get("sub_package_created_date") or ""),
        "registration_created_date": str(row.get("registration_created_date") or ""),
        "status_updated_at": str(row.get("status_updated_at") or ""),
        "jurisdiction": str(row.get("state") or ""),
        "registration_type": str(row.get("registration_name") or ""),
        "sku": str(row.get("sku") or ""),
        "status": str(row.get("status") or ""),
        "status_description": str(row.get("sub_package_status") or ""),
        "registration_status": str(row.get("registration_status") or ""),
        "days_to_confirmation": str(row.get("days_to_confirmation") or ""),
        "show_eta": str(row.get("show_eta") or ""),
        "eta_days": str(row.get("eta_days") or ""),
        "expired": str(row.get("expired") or ""),
        "no_access": str(row.get("no_access") or ""),
        "no_deliverable_access": str(row.get("no_deliverable_access") or ""),
        "customer_name": str(row.get("customer_name") or ""),
        "sales_rep_email": str(row.get("sales_rep_email") or ""),
        "order_hash_code": str(row.get("hash_code") or ""),
        "questionnaire_link": str(row.get("questionnaire_link") or ""),
        "avatax_account_id": str(row.get("avatax_account_id") or ""),
        "custom_report_id": str(row.get("custom_report_id") or ""),
        "sub_package_id": str(row.get("sub_package_id") or ""),
        "sub_package_status_id": str(row.get("sub_package_status_id") or ""),
        "registration_id": str(row.get("registration_id") or ""),
        "data_request_id": str(row.get("data_request_id") or ""),
        "deliverable_available": "true" if bool(deliverable_html) else "false",
        "deliverable_error": deliverable_error,
        "support_email": str(row.get("sales_rep_email") or ""),
        "state_portal_url": state_portal_url,
        "all_urls": json.dumps(urls, ensure_ascii=True),
        "attachment_names": " | ".join(name for name in attachment_names if name),
        "attachment_urls": json.dumps([url for url in attachment_urls if url], ensure_ascii=True),
        "username": username,
        "password": password,
        "secret_question": secret_question,
        "secret_answer": secret_answer,
        "confirmation_number": confirmation_number,
        "license_number": license_number,
        "account_number": account_number,
        "registration_number": registration_number,
        "business_number": business_number,
        "ein_or_tax_id": ein_or_tax_id,
        "mentions_7812": "true" if "7812" in combined_text else "false",
        "mentions_1770": "true" if "1770" in combined_text else "false",
        "mentions_mudmixer": "true" if "mudmixer" in combined_text.lower() else "false",
        "mentions_ojmd": "true" if "ojmd" in combined_text.lower() else "false",
        "api_confirmation_fields": confirmation_summary,
        "deliverable_fields_json": json.dumps(merged_fields, ensure_ascii=True, sort_keys=True),
        "deliverable_text": text,
        "raw_row_json": json.dumps(row, ensure_ascii=True, sort_keys=True),
        "raw_deliverable_json": json.dumps(
            {
                "deliverable_content": deliverable_html,
                "attachments": attachment_records,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    }


def write_csv(path: str, rows: List[Dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"No rows to write to {path}")
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if not args.token:
        print(
            "Missing bearer token. Pass --token or set AVALARA_REGISTRATIONS_TOKEN.",
            file=sys.stderr,
        )
        return 1

    cutoff = date.fromisoformat(args.cutoff_date)
    status_rows = fetch_status_rows(args.token, args.per_page)

    export_rows: List[Dict[str, str]] = []
    raw_export: List[dict] = []
    for row in status_rows:
        deliverable = None
        deliverable_error = ""
        if not row.get("no_deliverable_access") and row.get("registration_id"):
            deliverable, deliverable_error = fetch_deliverable(args.token, row.get("registration_id"))
        export_row = make_export_row(row, deliverable, cutoff)
        export_row["deliverable_error"] = deliverable_error
        export_rows.append(export_row)
        raw_export.append(
            {
                "row": row,
                "deliverable": deliverable,
                "deliverable_error": deliverable_error,
            }
        )

    export_rows.sort(
        key=lambda item: (
            item["date_added"],
            item["jurisdiction"],
            item["registration_type"],
            item["order_number"],
        )
    )

    focused_rows = [row for row in export_rows if row["migration_focus_guess"] == "true"]

    all_csv = f"{args.output_prefix}-all.csv"
    focus_csv = f"{args.output_prefix}-migration-focus.csv"
    raw_json = f"{args.output_prefix}-all.json"

    existing_progress = load_existing_progress(focus_csv)
    for row in export_rows:
        prior = existing_progress.get(progress_key(row), {})
        row["ai_completion"] = prior.get("ai_completion", row["ai_completion"])
        row["ai_notes"] = prior.get("ai_notes", row["ai_notes"])

    write_csv(all_csv, export_rows)
    write_csv(focus_csv, focused_rows or export_rows)

    with open(raw_json, "w", encoding="utf-8") as handle:
        json.dump(raw_export, handle, indent=2, ensure_ascii=True, sort_keys=True)

    print(f"Wrote {len(export_rows)} rows to {all_csv}")
    print(f"Wrote {len(focused_rows)} rows to {focus_csv}")
    print(f"Wrote raw JSON to {raw_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
