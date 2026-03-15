"""Backup and restore helpers for Postgres and Qdrant."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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


def _restore_postgres(*, postgres_service: str, postgres_user: str, postgres_db: str, sql_path: Path) -> None:
    print(f"Restoring Postgres from: {sql_path}")
    with sql_path.open("rb") as handle:
        cmd = [
            "docker",
            "compose",
            "exec",
            "-T",
            postgres_service,
            "psql",
            "-U",
            postgres_user,
            postgres_db,
        ]
        print("$", " ".join(cmd), f"< {sql_path}")
        subprocess.run(cmd, check=True, stdin=handle)


def _upload_qdrant_snapshot(*, qdrant_url: str, collection: str, snapshot_path: Path) -> str:
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/snapshots/upload"
    boundary = f"----secure-rag-{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="snapshot"; filename="{snapshot_path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    file_bytes = snapshot_path.read_bytes()
    body = head + file_bytes + tail

    req = Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(req, timeout=600) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    result = payload.get("result") or {}
    return str(result.get("name") or snapshot_path.name)


def _recover_qdrant_snapshot(*, qdrant_url: str, collection: str, snapshot_name: str) -> None:
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/snapshots/{snapshot_name}/recover"
    req = Request(url=url, method="POST")
    with urlopen(req, timeout=180):
        return


def _load_manifest(backup_dir: Path, manifest_path: Path | None) -> tuple[Path, dict[str, Any]]:
    if manifest_path is not None:
        manifest = manifest_path
    else:
        candidates = sorted(backup_dir.glob("*/manifest.json"))
        if not candidates:
            raise FileNotFoundError(f"No manifest.json found under {backup_dir}")
        manifest = candidates[-1]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid manifest format: {manifest}")
    return manifest, payload


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


def restore(
    *,
    backup_dir: Path,
    manifest_path: Path | None,
    postgres_service: str,
    postgres_user: str,
    postgres_db: str,
    qdrant_url: str,
    skip_postgres: bool,
    skip_qdrant: bool,
) -> None:
    manifest_file, manifest = _load_manifest(backup_dir, manifest_path)
    base_dir = manifest_file.parent

    print(f"Using manifest: {manifest_file}")

    if not skip_postgres:
        pg_dump = manifest.get("postgres_dump")
        if not pg_dump:
            raise ValueError("Manifest does not include postgres_dump")
        sql_path = Path(str(pg_dump))
        if not sql_path.is_absolute():
            sql_path = (base_dir / sql_path).resolve()
        if not sql_path.exists():
            raise FileNotFoundError(f"Postgres dump not found: {sql_path}")
        _restore_postgres(
            postgres_service=postgres_service,
            postgres_user=postgres_user,
            postgres_db=postgres_db,
            sql_path=sql_path,
        )
    else:
        print("Skipping Postgres restore (--skip-postgres).")

    if not skip_qdrant:
        snapshots = manifest.get("qdrant") or []
        if not isinstance(snapshots, list):
            raise ValueError("Manifest qdrant section must be a list")
        for item in snapshots:
            if not isinstance(item, dict):
                continue
            if str(item.get("status")) != "ok":
                continue
            collection = str(item.get("collection") or "").strip()
            file_path = str(item.get("file") or "").strip()
            if not collection or not file_path:
                continue
            snapshot_file = Path(file_path)
            if not snapshot_file.is_absolute():
                snapshot_file = (base_dir / snapshot_file).resolve()
            if not snapshot_file.exists():
                print(f"Skipping missing snapshot file: {snapshot_file}")
                continue
            print(f"Restoring Qdrant snapshot collection={collection} file={snapshot_file}")
            uploaded_name = _upload_qdrant_snapshot(
                qdrant_url=qdrant_url,
                collection=collection,
                snapshot_path=snapshot_file,
            )
            _recover_qdrant_snapshot(
                qdrant_url=qdrant_url,
                collection=collection,
                snapshot_name=uploaded_name,
            )
    else:
        print("Skipping Qdrant restore (--skip-qdrant).")

    print("Restore completed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--backup-dir", default="backups")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--postgres-service", default="postgres")
    parser.add_argument("--postgres-user", default=os.getenv("POSTGRES_USER", "secure_rag"))
    parser.add_argument("--postgres-db", default=os.getenv("POSTGRES_DB", "secure_rag"))
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL_PUBLIC", "http://localhost:6333"))
    parser.add_argument("--qdrant-collections", nargs="+", default=["text_nodes", "image_nodes"])
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument("--skip-qdrant", action="store_true")
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
        restore(
            backup_dir=Path(args.backup_dir),
            manifest_path=Path(args.manifest) if str(args.manifest).strip() else None,
            postgres_service=args.postgres_service,
            postgres_user=args.postgres_user,
            postgres_db=args.postgres_db,
            qdrant_url=args.qdrant_url,
            skip_postgres=bool(args.skip_postgres),
            skip_qdrant=bool(args.skip_qdrant),
        )


if __name__ == "__main__":
    main()
