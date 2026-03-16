#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests


INPUT_PATH = Path(
    "exports/data-dumps/2026-03-13/returns-search-surface/raw/us-region-taxforms.json"
)
OUTPUT_DIR = Path(
    "exports/data-dumps/2026-03-13/returns-search-surface/form-images"
)
SUMMARY_PATH = Path(
    "exports/data-dumps/2026-03-13/returns-search-surface/form-images-summary.json"
)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def extension_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    return suffix if suffix else ".bin"


def main() -> None:
    data = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    form_count = 0
    image_ref_count = 0
    download_count = 0
    skipped_existing_count = 0
    error_count = 0
    errors: list[dict[str, str]] = []
    by_form: list[dict[str, object]] = []
    pending: list[dict[str, object]] = []

    for region, items in sorted(data.items()):
        for item in items:
            if item.get("_error"):
                continue

            summary = item.get("FormSummary", {})
            tax_form_code = summary.get("TaxFormCode")
            if not tax_form_code:
                continue

            form_count += 1
            form_dir = OUTPUT_DIR / safe_name(tax_form_code)
            form_dir.mkdir(parents=True, exist_ok=True)

            urls = item.get("FormImageUrls", [])
            form_record = {
                "region": region,
                "tax_form_code": tax_form_code,
                "tax_form_name": summary.get("TaxFormName"),
                "image_count": len(urls),
                "files": [],
            }

            for idx, url in enumerate(urls, start=1):
                image_ref_count += 1
                ext = extension_from_url(url)
                filename = f"{idx:02d}{ext}"
                path = form_dir / filename

                if path.exists() and path.stat().st_size > 0:
                    skipped_existing_count += 1
                    form_record["files"].append(
                        {"filename": filename, "url": url, "status": "existing"}
                    )
                    continue

                pending.append(
                    {
                        "tax_form_code": tax_form_code,
                        "url": url,
                        "path": path,
                        "filename": filename,
                        "form_record": form_record,
                    }
                )

            by_form.append(form_record)

    def download_one(task: dict[str, object]) -> dict[str, object]:
        with requests.Session() as session:
            session.headers.update({"User-Agent": "Mozilla/5.0"})
            response = session.get(str(task["url"]), timeout=60)
            response.raise_for_status()
            path = Path(task["path"])
            path.write_bytes(response.content)
            return {
                "tax_form_code": task["tax_form_code"],
                "url": task["url"],
                "filename": task["filename"],
                "bytes": len(response.content),
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(download_one, task): task for task in pending}
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            form_record = task["form_record"]
            try:
                result = future.result()
                download_count += 1
                form_record["files"].append(
                    {
                        "filename": result["filename"],
                        "url": result["url"],
                        "status": "downloaded",
                        "bytes": result["bytes"],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                error_count += 1
                errors.append(
                    {
                        "tax_form_code": str(task["tax_form_code"]),
                        "url": str(task["url"]),
                        "error": str(exc),
                    }
                )
                form_record["files"].append(
                    {
                        "filename": str(task["filename"]),
                        "url": str(task["url"]),
                        "status": "error",
                        "error": str(exc),
                    }
                )

    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "source_file": str(INPUT_PATH),
                "output_dir": str(OUTPUT_DIR),
                "form_count": form_count,
                "image_reference_count": image_ref_count,
                "download_count": download_count,
                "skipped_existing_count": skipped_existing_count,
                "error_count": error_count,
                "errors": errors,
                "forms": by_form,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
