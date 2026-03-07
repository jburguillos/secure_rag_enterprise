"""Trigger local-folder ingestion via API."""

from __future__ import annotations

import argparse
import json

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--path", required=True)
    parser.add_argument("--acl-sidecar", required=True)
    parser.add_argument("--dataset-source", default="local_folder")
    args = parser.parse_args()

    payload = {
        "path": args.path,
        "acl_sidecar_path": args.acl_sidecar,
        "dry_run": False,
        "dataset_source": args.dataset_source,
    }

    response = requests.post(f"{args.api_url.rstrip('/')}/ingest/local", json=payload, timeout=300)
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
