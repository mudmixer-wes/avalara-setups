#!/usr/bin/env python3

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE = ROOT / "exports" / "data-dumps" / "2026-03-13" / "rates-jurisdictions"


def load_json(path: Path):
    return json.loads(path.read_text())


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def clean(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def jurisdiction_join_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        country = clean(row.get("country"))
        region = clean(row.get("region"))
        juris_type = clean(row.get("type"))
        state_fips = clean(row.get("stateFips"))
        county_fips = clean(row.get("countyFips"))
        place_fips = clean(row.get("placeFips"))
        code = clean(row.get("code"))
        name = clean(row.get("name"))
        short_name = clean(row.get("shortName"))
        county = clean(row.get("county"))
        city = clean(row.get("city"))

        out.append(
            {
                "jurisdiction_id": row.get("id"),
                "country": country,
                "region": region,
                "jurisdiction_type": juris_type,
                "jurisdiction_code": code,
                "name": name,
                "short_name": short_name,
                "state_fips": state_fips,
                "county_fips": county_fips,
                "place_fips": place_fips,
                "county_name": county,
                "city_name": city,
                "tax_authority_type_id": clean(row.get("taxAuthorityTypeId")),
                "is_acm": row.get("isAcm"),
                "is_local_admin": row.get("isLocalAdmin"),
                "is_sst": row.get("isSst"),
                "effective_date": clean(row.get("effectiveDate")),
                "end_date": clean(row.get("endDate")),
                "state_join_key": f"US-{region}-{state_fips}" if region and state_fips else "",
                "county_join_key": f"US-{region}-{state_fips}-{county_fips}" if region and state_fips and county_fips else "",
                "place_join_key": f"US-{region}-{state_fips}-{place_fips}" if region and state_fips and place_fips else "",
                "name_join_key": f"{country}|{region}|{juris_type}|{name}",
                "code_join_key": f"{country}|{region}|{juris_type}|{code}",
            }
        )
    return out


def zip_rate_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def zip_join_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        state = clean(row.get("STATE_ABBREV"))
        county_name = clean(row.get("COUNTY_NAME"))
        city_name = clean(row.get("CITY_NAME"))
        zip_code = clean(row.get("ZIP_CODE"))
        out.append(
            {
                "zip_code": zip_code,
                "state_abbrev": state,
                "county_name": county_name,
                "city_name": city_name,
                "state_county_name_key": f"{state}|{county_name}",
                "state_city_name_key": f"{state}|{city_name}",
                "zip_state_city_key": f"{zip_code}|{state}|{city_name}",
                "state_sales_tax": clean(row.get("STATE_SALES_TAX")),
                "state_use_tax": clean(row.get("STATE_USE_TAX")),
                "county_sales_tax": clean(row.get("COUNTY_SALES_TAX")),
                "county_use_tax": clean(row.get("COUNTY_USE_TAX")),
                "city_sales_tax": clean(row.get("CITY_SALES_TAX")),
                "city_use_tax": clean(row.get("CITY_USE_TAX")),
                "total_sales_tax": clean(row.get("TOTAL_SALES_TAX")),
                "total_use_tax": clean(row.get("TOTAL_USE_TAX")),
                "tax_shipping_alone": clean(row.get("TAX_SHIPPING_ALONE")),
                "tax_shipping_and_handling_together": clean(row.get("TAX_SHIPPING_AND_HANDLING_TOGETHER")),
            }
        )
    return out


def build_summary(juris_rows: list[dict], zip_rows: list[dict]) -> dict:
    by_type = Counter(row["jurisdiction_type"] for row in juris_rows)
    fips_coverage = {}
    for juris_type in sorted(by_type):
        subset = [row for row in juris_rows if row["jurisdiction_type"] == juris_type]
        fips_coverage[juris_type] = {
            "rows": len(subset),
            "with_state_fips": sum(bool(row["state_fips"]) for row in subset),
            "with_county_fips": sum(bool(row["county_fips"]) for row in subset),
            "with_place_fips": sum(bool(row["place_fips"]) for row in subset),
        }

    summary = {
        "jurisdiction_rows": len(juris_rows),
        "zip_rate_rows": len(zip_rows),
        "jurisdiction_type_counts": by_type,
        "fips_coverage_by_type": fips_coverage,
        "zip_unique_states": len({row["state_abbrev"] for row in zip_rows}),
        "zip_unique_state_county_name_keys": len({row["state_county_name_key"] for row in zip_rows}),
        "zip_unique_state_city_name_keys": len({row["state_city_name_key"] for row in zip_rows}),
        "notes": [
            "State, county, and many city rows can be joined to public GIS using FIPS-style fields from Avalara.",
            "Special jurisdictions generally do not expose public boundary IDs here; later matching will likely depend on state plus code/name and external GIS source rules.",
            "Zip-rate data is useful as a coarse name-based bridge, but it is not a boundary dataset.",
        ],
    }
    return summary


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def readiness_bucket(score: float) -> str:
    if score >= 90:
        return "straightforward"
    if score >= 80:
        return "workable"
    if score >= 70:
        return "moderate"
    if score >= 60:
        return "hard"
    return "ugly"


def state_note_parts(row: dict) -> list[str]:
    notes = []
    if row["special_rows"] >= 500:
        notes.append("special-heavy")
    elif row["special_rows"] >= 150:
        notes.append("meaningful special-jurisdiction burden")
    if row["special_rows"] and row["special_identifier_ratio"] < 0.1:
        notes.append("specials mostly need name/code matching")
    if row["city_rows"] and row["city_place_fips_ratio"] < 0.95:
        notes.append("city place FIPS has gaps")
    if row["city_rows"] and row["city_county_fips_ratio"] < 0.5:
        notes.append("many city rows missing county FIPS")
    if row["county_rows"] and row["county_fips_ratio"] < 0.99:
        notes.append("county FIPS has gaps")
    return notes


def build_state_readiness(juris_rows: list[dict], zip_rows: list[dict]) -> tuple[list[dict], dict]:
    regions = load_json(BASE / "us-regions.json")["value"]
    region_meta = {row["code"]: row for row in regions if row.get("countryCode") == "US"}

    by_state = {code: Counter() for code in region_meta}
    for row in juris_rows:
        state = row["region"]
        if state not in by_state:
            continue
        t = row["jurisdiction_type"]
        by_state[state][f"{t}_rows"] += 1
        if row["state_fips"]:
            by_state[state][f"{t}_state_fips"] += 1
        if row["county_fips"]:
            by_state[state][f"{t}_county_fips"] += 1
        if row["place_fips"]:
            by_state[state][f"{t}_place_fips"] += 1

    zip_by_state = Counter(row["state_abbrev"] for row in zip_rows)

    output = []
    for state in sorted(region_meta):
        meta = region_meta[state]
        counts = by_state[state]
        county_rows = counts["County_rows"]
        city_rows = counts["City_rows"]
        special_rows = counts["Special_rows"]

        county_fips_ratio = ratio(counts["County_county_fips"], county_rows)
        city_place_fips_ratio = ratio(counts["City_place_fips"], city_rows)
        city_county_fips_ratio = ratio(counts["City_county_fips"], city_rows)
        special_county_ratio = ratio(counts["Special_county_fips"], special_rows)
        special_place_ratio = ratio(counts["Special_place_fips"], special_rows)
        special_identifier_ratio = max(special_county_ratio, special_place_ratio)

        state_score = 10.0 if counts["State_state_fips"] else 0.0
        county_score = 25.0 * county_fips_ratio
        city_place_score = 35.0 * city_place_fips_ratio
        city_county_score = 10.0 * city_county_fips_ratio
        if special_rows == 0:
            special_score = 20.0
        else:
            burden_penalty = min(12.0, special_rows / 150.0)
            low_id_penalty = (1.0 - special_identifier_ratio) * 8.0
            special_score = max(0.0, 20.0 - burden_penalty - low_id_penalty)

        score = round(state_score + county_score + city_place_score + city_county_score + special_score, 1)
        row = {
            "state_abbrev": state,
            "state_name": meta.get("name"),
            "classification": meta.get("classification"),
            "is_region_taxable": meta.get("isRegionTaxable"),
            "streamlined_sales_tax": meta.get("streamlinedSalesTax"),
            "zip_rows": zip_by_state.get(state, 0),
            "state_rows": counts["State_rows"],
            "county_rows": county_rows,
            "city_rows": city_rows,
            "special_rows": special_rows,
            "county_fips_ratio": round(county_fips_ratio, 4),
            "city_place_fips_ratio": round(city_place_fips_ratio, 4),
            "city_county_fips_ratio": round(city_county_fips_ratio, 4),
            "special_county_ratio": round(special_county_ratio, 4),
            "special_place_ratio": round(special_place_ratio, 4),
            "special_identifier_ratio": round(special_identifier_ratio, 4),
            "state_score": round(state_score, 1),
            "county_score": round(county_score, 1),
            "city_place_score": round(city_place_score, 1),
            "city_county_score": round(city_county_score, 1),
            "special_score": round(special_score, 1),
            "join_readiness_score": score,
            "join_readiness_bucket": readiness_bucket(score),
        }
        notes = state_note_parts(row)
        row["notes"] = "; ".join(notes)
        output.append(row)

    summary = {
        "states_scored": len(output),
        "bucket_counts": Counter(row["join_readiness_bucket"] for row in output),
        "top_10": sorted(output, key=lambda row: row["join_readiness_score"], reverse=True)[:10],
        "bottom_10": sorted(output, key=lambda row: row["join_readiness_score"])[:10],
        "scoring_notes": {
            "state_score": "10 points if Avalara exposes a state-level row with state FIPS",
            "county_score": "25 points scaled by county FIPS coverage",
            "city_place_score": "35 points scaled by city place-FIPS coverage",
            "city_county_score": "10 points scaled by city county-FIPS coverage",
            "special_score": "20 points minus penalties for special-jurisdiction volume and weak identifier coverage",
        },
    }
    return output, summary


def main() -> None:
    juris_raw = load_json(BASE / "us-jurisdictions.json")["value"]
    zip_raw = zip_rate_rows(BASE / "taxrates-by-zipcode-2026-03-13.csv")

    juris_join = jurisdiction_join_rows(juris_raw)
    zip_join = zip_join_rows(zip_raw)

    write_csv(BASE / "us-jurisdictions-boundary-match.csv", juris_join)
    write_csv(BASE / "taxrates-by-zipcode-boundary-bridge.csv", zip_join)

    write_csv(
        BASE / "us-jurisdictions-state.csv",
        [row for row in juris_join if row["jurisdiction_type"] == "State"],
    )
    write_csv(
        BASE / "us-jurisdictions-county.csv",
        [row for row in juris_join if row["jurisdiction_type"] == "County"],
    )
    write_csv(
        BASE / "us-jurisdictions-city.csv",
        [row for row in juris_join if row["jurisdiction_type"] == "City"],
    )
    write_csv(
        BASE / "us-jurisdictions-special.csv",
        [row for row in juris_join if row["jurisdiction_type"] == "Special"],
    )

    summary = build_summary(juris_join, zip_join)
    (BASE / "boundary-matching-summary.json").write_text(
        json.dumps(summary, indent=2, default=lambda x: dict(x)),
        encoding="utf-8",
    )
    readiness_rows, readiness_summary = build_state_readiness(juris_join, zip_join)
    write_csv(BASE / "state-join-readiness.csv", readiness_rows)
    (BASE / "state-join-readiness-summary.json").write_text(
        json.dumps(readiness_summary, indent=2, default=lambda x: dict(x)),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
