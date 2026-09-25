"""Apply the additive migration: python -m app.greetings.migrate."""

from pathlib import Path
from typing import LiteralString, cast

import psycopg

from app.config import settings


def main() -> None:
    directory = Path(__file__).resolve().parents[2] / "migrations"
    with psycopg.connect(settings.testing_db_url) as connection:
        for path in sorted(directory.glob("*.sql")):
            # These are idempotent, version-controlled files, never user-provided SQL.
            connection.execute(cast(LiteralString, path.read_text(encoding="utf-8")))
    print("Greeting tables are ready in the business database.")


if __name__ == "__main__":
    main()
