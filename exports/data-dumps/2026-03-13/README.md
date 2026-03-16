# Avalara Data Dumps

This folder preserves the original flat exports and also provides categorized copies for easier review.

## Quick Start

If you are trying to replace Avalara or bring tax operations in house, start here:

- `rates-jurisdictions/`
  - nationwide U.S. rate and jurisdiction datasets
  - most useful file: `taxrates-by-zipcode-2026-03-13.csv`
- `returns-catalog/`
  - Avalara's returns form catalog and filtered likely-official form-name candidates
  - most useful file: `returns-form-catalog-likely-official.csv`
- `nexus-config/`
  - parent-company nexus setup exported from `Where you report tax`
  - most useful file: `company-6359760-nexus.csv`
- `filings/`
  - default-company filing calendar snapshot and exploded question/answer data
  - most useful files:
    - `default-company-filing-calendars-snapshot.csv`
    - `default-company-filing-calendar-answers.csv`
- `commercial-account/`
  - company inventory, subscriptions, and commercial obligations
  - most useful files:
    - `account-companies.csv`
    - `customer-obligations.csv`
    - `customer-obligations-by-product.csv`
    - `customer-obligations-by-connector.csv`
- `settings/`
  - captured settings for the new returns company
- `manifests/`
  - top-level summary files

## Notes

- Original files remain in the root of this folder to avoid breaking prior references.
- Categorized subfolders are mirrored copies for easier navigation.
- These exports reflect data captured on `2026-03-13`.
