#!/usr/bin/env python3

import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKUPS = ROOT / "backups"
DUMP_DATE = date(2026, 3, 13).isoformat()
OUT_DIR = ROOT / "exports" / "data-dumps" / DUMP_DATE


def load_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_companies() -> dict:
    raw = load_json(BACKUPS / "account-companies-380.json")
    rows = raw.get("value", [])
    write_json(OUT_DIR / "account-companies.json", raw)
    write_csv(OUT_DIR / "account-companies.csv", rows)
    return {
        "company_count": len(rows),
        "active_company_codes": [row["companyCode"] for row in rows if row.get("isActive")],
        "default_company_code": next((row["companyCode"] for row in rows if row.get("isDefault")), None),
    }


def export_obligations() -> dict:
    raw = load_json(BACKUPS / "account-obligations-172.json")
    write_json(OUT_DIR / "account-obligations.json", raw)

    rows = raw.get("customerObligations", [])
    write_csv(OUT_DIR / "customer-obligations.csv", rows)

    by_product = defaultdict(lambda: {"count": 0, "price_total": 0.0})
    by_connector = defaultdict(lambda: {"count": 0, "price_total": 0.0})
    for row in rows:
        product = row.get("productCatalogId") or "UNKNOWN"
        connector = row.get("connectorName") or "NONE"
        by_product[product]["count"] += 1
        by_product[product]["price_total"] += float(row.get("price") or 0)
        by_connector[connector]["count"] += 1
        by_connector[connector]["price_total"] += float(row.get("price") or 0)

    product_rows = [
        {
            "product_catalog_id": product,
            "row_count": values["count"],
            "price_total": round(values["price_total"], 2),
        }
        for product, values in sorted(by_product.items())
    ]
    connector_rows = [
        {
            "connector_name": connector,
            "row_count": values["count"],
            "price_total": round(values["price_total"], 2),
        }
        for connector, values in sorted(by_connector.items())
    ]
    write_csv(OUT_DIR / "customer-obligations-by-product.csv", product_rows)
    write_csv(OUT_DIR / "customer-obligations-by-connector.csv", connector_rows)

    summary = {
        "managed_returns": raw.get("managedReturns", {}),
        "customer_obligation_count": len(rows),
        "obligation_sources": Counter(row.get("obligationSource") for row in rows),
        "products": Counter(row.get("productCatalogId") for row in rows),
        "connectors": Counter(row.get("connectorName") or "NONE" for row in rows),
    }
    write_json(OUT_DIR / "account-obligations-summary.json", summary)
    return summary


def flatten_filing_calendars() -> tuple[list[dict], list[dict], dict]:
    raw = load_json(BACKUPS / "filingCalendars-2097.json")
    write_json(OUT_DIR / "default-company-filing-calendars-snapshot.json", raw)

    calendar_rows = []
    answer_rows = []
    statuses = Counter()
    frequencies = Counter()
    tax_forms = Counter()

    for row in raw:
        data = row.get("data") or {}
        statuses[row.get("GRStatus")] += 1
        frequencies[data.get("filingFrequencyId")] += 1
        tax_forms[data.get("taxFormCode")] += 1
        calendar_rows.append(
            {
                "id": row.get("id"),
                "company_id": row.get("companyId"),
                "gr_type_pre_transform": row.get("GRTypePreTransform"),
                "gr_status": row.get("GRStatus"),
                "company_return_id": data.get("companyReturnId"),
                "return_name": data.get("returnName"),
                "tax_form_code": data.get("taxFormCode"),
                "filing_frequency_id": data.get("filingFrequencyId"),
                "registration_id": data.get("registrationId"),
                "months": data.get("months"),
                "tax_type_id": data.get("taxTypeId"),
                "location_code": data.get("locationCode"),
                "effective_date": data.get("effDate"),
                "end_date": data.get("endDate"),
                "country": data.get("country"),
                "region": data.get("region"),
                "tax_authority_id": data.get("taxAuthorityId"),
                "tax_authority_name": data.get("taxAuthorityName"),
                "answers_json": json.dumps(data.get("answers", []), ensure_ascii=True),
            }
        )
        for answer in data.get("answers", []):
            answer_rows.append(
                {
                    "filing_calendar_id": row.get("id"),
                    "company_return_id": data.get("companyReturnId"),
                    "tax_form_code": data.get("taxFormCode"),
                    "region": data.get("region"),
                    "filing_question_id": answer.get("filingQuestionId"),
                    "answer": answer.get("answer"),
                }
            )

    write_csv(OUT_DIR / "default-company-filing-calendars-snapshot.csv", calendar_rows)
    write_csv(OUT_DIR / "default-company-filing-calendar-answers.csv", answer_rows)

    summary = {
        "filing_calendar_row_count": len(calendar_rows),
        "answer_row_count": len(answer_rows),
        "status_counts": statuses,
        "frequency_counts": frequencies,
        "top_tax_forms": tax_forms.most_common(25),
    }
    write_json(OUT_DIR / "default-company-filing-calendars-summary.json", summary)
    return calendar_rows, answer_rows, summary


def export_new_company_settings() -> dict:
    raw = load_json(BACKUPS / "company-6550943-settings.json")
    write_json(OUT_DIR / "company-6550943-settings.json", raw)
    summary = {
        "company_id": 6550943,
        "is_ssgl_company": raw.get("isSSGLCompany"),
        "two_factor_alias_configured": ((raw.get("twoFactorAlias") or {}).get("configured")),
        "ssgl_id": ((raw.get("ssgl") or {}).get("id")),
        "ssgl_set": ((raw.get("ssgl") or {}).get("set")),
        "ssgl_name": ((raw.get("ssgl") or {}).get("name")),
        "ssgl_modified_date": ((raw.get("ssgl") or {}).get("modifiedDate")),
        "ssgl_countries": sorted((((raw.get("ssgl") or {}).get("value") or {}).get("countries") or {}).keys()),
    }
    write_json(OUT_DIR / "company-6550943-settings-summary.json", summary)
    return summary


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    companies = export_companies()
    obligations = export_obligations()
    _, _, filing_calendars = flatten_filing_calendars()
    settings = export_new_company_settings()

    summary = {
        "dump_dir": str(OUT_DIR),
        "companies": companies,
        "obligations": obligations,
        "filing_calendars": filing_calendars,
        "new_company_settings": settings,
    }
    write_json(OUT_DIR / "offboarding-export-summary.json", summary)


if __name__ == "__main__":
    main()
