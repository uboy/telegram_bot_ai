"""Reindex a knowledge base by re-uploading sources via API."""
import argparse
import os
from pathlib import Path
import sys

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex KB by re-uploading sources")
    parser.add_argument("--api-url", default=os.getenv("BACKEND_API_URL", "http://localhost:8000/api/v1"))
    parser.add_argument("--api-key", default=os.getenv("BACKEND_API_KEY", ""))
    parser.add_argument("--kb-id", type=int, required=True)
    parser.add_argument("--zip-path", type=str, help="Path to zip archive with documents")
    parser.add_argument("--source-dir", type=str, help="Directory with documents to upload")
    parser.add_argument("--clear", action="store_true", help="Clear KB before reindex")
    return parser.parse_args()


def ensure_inputs(args: argparse.Namespace) -> None:
    if not args.zip_path and not args.source_dir:
        raise SystemExit("Provide --zip-path or --source-dir")
    if args.zip_path and args.source_dir:
        raise SystemExit("Use only one of --zip-path or --source-dir")


def request_headers(api_key: str) -> dict:
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def clear_kb(client: httpx.Client, api_url: str, kb_id: int, headers: dict) -> None:
    url = f"{api_url}/knowledge-bases/{kb_id}/clear"
    resp = client.post(url, headers=headers)
    resp.raise_for_status()


def upload_zip(client: httpx.Client, api_url: str, kb_id: int, zip_path: str, headers: dict) -> None:
    path = Path(zip_path)
    if not path.exists():
        raise SystemExit(f"Zip not found: {zip_path}")
    url = f"{api_url}/ingestion/document"
    files = {"file": (path.name, path.read_bytes())}
    data = {
        "knowledge_base_id": str(kb_id),
        "file_name": path.name,
        "file_type": "zip",
    }
    resp = client.post(url, headers=headers, files=files, data=data, timeout=600)
    resp.raise_for_status()


def upload_dir(client: httpx.Client, api_url: str, kb_id: int, source_dir: str, headers: dict) -> None:
    root = Path(source_dir)
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Directory not found: {source_dir}")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise SystemExit("No files found in source dir")

    url = f"{api_url}/ingestion/document"
    for path in files:
        ext = path.suffix.lstrip(".")
        data = {
            "knowledge_base_id": str(kb_id),
            "file_name": path.name,
            "file_type": ext or None,
        }
        file_payload = {"file": (path.name, path.read_bytes())}
        resp = client.post(url, headers=headers, files=file_payload, data=data, timeout=600)
        resp.raise_for_status()


def main() -> int:
    args = parse_args()
    ensure_inputs(args)
    headers = request_headers(args.api_key)

    with httpx.Client() as client:
        if args.clear:
            clear_kb(client, args.api_url, args.kb_id, headers)
        if args.zip_path:
            upload_zip(client, args.api_url, args.kb_id, args.zip_path, headers)
        else:
            upload_dir(client, args.api_url, args.kb_id, args.source_dir, headers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
