#!/usr/bin/env python3

import csv
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "returns-default-company-code.json"
OUT_DIR = ROOT / "exports" / "data-dumps" / "2026-03-13"
CATALOG_CSV = OUT_DIR / "returns-form-catalog.csv"
STATE_LEVEL_CSV = OUT_DIR / "returns-form-catalog-state-level.csv"
LIKELY_OFFICIAL_CSV = OUT_DIR / "returns-form-catalog-likely-official.csv"
REGION_SUMMARY_CSV = OUT_DIR / "returns-form-catalog-region-summary.csv"
SUMMARY_JSON = OUT_DIR / "returns-form-catalog-summary.json"
PARENT_NEXUS_JSON = ROOT / "backups" / "company-6359760-nexus.json"
CHILD_NEXUS_JSON = ROOT / "backups" / "company-6550943-nexus.json"
PARENT_NEXUS_CSV = OUT_DIR / "company-6359760-nexus.csv"
CHILD_NEXUS_CSV = OUT_DIR / "company-6550943-nexus.csv"
NEXUS_SUMMARY_JSON = OUT_DIR / "nexus-summary.json"


US_REGION_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
    "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN",
    "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH",
    "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VI",
    "VT", "WA", "WI", "WV", "WY", "GU", "AS", "MP", "UM",
}


FORM_NUMBER_HINT = re.compile(
    r"\b("
    r"\d{2,5}(?:[-/]\d{1,5})?[A-Z]?"
    r"|[A-Z]{1,6}\s?\d{1,5}[A-Z]?"
    r"|CDTFA\s?\d{1,5}(?:\s?[A-Z0-9]+)?"
    r"|ST\s?\d{1,5}[A-Z]?"
    r"|UST\s?\d{1,5}[A-Z]?"
    r"|TPT[- ]?\d{1,5}[A-Z]?"
    r"|PV\s?\d{1,5}[A-Z]?"
    r"|E\d{3,5}"
    r")\b"
)

DESCRIPTIVE_TERMS = {
    "monthly",
    "quarterly",
    "annual",
    "annually",
    "prepayment",
    "consumer",
    "sellers",
    "seller",
    "sales",
    "use",
    "rental",
    "lease",
    "lodging",
    "universal",
    "city",
    "county",
    "town",
    "village",
    "project",
    "report",
    "return",
}

LOCALITY_HINTS = {
    "city",
    "county",
    "town",
    "village",
    "borough",
    "township",
    "parish",
    "municipal",
}


def classify_name(row: dict) -> str:
    name = (row.get("TaxFormName") or "").strip()
    description = (row.get("Description") or "").strip()
    region = (row.get("Region") or "").strip()
    lower_name = name.lower()

    has_form_number = bool(FORM_NUMBER_HINT.search(name))
    descriptive_hits = sum(term in lower_name for term in DESCRIPTIVE_TERMS)
    is_local = any(term in lower_name for term in LOCALITY_HINTS)

    if has_form_number and not is_local and descriptive_hits <= 2:
        return "likely_official_or_near_official"
    if has_form_number:
        return "likely_normalized_form_label"
    if description and any(token in description.lower() for token in ("return", "tax", "report")):
        if region in US_REGION_CODES and not is_local:
            return "descriptive_state_level_label"
        return "descriptive_local_label"
    return "descriptive_or_internal_label"


def scope_level(row: dict) -> str:
    name = (row.get("TaxFormName") or "").lower()
    if any(term in name for term in LOCALITY_HINTS):
        return "likely_local"
    region = (row.get("Region") or "").strip()
    if region in US_REGION_CODES or row.get("Country") in {"CA", "IN"}:
        return "likely_state_or_province_level"
    return "unclear"


def load_rows() -> list[dict]:
    return json.loads(SOURCE.read_text())


