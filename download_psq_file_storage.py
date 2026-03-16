#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from pathlib import Path
from time import sleep

import requests


BASE_URL = "https://ps.avalara.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

TAG_RE = re.compile(r"<(?:[^<>\"']|\"[^\"]*\"|'[^']*')+>", re.S)
WHITESPACE_RE = re.compile(r"\s+")
ROW_RE = re.compile(r"<tr(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", re.S | re.I)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
FOLDER_ID_RE = re.compile(
    r'class="[^"]*\bfolder_row\b[^"]*"[^>]*onclick="getDriveFiles\(\'([^\']+)\'\)"',
    re.I,
)
DOWNLOAD_ID_RE = re.compile(r'href=(?:"|)?/GoogleDrive/DownloadFile/([^\s">]+)', re.I)
COOKIE_ROW_RE = re.compile(r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", re.S | re.I)


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = TAG_RE.sub(" ", value)
    value = value.replace("\xa0", " ")
    return WHITESPACE_RE.sub(" ", value).strip()


def sanitize_component(value: str) -> str:
    value = value.replace("/", "_").replace("\0", "")
    return value.strip()


def parse_cookies_from_error_page(path: Path) -> dict[str, str]:
    text = path.read_text()
    start = text.find('<div id="cookiespage"')
    end = text.find('<div id="headerspage"')
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Could not find cookies table in {path}")

    section = text[start:end]
    cookies: dict[str, str] = {}
    for key_html, value_html in COOKIE_ROW_RE.findall(section):
        key = clean_text(key_html)
        value = clean_text(value_html)
        if key and value:
            cookies[key] = value

    headers_start = end
    headers_end = text.find('<div id="routingpage"', headers_start)
    if headers_end == -1:
        headers_end = len(text)
    headers_section = text[headers_start:headers_end]
    for key_html, value_html in COOKIE_ROW_RE.findall(headers_section):
        key = clean_text(key_html).lower()
        value = clean_text(value_html)
        if key != "set-cookie" or "=" not in value:
            continue
        cookie_pair = value.split(";", 1)[0]
        name, cookie_value = cookie_pair.split("=", 1)
        cookies[name] = cookie_value

    if ".AspNetCore.Identity.Application" not in cookies:
        raise ValueError("Required PSQ auth cookie was not found in the error page")
    return cookies


def build_session(cookies: dict[str, str]) -> requests.Session:
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(
        {
            "user-agent": USER_AGENT,
            "accept-language": "en-US,en;q=0.9",
        }
    )
    return session


def fetch_listing(session: requests.Session, folder_id: str | None) -> str:
    path = "/GoogleDrive/_GoogleDriveFiles"
    if folder_id:
        path += f"/{folder_id}"

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(
                BASE_URL + path,
                headers={
                    "accept": "*/*",
                    "connection": "close",
                    "referer": f"{BASE_URL}/GoogleDrive/UploadedFiles",
                    "x-requested-with": "XMLHttpRequest",
                },
                timeout=(20, 20),
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 3:
                break
            sleep(1)

    raise RuntimeError(f"Failed to fetch listing for {path}") from last_error


def parse_listing(html_text: str) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    folders: list[dict[str, str]] = []
    files: list[dict[str, object]] = []

    for match in ROW_RE.finditer(html_text):
        full_row = match.group(0)
        cells = [clean_text(cell) for cell in TD_RE.findall(match.group("body"))]
        if not cells:
            continue

        name = cells[0]
        if not name or name == "Name":
            continue

        folder_match = FOLDER_ID_RE.search(full_row)
        if folder_match:
            folders.append({"id": folder_match.group(1), "name": name})
            continue

        download_match = DOWNLOAD_ID_RE.search(full_row)
        if not download_match:
            continue

        size_raw = cells[1] if len(cells) > 1 else ""
        modified = cells[2] if len(cells) > 2 else ""
        size_bytes = int(size_raw) if size_raw.isdigit() else None
        files.append(
            {
                "name": name,
                "size_bytes": size_bytes,
                "modified": modified,
                "file_id": download_match.group(1),
                "download_path": f"/GoogleDrive/DownloadFile/{download_match.group(1)}",
            }
        )

    return folders, files


def crawl_tree(session: requests.Session) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    seen: set[str | None] = set()
    folders: list[dict[str, object]] = []
    files: list[dict[str, object]] = []

    def walk(folder_id: str | None, path_parts: list[str]) -> None:
        key = folder_id or "__root__"
        if key in seen:
            return
        seen.add(key)

        label = "/".join(path_parts) if path_parts else "<root>"
        print(f"Crawl folder -> {label}", flush=True)
        html_text = fetch_listing(session, folder_id)
        current_id = folder_id or "__root__"
        folders.append({"folder_id": current_id, "path": path_parts.copy()})

        child_folders, child_files = parse_listing(html_text)
        for entry in child_files:
            files.append(
                {
                    "folder_id": current_id,
                    "path": path_parts + [entry["name"]],
                    "file_id": entry["file_id"],
                    "download_path": entry["download_path"],
                    "size_bytes": entry["size_bytes"],
                    "modified": entry["modified"],
                }
            )

        for child in child_folders:
            walk(child["id"], path_parts + [child["name"]])

    walk(None, [])
    return folders, files


def download_file(session: requests.Session, base_output_dir: Path, entry: dict[str, object]) -> Path:
    path_parts = [sanitize_component(part) for part in entry["path"]]
    target = base_output_dir.joinpath(*path_parts)
    target.parent.mkdir(parents=True, exist_ok=True)

    expected_size = entry.get("size_bytes")
    if target.exists() and expected_size and target.stat().st_size == expected_size:
        return target

    response = session.get(
        BASE_URL + str(entry["download_path"]),
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "connection": "close",
            "referer": f"{BASE_URL}/GoogleDrive/UploadedFiles",
            "upgrade-insecure-requests": "1",
        },
        timeout=(20, 120),
        stream=True,
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type.lower():
        preview = response.text[:400]
        raise RuntimeError(f"Unexpected HTML response for {entry['download_path']}: {preview}")

    temp_path = target.with_suffix(target.suffix + ".part")
    bytes_written = 0
    with temp_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 64):
            if not chunk:
                continue
            handle.write(chunk)
            bytes_written += len(chunk)

    if expected_size and bytes_written != expected_size:
        raise RuntimeError(
            f"Size mismatch for {target.name}: expected {expected_size}, got {bytes_written}"
        )

    temp_path.replace(target)
    return target


def write_manifest(output_dir: Path, folders: list[dict[str, object]], files: list[dict[str, object]]) -> None:
    manifest_json = output_dir / "psq-file-storage-manifest.json"
    manifest_csv = output_dir / "psq-file-storage-manifest.csv"
    summary_json = output_dir / "psq-file-storage-summary.json"

    payload = {
        "folder_count": len(folders),
        "file_count": len(files),
        "folders": folders,
        "files": files,
    }
    manifest_json.write_text(json.dumps(payload, indent=2))

    with manifest_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["relative_path", "file_id", "size_bytes", "modified", "download_path"],
        )
        writer.writeheader()
        for entry in files:
            writer.writerow(
                {
                    "relative_path": "/".join(str(part) for part in entry["path"]),
                    "file_id": entry["file_id"],
                    "size_bytes": entry["size_bytes"] or "",
                    "modified": entry["modified"],
                    "download_path": entry["download_path"],
                }
            )

    summary_json.write_text(
        json.dumps(
            {
                "folder_count": len(folders),
                "file_count": len(files),
                "root_output_dir": str((output_dir / "files").resolve()),
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie-source-html", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    files_output_dir = args.output_dir / "files"
    files_output_dir.mkdir(parents=True, exist_ok=True)

    cookies = parse_cookies_from_error_page(args.cookie_source_html)
    session = build_session(cookies)

    print("Crawling PSQ file tree...", flush=True)
    folders, files = crawl_tree(session)
    print(f"Discovered {len(folders)} folders and {len(files)} files.", flush=True)
    write_manifest(args.output_dir, folders, files)

    downloaded = 0
    for entry in files:
        print(f"Crawl save -> {'/'.join(entry['path'])}", flush=True)
        download_file(build_session(cookies), files_output_dir, entry)
        downloaded += 1
        print(f"[{downloaded}/{len(files)}] {'/'.join(entry['path'])}")


if __name__ == "__main__":
    main()
