#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests


ACCOUNT_ID = "2006428542"
DOMAIN_IDS = "1,8,1001011"
BASE_URL = "https://api.returns.avalara.com"
OUT_DIR = Path("exports/data-dumps/2026-03-13/returns-search-surface")
RAW_DIR = OUT_DIR / "raw"


def require_token() -> str:
    token = os.environ.get("AVALARA_RETURNS_TOKEN", "").strip()
    if not token:
        raise SystemExit("AVALARA_RETURNS_TOKEN is required")
    return token


def session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Origin": "https://app.avalara.com",
            "Referer": "https://app.avalara.com/",
            "User-Agent": "Mozilla/5.0",
        }
    )
    return s


def get_json(
    s: requests.Session,
    url: str,
    *,
    retries: int = 3,
    timeout: int = 60,
) -> Any:
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = s.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries:
                raise
            time.sleep(1.5 * attempt)
    raise last_exc  # pragma: no cover


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def parse_tax_form_code_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    value = value.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except json.JSONDecodeError:
        pass
    return [value]


def authority_level_guess(report_level: str | None) -> str:
    mapping = {
        "STA": "state",
        "CTY": "county",
        "CITY": "city",
        "CIT": "city",
        "SPC": "special",
        "DST": "district",
    }
    return mapping.get((report_level or "").upper(), "")


def is_likely_registration_field(question: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(question.get("Question", "")),
            str(question.get("FilingQuestionCode", "")),
            str(question.get("Destination", "")),
            str(question.get("JsonPath", "")),
            str(question.get("GRLabel", "")),
        ]
    ).lower()
    keywords = [
        "account",
        "registration",
        "permit",
        "license",
        "import id",
        "location id",
        "state id",
        "tax id",
        "fein",
        "ein",
        "username",
        "user id",
        "password",
        "pin",
        "access code",
        "confirmation",
        "vendor",
        "seller",
        "treasury",
        "login",
    ]
    if any(keyword in text for keyword in keywords):
        return True
    return str(question.get("Destination")) in {"RegistrationId", "EfileUsername", "EfilePassword"}


def stringify_list(values: list[Any]) -> str:
    return " | ".join(str(v) for v in values if v not in (None, ""))


def fetch_region_tax_forms(s: requests.Session, region: str) -> list[dict[str, Any]]:
    url = (
        f"{BASE_URL}/taxForms/US/{region}/{DOMAIN_IDS}"
        "?blockSetupForUSFLDR15CS=false&makeSSTFormsVisibleToCustomers=false"
    )
    return get_json(s, url)


def fetch_region_onboarding_questions(s: requests.Session, region: str) -> list[dict[str, Any]]:
    url = f"{BASE_URL}/onboardingQuestions/US/{region}/1"
    return get_json(s, url)


def fetch_metadata(s: requests.Session, tax_form_code: str) -> dict[str, Any]:
    url = f"{BASE_URL}/filingCalendarMetadata?taxFormCode={tax_form_code}"
    return get_json(s, url)


