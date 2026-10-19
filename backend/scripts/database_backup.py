"""Local PostgreSQL dump/restore with integrity checks; no automatic remote upload.

Read credentials from DATABASE_URL. Restore targets an empty, separately created
database and requires its name to be repeated. Never use --clean or auto-replay payouts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from sqlalchemy.engine import make_url


def connection_env(database_url: str) -> tuple[dict[str, str], str]:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql" or not url.database:
        raise ValueError("DATABASE_URL must identify a PostgreSQL database")
    env = os.environ.copy()
    for name, value in {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
    }.items():
        env[name] = value
    if "sslmode" in url.query:
        env["PGSSLMODE"] = str(url.query["sslmode"])
    return env, url.database


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def backup(path: Path, env: dict[str, str]) -> None:
    path = path.resolve()
    manifest = path.with_suffix(path.suffix + ".sha256.json")
    partial = path.with_suffix(path.suffix + ".partial")
    if any(candidate.exists() for candidate in (path, manifest, partial)):
        raise ValueError("Backup target already exists; choose a new output path")
    path.parent.mkdir(parents=True, exist_ok=True)
    # pg_dump reads credentials from its environment, not process arguments or logs.
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(partial),
        ],
        env=env,
        check=True,
    )
    if not partial.is_file() or partial.stat().st_size == 0:
        raise ValueError("pg_dump produced an empty backup")
    metadata = {
        "format": "postgresql-custom",
        "size": partial.stat().st_size,
        "sha256": digest(partial),
    }
    partial.replace(path)
    with manifest.open("x", encoding="utf-8") as output:
        json.dump(metadata, output)


def restore(path: Path, env: dict[str, str], database: str, confirmation: str) -> None:
    if confirmation != database:
        raise ValueError(
            "--confirm-database must exactly match the target database name"
        )
    path = path.resolve(strict=True)
    manifest = path.with_suffix(path.suffix + ".sha256.json")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        metadata.get("format") != "postgresql-custom"
        or metadata.get("size") != path.stat().st_size
        or metadata.get("sha256") != digest(path)
    ):
        raise ValueError("Backup integrity check failed; restoration was not started")
    # No --clean: existing business tables cause a transactional failure, never deletion.
    subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            database,
            str(path),
        ],
        env=env,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["backup", "restore"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--confirm-database", default="")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        parser.error("Set DATABASE_URL in the process environment")
    env, database = connection_env(url)
    if args.operation == "backup":
        backup(args.file, env)
    else:
        restore(args.file, env, database, args.confirm_database)
    print(f"{args.operation} completed; external transactions were not replayed")


if __name__ == "__main__":
    main()
