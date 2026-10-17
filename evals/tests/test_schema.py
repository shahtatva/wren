import re

from evals.schema import MIGRATIONS, eval_schema_sql, split_statements


def test_split_statements_respects_dollar_quotes_strings_and_comments():
    text = (
        "-- a comment; with a semicolon\n"
        "create table t (a text default 'x;y'); -- trailing\n"
        "create function f() returns int language sql as $$ select 1; $$;\n"
    )
    assert split_statements(text) == [
        "create table t (a text default 'x;y')",
        "create function f() returns int language sql as $$ select 1; $$",
    ]


def test_every_statement_targets_the_eval_schema_and_no_auth_users():
    statements = eval_schema_sql("evals")
    assert statements
    for statement in statements:
        assert "public." not in statement
        assert "auth.users" not in statement


def test_tables_indexes_and_hybrid_search_are_carried_over():
    statements = eval_schema_sql("evals")
    raw = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
    app_indexes = len(re.findall(r"create index \w+ on public\.(?:memories|sessions)\b", raw))
    assert sum(s.startswith("create table evals.") for s in statements) == 2
    assert sum(bool(re.match(r"create index \w+ on evals\.", s)) for s in statements) == app_indexes
    (function,) = [s for s in statements if s.startswith("create function evals.hybrid_search")]
    assert "from evals.memories" in function and "join evals.sessions" in function


def test_memories_accepts_the_eval_only_kinds():
    (memories,) = [s for s in eval_schema_sql("evals") if s.startswith("create table evals.memories")]
    for kind in ("story", "vibe", "pipeline", "chunk", "turn"):
        assert f"'{kind}'" in memories


def test_rls_is_on_for_both_tables():
    statements = eval_schema_sql("evals")
    for table in ("sessions", "memories"):
        assert f"alter table evals.{table} enable row level security" in statements
