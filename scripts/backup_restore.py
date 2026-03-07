"""Backup and restore helpers for Postgres and Qdrant."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def backup() -> None:
    backup_dir = Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    run([
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "secure_rag",
        "secure_rag",
    ])
    run([
        "docker",
        "compose",
        "exec",
        "-T",
        "qdrant",
        "curl",
        "-sS",
        "-X",
        "POST",
        "http://localhost:6333/collections/text_nodes/snapshots",
    ])


def restore() -> None:
    print("Restore flow is environment-specific. Use pg_restore/psql with your chosen backup artifact and Qdrant snapshot upload endpoint.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    args = parser.parse_args()

    if args.action == "backup":
        backup()
    else:
        restore()


if __name__ == "__main__":
    main()
