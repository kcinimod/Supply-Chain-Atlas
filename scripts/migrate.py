"""Tiny forward-only migration runner: applies db/migrations/*.sql in order,
recording each in schema_migrations so re-runs are no-ops.

    uv run python -m scripts.migrate
"""
from __future__ import annotations

import pathlib

from dotenv import load_dotenv

from db.pool import connection

load_dotenv()

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "db" / "migrations"


def main() -> None:
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        pending = [f for f in files if f.name not in applied]
        if not pending:
            print(f"schema up to date ({len(files)} migration(s) applied)")
            return

        for f in pending:
            print(f"applying {f.name} ...")
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (f.name,)
            )
        print(f"applied {len(pending)} migration(s)")


if __name__ == "__main__":
    main()
