"""Backup and restore helpers for Postgres and Qdrant."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def run(cmd: list[str], *, stdout_path: Path | None = None) -> None:
    print("$", " ".join(cmd))
    if stdout_path is None:
        subprocess.run(cmd, check=True)
        return
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("wb") as handle:
        subprocess.run(cmd, check=True, stdout=handle)


def _http_json(method: str, url: str) -> dict:
    req = Request(url=url, method=method)
    with urlopen(req, timeout=60) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=180) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def backup(
    *,
    root_dir: Path,
    postgres_service: str,
    postgres_user: str,
    postgres_db: str,
    qdrant_url: str,
    qdrant_collections: list[str],
) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = root_dir / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)

    postgres_dump_path = backup_dir / "postgres.sql"
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            postgres_service,
            "pg_dump",
            "-U",
            postgres_user,
            postgres_db,
        ],
        stdout_path=postgres_dump_path,
    )

    snapshot_artifacts: list[dict[str, str]] = []
    for collection in qdrant_collections:
        create_url = f"{qdrant_url.rstrip('/')}/collections/{collection}/snapshots"
        try:
            created = _http_json("POST", create_url)
            snapshot_name = str(created.get("result", {}).get("name") or "")
            if not snapshot_name:
                snapshot_artifacts.append(
                    {
                        "collection": collection,
                        "status": "error",
                        "detail": "snapshot name missing from response",
                    }
                )
                continue

            download_url = f"{qdrant_url.rstrip('/')}/collections/{collection}/snapshots/{snapshot_name}"
            output_path = backup_dir / f"qdrant_{collection}_{snapshot_name}.snapshot"
            _download_file(download_url, output_path)
            snapshot_artifacts.append(
                {
                    "collection": collection,
                    "status": "ok",
                    "snapshot_name": snapshot_name,
                    "file": str(output_path),
                }
            )
        except HTTPError as exc:
            snapshot_artifacts.append(
                {
                    "collection": collection,
                    "status": "skipped",
                    "detail": f"http_{exc.code}",
                }
            )

    manifest = {
        "timestamp_utc": timestamp,
        "postgres_dump": str(postgres_dump_path),
        "qdrant": snapshot_artifacts,
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Backup completed: {backup_dir}")
    print(f"- Postgres dump: {postgres_dump_path}")
    print(f"- Manifest: {manifest_path}")


def restore() -> None:
    print(
        "Restore flow is environment-specific. Use the backup manifest and run:\n"
        "1) psql < postgres.sql\n"
        "2) upload Qdrant snapshot files to /collections/{collection}/snapshots/upload"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--backup-dir", default="backups")
    parser.add_argument("--postgres-service", default="postgres")
    parser.add_argument("--postgres-user", default=os.getenv("POSTGRES_USER", "secure_rag"))
    parser.add_argument("--postgres-db", default=os.getenv("POSTGRES_DB", "secure_rag"))
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL_PUBLIC", "http://localhost:6333"))
    parser.add_argument("--qdrant-collections", nargs="+", default=["text_nodes", "image_nodes"])
    args = parser.parse_args()

    if args.action == "backup":
        backup(
            root_dir=Path(args.backup_dir),
            postgres_service=args.postgres_service,
            postgres_user=args.postgres_user,
            postgres_db=args.postgres_db,
            qdrant_url=args.qdrant_url,
            qdrant_collections=list(args.qdrant_collections),
        )
    else:
        restore()


if __name__ == "__main__":
    main()