def build_export_rows(rows: list[dict]) -> list[dict]:
    export_rows = []
    for row in rows:
        export_rows.append(
            {
                "country": row.get("Country"),
                "region": row.get("Region"),
                "tax_form_code": row.get("TaxFormCode"),
                "tax_form_name": row.get("TaxFormName"),
                "legacy_return_name": row.get("LegacyReturnName"),
                "description": row.get("Description"),
                "purpose": row.get("Purpose"),
                "status": row.get("Status"),
                "effective_date": row.get("EffDate"),
                "end_date": row.get("EndDate"),
                "major": row.get("Major"),
                "minor": row.get("Minor"),
                "revision": row.get("Revision"),
                "deleted": row.get("Deleted"),
                "preview_image": row.get("PreviewImage"),
                "scope_level_guess": scope_level(row),
                "form_name_classification": classify_name(row),
            }
        )
    return export_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize_regions(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], Counter] = {}
    for row in rows:
        key = (row["country"], row["region"])
        grouped.setdefault(key, Counter())
        grouped[key]["row_count"] += 1
        grouped[key][f"class::{row['form_name_classification']}"] += 1
        grouped[key][f"scope::{row['scope_level_guess']}"] += 1

    summary_rows = []
    for (country, region), counter in sorted(grouped.items()):
        summary_rows.append(
            {
                "country": country,
                "region": region,
                "row_count": counter["row_count"],
                "likely_official_or_near_official": counter["class::likely_official_or_near_official"],
                "likely_normalized_form_label": counter["class::likely_normalized_form_label"],
                "descriptive_state_level_label": counter["class::descriptive_state_level_label"],
                "descriptive_local_label": counter["class::descriptive_local_label"],
                "descriptive_or_internal_label": counter["class::descriptive_or_internal_label"],
                "likely_state_or_province_level": counter["scope::likely_state_or_province_level"],
                "likely_local": counter["scope::likely_local"],
                "unclear": counter["scope::unclear"],
            }
        )
    return summary_rows


def load_json_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    return []


def export_nexus() -> dict:
    parent_rows = load_json_rows(PARENT_NEXUS_JSON)
    child_rows = load_json_rows(CHILD_NEXUS_JSON)
    if parent_rows:
        write_csv(PARENT_NEXUS_CSV, parent_rows)
    if child_rows:
        write_csv(CHILD_NEXUS_CSV, child_rows)

    return {
        "parent_company_rows": len(parent_rows),
        "child_company_rows": len(child_rows),
        "parent_regions": sorted({row.get("region") for row in parent_rows if row.get("region")}),
        "parent_jurisdiction_types": Counter(row.get("jurisdictionTypeId") for row in parent_rows),
        "parent_nexus_type_ids": Counter(row.get("nexusTypeId") for row in parent_rows),
        "parent_tax_type_groups": Counter(row.get("taxTypeGroup") for row in parent_rows),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    export_rows = build_export_rows(rows)
    state_level_rows = [row for row in export_rows if row["scope_level_guess"] == "likely_state_or_province_level"]
    likely_official_rows = [row for row in export_rows if row["form_name_classification"] == "likely_official_or_near_official"]
    region_summary_rows = summarize_regions(export_rows)

    write_csv(CATALOG_CSV, export_rows)
    write_csv(STATE_LEVEL_CSV, state_level_rows)
    write_csv(LIKELY_OFFICIAL_CSV, likely_official_rows)
    write_csv(REGION_SUMMARY_CSV, region_summary_rows)

    summary = {
        "source_file": str(SOURCE.name),
        "total_rows": len(export_rows),
        "rows_by_country": Counter(row["country"] for row in export_rows),
        "rows_by_scope_level_guess": Counter(row["scope_level_guess"] for row in export_rows),
        "rows_by_form_name_classification": Counter(row["form_name_classification"] for row in export_rows),
        "state_level_rows": len(state_level_rows),
        "likely_official_rows": len(likely_official_rows),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=lambda x: dict(x)))
    NEXUS_SUMMARY_JSON.write_text(json.dumps(export_nexus(), indent=2, default=lambda x: dict(x)))


if __name__ == "__main__":
    main()
