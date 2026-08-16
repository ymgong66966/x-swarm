"""Copy every row from one xswarm database into another, in dependency order.

For the move off the SQLite file on one machine and onto a shared Postgres, so the
scheduled workflow sees the same articles, drafts and Typefully draft ids a human has
been approving locally. The destination is created by Alembic first:

    XSWARM_DATABASE_URL=postgresql+psycopg://... .venv/bin/alembic upgrade head
    .venv/bin/python scripts/copy_db.py sqlite:///xswarm.db postgresql+psycopg://...

Refuses a destination that already holds rows: this is a move, not a merge, and merging
two histories of the same draft would double-post.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, func, insert, select, text

from xswarm.models import Base


def copy(source_url: str, target_url: str) -> None:
    source = create_engine(source_url, future=True)
    target = create_engine(target_url, future=True)
    tables = Base.metadata.sorted_tables

    with source.connect() as src, target.begin() as dst:
        for table in tables:
            if dst.execute(select(func.count()).select_from(table)).scalar():
                raise SystemExit(f"{table.name} in the target already has rows; refusing")
        for table in tables:
            rows = [dict(r) for r in src.execute(select(table)).mappings()]
            if not rows:
                print(f"{table.name}: empty")
                continue
            dst.execute(insert(table), rows)
            print(f"{table.name}: {len(rows)}")
        if dst.dialect.name == "postgresql":
            # Sequences still sit at 1 after an explicit-id insert, so the next write
            # would collide with row 1.
            for table in tables:
                for column in table.primary_key:
                    if column.autoincrement and column.type.python_type is int:
                        dst.execute(
                            text(
                                "SELECT setval(pg_get_serial_sequence(:t, :c), "
                                f"coalesce((SELECT max({column.name}) FROM {table.name}), 1))"
                            ),
                            {"t": table.name, "c": column.name},
                        )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: copy_db.py <source-url> <target-url>")
    copy(sys.argv[1], sys.argv[2])
