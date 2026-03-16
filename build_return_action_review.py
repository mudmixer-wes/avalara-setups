#!/usr/bin/env python3

import csv
import re
from pathlib import Path


WORKDIR = Path("/Users/wesmelton/Documents/Dev/MudMixer/avalara")
SOURCE_CSV = WORKDIR / "active-returns-default-company-code.csv"

ALL_OUTPUT = WORKDIR / "return-action-review.csv"
DELETE_OUTPUT = WORKDIR / "return-action-delete.csv"
MIGRATE_OUTPUT = WORKDIR / "return-action-migrate.csv"

OLD_EIN = "45-3991770"
NEW_EIN = "47-3097812"
CUTOVER_DATE = "2026-03-01"


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def classify(row: dict[str, str]) -> tuple[str, str, str]:
    return_ein = row.get("return_ein", "")
    entity_name = row.get("return_legal_entity_name", "")

    normalized_ein = normalize(return_ein)
    normalized_entity = normalize(entity_name)

    old_entity = normalized_entity in {"ojmdpartnership"}
    new_ein = normalized_ein == normalize(NEW_EIN)
    old_ein = normalized_ein == normalize(OLD_EIN)

    if new_ein:
        if old_entity:
            return (
                "migrate",
                "new_ein_old_entity_name",
                "EIN matches MudMixer (47-3097812), but the legal entity name still shows OJMD. "
                "Migrate this return to the new company code and correct the entity name during setup.",
            )
        return (
            "migrate",
            "new_ein_matches_target_company",
            "Return already points to EIN 47-3097812. Migrate it into the new company code and "
            "retire the old-company copy after validation.",
        )

    if old_ein or old_entity:
        return (
            "delete",
            "legacy_ojmd_registration",
            "Return still points to OJMD Partnership / EIN 45-3991770 in the old DEFAULT company code. "
            "Recommend delete rather than migrate.",
        )

    return (
        "migrate",
        "fallback_manual_review",
        "Return does not clearly match the old OJMD pattern. Defaulting to migrate, but verify manually.",
    )


def main() -> None:
    with SOURCE_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    review_rows = []
    for row in rows:
        recommended_action, action_basis, action_note = classify(row)
        enriched = dict(row)
        enriched["ai_completed"] = ""
        enriched["recommended_action"] = recommended_action
        enriched["action_basis"] = action_basis
        enriched["action_note"] = action_note
        enriched["cutover_date"] = CUTOVER_DATE
        enriched["review_status"] = ""
        enriched["reviewer_notes"] = ""
        review_rows.append(enriched)

    review_rows.sort(
        key=lambda row: (
            0 if row["recommended_action"] == "delete" else 1,
            row.get("region", ""),
            row.get("tax_form_code", ""),
        )
    )

    fieldnames = [
        "ai_completed",
        "recommended_action",
        "action_basis",
        "action_note",
        "cutover_date",
        "review_status",
        "reviewer_notes",
    ] + list(rows[0].keys())

    outputs = {
        ALL_OUTPUT: review_rows,
        DELETE_OUTPUT: [row for row in review_rows if row["recommended_action"] == "delete"],
        MIGRATE_OUTPUT: [row for row in review_rows if row["recommended_action"] == "migrate"],
    }

    for path, data in outputs.items():
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)


if __name__ == "__main__":
    main()
