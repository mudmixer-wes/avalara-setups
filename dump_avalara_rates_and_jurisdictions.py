#!/usr/bin/env python3
"""Export Avalara rate and jurisdiction data using an existing AvaTax token."""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://rest.avatax.com"


def fetch(session_headers: dict[str, str], path: str) -> tuple[str, dict[str, str]]:
    request = Request(f"{BASE_URL}{path}", headers=session_headers)
    with urlopen(request, timeout=300) as response:
        body = response.read().decode("utf-8")
        headers = {key.lower(): value for key, value in response.headers.items()}
        return body, headers


def fetch_json(session_headers: dict[str, str], path: str) -> dict:
    body, _ = fetch(session_headers, path)
    return json.loads(body)


def fetch_text(session_headers: dict[str, str], path: str) -> str:
    body, _ = fetch(session_headers, path)
    return body


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    token = os.environ.get("AVATAX_TOKEN")
    if not token:
        print("AVATAX_TOKEN is required", file=sys.stderr)
        return 1

    dump_date = os.environ.get("DUMP_DATE") or date.today().isoformat()
    dump_root = Path("exports/data-dumps") / dump_date
    dump_root.mkdir(parents=True, exist_ok=True)

    session_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-Avalara-Client": "mudmixer-avalara-dump/1.0",
    }

    regions_path = "/api/v2/definitions/regions?$filter=" + quote("countryCode eq 'US'")
    jurisdictions_path = "/api/v2/definitions/jurisdictions?$filter=" + quote("country eq 'US'")

    regions = fetch_json(session_headers, regions_path)
    jurisdictions = fetch_json(session_headers, jurisdictions_path)
    subscriptions = fetch_json(session_headers, "/api/v2/utilities/subscriptions")
    taxcontent_us = fetch_json(session_headers, "/api/v2/taxcontent/rates/US")
    taxcontent_ca = fetch_json(session_headers, "/api/v2/taxcontent/rates/CA")
    zip_rate_csv = fetch_text(session_headers, f"/api/v2/taxratesbyzipcode/download/{dump_date}")

    write_json(dump_root / "us-regions.json", regions)
    write_json(dump_root / "us-jurisdictions.json", jurisdictions)
    write_json(dump_root / "subscriptions.json", subscriptions)
    write_json(dump_root / "taxcontent-rates-us.json", taxcontent_us)
    write_json(dump_root / "taxcontent-rates-ca.json", taxcontent_ca)
    (dump_root / f"taxrates-by-zipcode-{dump_date}.csv").write_text(
        zip_rate_csv, encoding="utf-8"
    )

    region_rows = regions.get("value", [])
    write_csv(
        dump_root / "us-regions.csv",
        region_rows,
        [
            "countryCode",
            "code",
            "name",
            "classification",
            "streamlinedSalesTax",
            "isRegionTaxable",
            "localizedNames",
        ],
    )

    jurisdiction_rows = jurisdictions.get("value", [])
    jurisdiction_csv_rows = [
        {
            "code": row.get("code"),
            "name": row.get("name"),
            "type": row.get("type"),
            "region": row.get("region"),
            "country": row.get("country"),
            "shortName": row.get("shortName"),
            "id": row.get("id"),
            "effectiveDate": row.get("effectiveDate"),
            "endDate": row.get("endDate"),
            "createDate": row.get("createDate"),
            "modifiedDate": row.get("modifiedDate"),
        }
        for row in jurisdiction_rows
    ]
    write_csv(
        dump_root / "us-jurisdictions.csv",
        jurisdiction_csv_rows,
        [
            "code",
            "name",
            "type",
            "region",
            "country",
            "shortName",
            "id",
            "effectiveDate",
            "endDate",
            "createDate",
            "modifiedDate",
        ],
    )

    subscription_rows = subscriptions.get("value", [])
    write_csv(
        dump_root / "subscriptions.csv",
        subscription_rows,
        list(subscription_rows[0].keys()) if subscription_rows else [],
    )

    state_codes = {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
    }
    region_values = regions.get("value", [])
    jurisdiction_values = jurisdictions.get("value", [])
    summary = {
        "dumpDate": dump_date,
        "regionsRecordCount": regions.get("@recordsetCount"),
        "regionClassificationCounts": {
            classification: sum(
                1 for row in region_values if row.get("classification") == classification
            )
            for classification in sorted(
                {row.get("classification") for row in region_values if row.get("classification")}
            )
        },
        "usStatesPresent": sorted(
            row.get("code")
            for row in region_values
            if row.get("classification") == "State" and row.get("code") in state_codes
        ),
        "jurisdictionsRecordCount": jurisdictions.get("@recordsetCount"),
        "jurisdictionTypes": sorted(
            {row.get("type") for row in jurisdiction_values if row.get("type")}
        ),
        "jurisdictionStatesPresent": sorted(
            {row.get("region") for row in jurisdiction_values if row.get("region") in state_codes}
        ),
        "taxcontentUSRecordCount": taxcontent_us.get("@recordsetCount"),
        "taxcontentCARecordCount": taxcontent_ca.get("@recordsetCount"),
        "zipRateCsvPath": f"taxrates-by-zipcode-{dump_date}.csv",
    }
    write_json(dump_root / "summary.json", summary)

    print(json.dumps({"dump_root": str(dump_root), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
