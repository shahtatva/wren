"""The eval schema: the app's sessions and memories tables and its hybrid_search(), rebuilt
in a separate Postgres schema so eval data never mixes with app data.

The DDL is read from supabase/migrations/ and rewritten for the eval schema, so evals run
the same column definitions, indexes and ranking SQL as the app. Three things differ:
- user_id has no foreign key to auth.users, because eval personas aren't real users.
- memories.kind also allows the eval-only kinds: transcript chunks, single turns, and
  pipeline items (which the app keeps in their own table).
- Only the owner can use it: no role is granted usage, and RLS is on with no policies.
"""

import re
from pathlib import Path

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
SCHEMA = "evals"
EVAL_KINDS = ("story", "feedback", "episode", "vibe", "pipeline", "chunk", "turn")
_TABLES = ("sessions", "memories")
_KEEP = re.compile(
    r"create table public\.(?:sessions|memories)\b"
    r"|create index \w+ on public\.(?:sessions|memories)\b"
    r"|create function public\.hybrid_search\b"
)
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


def split_statements(text: str) -> list[str]:
    """Split SQL on semicolons outside comments, strings and dollar-quoted bodies.

    Comments outside function bodies are dropped.
    """
    statements: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text[i] == "'":
            end = i + 1
            while end < n and not (text[end] == "'" and text[end + 1 : end + 2] != "'"):
                end += 2 if text[end] == "'" else 1  # '' is an escaped quote
            current.append(text[i : end + 1])
            i = end + 1
            continue
        tag = _DOLLAR_TAG.match(text, i)
        if tag:
            end = text.find(tag.group(0), tag.end())
            end = n if end == -1 else end + len(tag.group(0))
            current.append(text[i:end])
            i = end
            continue
        if text[i] == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(text[i])
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _rewrite(statement: str, schema: str) -> str:
    statement = re.sub(r"\s+references auth\.users \(id\) on delete cascade", "", statement)
    statement = statement.replace("public.", f"{schema}.")
    kinds = ", ".join(f"'{kind}'" for kind in EVAL_KINDS)
    return re.sub(r"check \(kind in \([^)]*\)\)", f"check (kind in ({kinds}))", statement)


def eval_schema_sql(schema: str = SCHEMA) -> list[str]:
    statements = [
        _rewrite(statement, schema)
        for path in sorted(MIGRATIONS.glob("*.sql"))
        for statement in split_statements(path.read_text(encoding="utf-8"))
        if _KEEP.match(statement)
    ]
    return statements + [f"alter table {schema}.{table} enable row level security" for table in _TABLES]


def reset(conn: psycopg.Connection, schema: str = SCHEMA) -> None:
    """Drop and rebuild the eval schema. Needs pgvector, which the app's first migration also enables."""
    with conn.transaction():
        conn.execute("create extension if not exists vector with schema extensions")
        conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("revoke all on schema {} from public").format(sql.Identifier(schema)))
        for statement in eval_schema_sql(schema):
            conn.execute(statement)