def main() -> None:
    token = require_token()
    s = session(token)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    catalog = get_json(s, f"{BASE_URL}/taxForms/all?domainIds[]=1&domainIds[]=8&domainIds[]=1001011")
    us_catalog = [row for row in catalog if row.get("Country") == "US"]
    us_catalog_by_code = {row["TaxFormCode"]: row for row in us_catalog}
    regions = sorted({row["Region"] for row in us_catalog if row.get("Region")})

    tax_forms_by_level = get_json(
        s,
        f"{BASE_URL}/taxFormsByLevel/US/{DOMAIN_IDS}"
        "?blockSetupForUSFLDR15CS=false&makeSSTFormsVisibleToCustomers=false",
    )
    all_onboarding_questions = get_json(s, f"{BASE_URL}/allOnboardingQuestions/US/1")

    write_json(RAW_DIR / "us-taxforms-all.json", catalog)
    write_json(RAW_DIR / "us-taxforms-by-level.json", tax_forms_by_level)
    write_json(RAW_DIR / "us-all-onboarding-questions.json", all_onboarding_questions)

    region_tax_forms: dict[str, list[dict[str, Any]]] = {}
    region_onboarding: dict[str, list[dict[str, Any]]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        form_futures = {executor.submit(fetch_region_tax_forms, s, region): region for region in regions}
        onboarding_futures = {
            executor.submit(fetch_region_onboarding_questions, s, region): region for region in regions
        }
        for future in concurrent.futures.as_completed(form_futures):
            region = form_futures[future]
            try:
                region_tax_forms[region] = future.result()
            except Exception as exc:  # noqa: BLE001
                region_tax_forms[region] = [{"_error": str(exc), "region": region}]
        for future in concurrent.futures.as_completed(onboarding_futures):
            region = onboarding_futures[future]
            try:
                region_onboarding[region] = future.result()
            except Exception as exc:  # noqa: BLE001
                region_onboarding[region] = [{"_error": str(exc), "region": region}]

    write_json(RAW_DIR / "us-region-taxforms.json", region_tax_forms)
    write_json(RAW_DIR / "us-region-onboarding-questions.json", region_onboarding)

    all_codes = sorted(us_catalog_by_code)
    metadata_by_code: dict[str, Any] = {}
    failures: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_metadata, s, code): code for code in all_codes}
        for idx, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            code = futures[future]
            try:
                metadata_by_code[code] = future.result()
            except Exception as exc:  # noqa: BLE001
                failures[code] = str(exc)
            if idx % 100 == 0:
                print(f"metadata fetched: {idx}/{len(all_codes)}", file=sys.stderr)

    write_json(RAW_DIR / "us-filing-calendar-metadata.json", metadata_by_code)
    write_json(RAW_DIR / "us-filing-calendar-metadata-failures.json", failures)

    catalog_rows: list[dict[str, Any]] = []
    for row in us_catalog:
        catalog_rows.append(
            {
                "tax_form_code": row.get("TaxFormCode"),
                "tax_form_name": row.get("TaxFormName"),
                "legacy_return_name": row.get("LegacyReturnName"),
                "description": row.get("Description"),
                "country": row.get("Country"),
                "region": row.get("Region"),
                "purpose": row.get("Purpose"),
                "status": row.get("Status"),
                "effective_date": row.get("EffDate"),
                "end_date": row.get("EndDate"),
                "preview_image": row.get("PreviewImage"),
                "form_master_id": row.get("FormMasterId"),
                "form_version_id": row.get("FormVersionId"),
                "major": row.get("Major"),
                "minor": row.get("Minor"),
                "revision": row.get("Revision"),
            }
        )

    search_form_rows: list[dict[str, Any]] = []
    for region, forms in sorted(region_tax_forms.items()):
        for item in forms:
            if item.get("_error"):
                continue
            summary = item.get("FormSummary", {})
            authorities = item.get("FormTaxAuthorities", [])
            tax_types = item.get("FormTaxTypes", [])
            search_form_rows.append(
                {
                    "country": summary.get("Country"),
                    "region": summary.get("Region") or region,
                    "tax_form_code": summary.get("TaxFormCode"),
                    "tax_form_name": summary.get("TaxFormName"),
                    "description": summary.get("Description"),
                    "purpose": normalize_text(summary.get("Purpose")),
                    "visible_to_customers": summary.get("VisibleToCustomers"),
                    "report_level": summary.get("ReportLevel"),
                    "authority_level_guess": authority_level_guess(summary.get("ReportLevel")),
                    "form_master_id": summary.get("FormMasterId"),
                    "tax_authority_count": len(authorities),
                    "tax_authorities": stringify_list([a.get("TaxAuthority") for a in authorities]),
                    "tax_authority_ids": stringify_list([a.get("TaxAuthorityId") for a in authorities]),
                    "tax_type_count": len(tax_types),
                    "tax_types": stringify_list([t.get("TaxType") for t in tax_types]),
                    "tax_type_ids": stringify_list([t.get("TaxTypeId") for t in tax_types]),
                    "form_image_urls": json_dumps(item.get("FormImageUrls", [])),
                    "raw_form_summary": json_dumps(summary),
                }
            )

    onboarding_rows: list[dict[str, Any]] = []
    all_onboarding_rows = [
        row for region_rows in region_onboarding.values() for row in region_rows if not row.get("_error")
    ]
    for row in all_onboarding_rows:
        codes = parse_tax_form_code_list(row.get("TaxFormCode"))
        onboarding_rows.append(
            {
                "country": row.get("Country"),
                "region": row.get("Region"),
                "sequence": row.get("Sequence"),
                "leading_question_id": row.get("LeadingQuestionId"),
                "leading_question_answer_id": row.get("LeadingQuestionAnswerId"),
                "question": normalize_text(row.get("Question")),
                "question_code": row.get("QuestionCode"),
                "answer": normalize_text(row.get("Answer")),
                "answer_help_text": normalize_text(row.get("AnswerHelpText")),
                "help_text": normalize_text(row.get("HelpText")),
                "data_type": row.get("DataType"),
                "allow_multi_select": row.get("AllowMultiSelect"),
                "domain_id": row.get("DomainId"),
                "next_question_id": row.get("NextQuestionId"),
                "tax_form_codes": stringify_list(codes),
                "tax_form_code_count": len(codes),
                "raw_row": json_dumps(row),
            }
        )

    form_summary_by_code = {
        row["tax_form_code"]: row for row in search_form_rows if row.get("tax_form_code")
    }

    metadata_summary_rows: list[dict[str, Any]] = []
    question_rows: list[dict[str, Any]] = []
    registration_field_rows: list[dict[str, Any]] = []

    question_sections = [
        ("StandardQuestions", "standard"),
        ("CustomQuestions", "custom"),
        ("GRFilingMethodQuestions", "filing_method"),
        ("filteredQuestions.filteredStandardQuestions", "filtered_standard"),
        ("filteredQuestions.filteredCustomQuestions", "filtered_custom"),
    ]

    for code in all_codes:
        metadata = metadata_by_code.get(code, {})
        header = metadata.get("FormHeader", {})
        catalog_row = us_catalog_by_code.get(code, {})
        authorities = metadata.get("FormTaxAuthorities", [])
        tax_types = metadata.get("FormTaxTypes", [])
        filing_methods = metadata.get("FormFilingMethods", [])
        filing_frequencies = metadata.get("FormFilingFrequencies", [])
        dependencies = metadata.get("FormDependencies", [])
        optional_schedules = metadata.get("OptionalSchedules", [])
        adjustments = metadata.get("AllowableAdjustments", [])

        all_question_items: list[dict[str, Any]] = []
        for section_path, section_label in question_sections:
            if "." in section_path:
                first, second = section_path.split(".", 1)
                questions = metadata.get(first, {}).get(second, [])
            else:
                questions = metadata.get(section_path, [])
            for question in questions:
                all_question_items.append(question)
                row = {
                    "tax_form_code": code,
                    "region": header.get("Region") or catalog_row.get("Region"),
                    "tax_form_name": header.get("TaxFormName") or catalog_row.get("TaxFormName"),
                    "section": section_label,
                    "filing_question_id": question.get("FilingQuestionId"),
                    "sort_order": question.get("SortOrder"),
                    "question": normalize_text(question.get("Question")),
                    "gr_label": question.get("GRLabel"),
                    "filing_question_code": question.get("FilingQuestionCode"),
                    "destination": question.get("Destination"),
                    "required": question.get("Required"),
                    "skyscraper_validation_required": question.get("SkyscraperValidationRequired"),
                    "hide_for_filing_method_id": question.get("HideForFilingMethodId"),
                    "hide_for_filing_method": question.get("HideForFilingMethod"),
                    "data_type": question.get("DataType"),
                    "max_length": question.get("MaxLength"),
                    "regex": question.get("Regex"),
                    "json_path": question.get("JsonPath"),
                    "internal_only": question.get("InternalOnly"),
                    "default_answer": question.get("DefaultAnswer"),
                    "placeholder_text": question.get("PlaceholderText"),
                    "help_text": normalize_text(question.get("HelpText")),
                    "inline_help_text": normalize_text(question.get("InlineHelpText")),
                    "likely_registration_field": is_likely_registration_field(question),
                    "raw_question": json_dumps(question),
                }
                question_rows.append(row)
                if row["likely_registration_field"]:
                    registration_field_rows.append(row)

        metadata_summary_rows.append(
            {
                "tax_form_code": code,
                "region": header.get("Region") or catalog_row.get("Region"),
                "tax_form_name": header.get("TaxFormName") or catalog_row.get("TaxFormName"),
                "description": header.get("Description") or catalog_row.get("Description"),
                "purpose": normalize_text(header.get("Purpose") or catalog_row.get("Purpose")),
                "report_level": form_summary_by_code.get(code, {}).get("report_level"),
                "authority_level_guess": form_summary_by_code.get(code, {}).get("authority_level_guess"),
                "due_day": header.get("DueDay"),
                "requires_outlet_setup": header.get("RequiresOutletSetup"),
                "is_two_factor_auth_required": header.get("IsTwoFactorAuthRequired"),
                "is_non_billable": header.get("IsNonBillable"),
                "ach_credit_only_payment": header.get("ACHCreditOnlyPayment"),
                "dor_website": header.get("DORWebsite"),
                "dor_email_address": header.get("DOREmailAddress"),
                "dor_phone_number": header.get("DORPhoneNumber"),
                "dor_address_mail_to": header.get("DORAddressMailTo"),
                "dor_address_1": header.get("DORAddress1"),
                "dor_address_2": header.get("DORAddress2"),
                "dor_address_city": header.get("DORAddressCity"),
                "dor_address_region": header.get("DORAddressRegion"),
                "dor_address_postal_code": header.get("DORAddressPostalCode"),
                "payment_website": header.get("PaymentWebsite"),
                "zero_website": header.get("ZeroWebsite"),
                "amended_website": header.get("AmendedWebsite"),
                "has_shared_login_credentials": header.get("HasSharedLoginCredentials"),
                "outlet_reporting_method": header.get("OutletReportingMethod"),
                "form_type": header.get("FormType"),
                "form_image_count": len(metadata.get("FormImageUrls", [])),
                "tax_authority_count": len(authorities),
                "tax_authorities": stringify_list([a.get("TaxAuthority") for a in authorities]),
                "tax_types": stringify_list([t.get("TaxType") for t in tax_types]),
                "filing_methods": stringify_list([m.get("FilingMethod") for m in filing_methods]),
                "filing_frequencies": stringify_list([f.get("FilingFrequency") for f in filing_frequencies]),
                "frequency_examples": stringify_list(
                    [
                        ((freq.get("FilingPeriodDates") or [{}])[0] or {}).get("GRDisplayStringLong")
                        for freq in filing_frequencies
                    ]
                ),
                "dependency_codes": stringify_list(
                    [dep.get("DependentTaxFormCode") or dep.get("TaxFormCode") for dep in dependencies]
                ),
                "optional_schedules": stringify_list([s.get("ScheduleName") for s in optional_schedules]),
                "adjustment_codes": stringify_list([adj.get("AdjustmentCode") for adj in adjustments]),
                "standard_question_count": len(metadata.get("StandardQuestions", [])),
                "custom_question_count": len(metadata.get("CustomQuestions", [])),
                "filing_method_question_count": len(metadata.get("GRFilingMethodQuestions", [])),
                "filtered_standard_question_count": len(
                    metadata.get("filteredQuestions", {}).get("filteredStandardQuestions", [])
                ),
                "filtered_custom_question_count": len(
                    metadata.get("filteredQuestions", {}).get("filteredCustomQuestions", [])
                ),
                "registration_field_count": sum(1 for q in all_question_items if is_likely_registration_field(q)),
                "registration_field_names": stringify_list(
                    [normalize_text(q.get("Question")) for q in all_question_items if is_likely_registration_field(q)]
                ),
                "raw_form_header": json_dumps(header),
            }
        )

    summary = {
        "account_id": ACCOUNT_ID,
        "us_catalog_form_count": len(us_catalog),
        "us_region_count": len(regions),
        "us_region_taxform_row_count": len(search_form_rows),
        "us_onboarding_question_row_count": len(onboarding_rows),
        "us_metadata_form_count": len(metadata_by_code),
        "us_metadata_failure_count": len(failures),
        "us_question_field_row_count": len(question_rows),
        "us_registration_field_row_count": len(registration_field_rows),
    }

    write_csv(
        OUT_DIR / "returns-search-form-catalog-us.csv",
        catalog_rows,
        [
            "tax_form_code",
            "tax_form_name",
            "legacy_return_name",
            "description",
            "country",
            "region",
            "purpose",
            "status",
            "effective_date",
            "end_date",
            "preview_image",
            "form_master_id",
            "form_version_id",
            "major",
            "minor",
            "revision",
        ],
    )
    write_csv(
        OUT_DIR / "returns-search-forms-us.csv",
        search_form_rows,
        [
            "country",
            "region",
            "tax_form_code",
            "tax_form_name",
            "description",
            "purpose",
            "visible_to_customers",
            "report_level",
            "authority_level_guess",
            "form_master_id",
            "tax_authority_count",
            "tax_authorities",
            "tax_authority_ids",
            "tax_type_count",
            "tax_types",
            "tax_type_ids",
            "form_image_urls",
            "raw_form_summary",
        ],
    )
    write_csv(
        OUT_DIR / "returns-search-onboarding-questions-us.csv",
        onboarding_rows,
        [
            "country",
            "region",
            "sequence",
            "leading_question_id",
            "leading_question_answer_id",
            "question",
            "question_code",
            "answer",
            "answer_help_text",
            "help_text",
            "data_type",
            "allow_multi_select",
            "domain_id",
            "next_question_id",
            "tax_form_codes",
            "tax_form_code_count",
            "raw_row",
        ],
    )
    write_csv(
        OUT_DIR / "returns-search-metadata-summary-us.csv",
        metadata_summary_rows,
        [
            "tax_form_code",
            "region",
            "tax_form_name",
            "description",
            "purpose",
            "report_level",
            "authority_level_guess",
            "due_day",
            "requires_outlet_setup",
            "is_two_factor_auth_required",
            "is_non_billable",
            "ach_credit_only_payment",
            "dor_website",
            "dor_email_address",
            "dor_phone_number",
            "dor_address_mail_to",
            "dor_address_1",
            "dor_address_2",
            "dor_address_city",
            "dor_address_region",
            "dor_address_postal_code",
            "payment_website",
            "zero_website",
            "amended_website",
            "has_shared_login_credentials",
            "outlet_reporting_method",
            "form_type",
            "form_image_count",
            "tax_authority_count",
            "tax_authorities",
            "tax_types",
            "filing_methods",
            "filing_frequencies",
            "frequency_examples",
            "dependency_codes",
            "optional_schedules",
            "adjustment_codes",
            "standard_question_count",
            "custom_question_count",
            "filing_method_question_count",
            "filtered_standard_question_count",
            "filtered_custom_question_count",
            "registration_field_count",
            "registration_field_names",
            "raw_form_header",
        ],
    )
    write_csv(
        OUT_DIR / "returns-search-question-fields-us.csv",
        question_rows,
        [
            "tax_form_code",
            "region",
            "tax_form_name",
            "section",
            "filing_question_id",
            "sort_order",
            "question",
            "gr_label",
            "filing_question_code",
            "destination",
            "required",
            "skyscraper_validation_required",
            "hide_for_filing_method_id",
            "hide_for_filing_method",
            "data_type",
            "max_length",
            "regex",
            "json_path",
            "internal_only",
            "default_answer",
            "placeholder_text",
            "help_text",
            "inline_help_text",
            "likely_registration_field",
            "raw_question",
        ],
    )
    write_csv(
        OUT_DIR / "returns-search-registration-fields-us.csv",
        registration_field_rows,
        [
            "tax_form_code",
            "region",
            "tax_form_name",
            "section",
            "filing_question_id",
            "sort_order",
            "question",
            "gr_label",
            "filing_question_code",
            "destination",
            "required",
            "data_type",
            "max_length",
            "regex",
            "json_path",
            "internal_only",
            "help_text",
            "inline_help_text",
            "raw_question",
        ],
    )
    write_json(OUT_DIR / "returns-search-surface-summary.json", summary)


if __name__ == "__main__":
    main()
