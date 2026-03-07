"""Trigger Google Drive ingestion via API."""

from __future__ import annotations

import argparse
import json

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--auth-mode", choices=["oauth", "service_account"], default="oauth")
    parser.add_argument("--dataset-source", default="google_drive")
    args = parser.parse_args()

    payload = {
        "folder_id": args.folder_id,
        "auth_mode": args.auth_mode,
        "dry_run": False,
        "dataset_source": args.dataset_source,
    }

    response = requests.post(f"{args.api_url.rstrip('/')}/ingest/gdrive", json=payload, timeout=300)
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
