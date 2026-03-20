"""Reindex a knowledge base using per-document API (RAGIDX-001).

This script reindexes a KB by:
1. Optionally clearing the KB
2. Uploading documents via the existing document API
3. Using the new per-document reindex endpoint for each document
4. Flushing the FAISS index rebuild at the end

Usage:
    # Reindex by uploading from directory
    python scripts/reindex_kb.py --kb-id 1 --source-dir ./docs
    
    # Reindex from zip archive
    python scripts/reindex_kb.py --kb-id 1 --zip-path ./docs.zip
    
    # Clear KB first
    python scripts/reindex_kb.py --kb-id 1 --source-dir ./docs --clear
"""
import argparse
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex KB using per-document API (RAGIDX-001)")
    parser.add_argument("--api-url", default=os.getenv("BACKEND_API_URL", "http://localhost:8000/api/v1"))
    parser.add_argument("--api-key", default=os.getenv("BACKEND_API_KEY", ""))
    parser.add_argument("--kb-id", type=int, required=True)
    parser.add_argument("--zip-path", type=str, help="Path to zip archive with documents")
    parser.add_argument("--source-dir", type=str, help="Directory with documents to upload")
    parser.add_argument("--clear", action="store_true", help="Clear KB before reindex")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers for reindex (default: 4)")
    parser.add_argument("--legacy-upload", action="store_true", help="Use legacy full-upload mode (bypass per-document reindex)")
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
    print(f"KB {kb_id} cleared")


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
    print(f"Zip uploaded: {zip_path}")


def upload_dir(client: httpx.Client, api_url: str, kb_id: int, source_dir: str, headers: dict) -> list[int]:
    """Upload documents from directory and return list of document IDs."""
    root = Path(source_dir)
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Directory not found: {source_dir}")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise SystemExit("No files found in source dir")

    url = f"{api_url}/ingestion/document"
    document_ids = []
    
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
        result = resp.json()
        doc_id = result.get("doc_id") or result.get("document_id")
        if doc_id:
            document_ids.append(int(doc_id))
        print(f"Uploaded: {path.name}")
    
    return document_ids


def reindex_document(client: httpx.Client, api_url: str, kb_id: int, document_id: int, headers: dict) -> tuple[int, bool]:
    """Reindex a single document via API. Returns (document_id, success)."""
    url = f"{api_url}/ingestion/reindex-document"
    payload = {
        "document_id": document_id,
        "knowledge_base_id": kb_id,
    }
    try:
        resp = client.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 404:
            print(f"  Document {document_id} not found (may not have chunks)")
            return document_id, False
        resp.raise_for_status()
        result = resp.json()
        chunks = result.get("chunks_updated", 0)
        print(f"  Document {document_id}: {chunks} chunks reindexed")
        return document_id, True
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            error_code = e.response.headers.get("X-Error-Code", "")
            if error_code == "embedding_model_mismatch":
                print(f"  Document {document_id}: EMBEDDING MODEL MISMATCH - run migration first")
                raise
        print(f"  Document {document_id}: failed - {e}")
        return document_id, False


def flush_index(client: httpx.Client, api_url: str, kb_id: int, headers: dict) -> None:
    """Flush pending FAISS rebuild for KB."""
    url = f"{api_url}/ingestion/flush-index"
    params = {"knowledge_base_id": kb_id}
    resp = client.post(url, headers=headers, params=params, timeout=120)
    resp.raise_for_status()
    result = resp.json()
    rebuilt = result.get("rebuilt_kbs", [])
    if kb_id in rebuilt:
        print(f"FAISS index rebuilt for KB {kb_id}")
    else:
        print(f"FAISS rebuild: KB {kb_id} not in rebuilt list {rebuilt}")


def main() -> int:
    args = parse_args()
    ensure_inputs(args)
    headers = request_headers(args.api_key)

    with httpx.Client() as client:
        if args.clear:
            clear_kb(client, args.api_url, args.kb_id, headers)
        
        if args.zip_path:
            upload_zip(client, args.api_url, args.kb_id, args.zip_path, headers)
            # For zip, we don't have individual document IDs, skip per-document reindex
            print("Zip upload complete. Note: per-document reindex not available for zip archives.")
            return 0
        
        if args.source_dir:
            # Upload documents and get document IDs
            print(f"Uploading documents from {args.source_dir}...")
            document_ids = upload_dir(client, args.api_url, args.kb_id, args.source_dir, headers)
            
            if not document_ids:
                print("No documents uploaded")
                return 0
            
            print(f"\nUploaded {len(document_ids)} documents")
            
            if args.legacy_upload:
                print("Legacy mode: skipping per-document reindex")
                return 0
            
            # Reindex each document using new API
            print(f"\nReindexing documents with {args.workers} workers...")
            start_time = time.time()
            
            success_count = 0
            failure_count = 0
            
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(reindex_document, client, args.api_url, args.kb_id, doc_id, headers): doc_id
                    for doc_id in document_ids
                }
                
                for future in as_completed(futures):
                    doc_id, success = future.result()
                    if success:
                        success_count += 1
                    else:
                        failure_count += 1
            
            elapsed = time.time() - start_time
            print(f"\nReindex complete: {success_count} succeeded, {failure_count} failed in {elapsed:.1f}s")
            
            # Flush FAISS rebuild
            print("\nFlushing FAISS index rebuild...")
            flush_index(client, args.api_url, args.kb_id, headers)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
