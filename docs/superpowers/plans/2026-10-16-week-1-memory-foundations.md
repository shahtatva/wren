# Week 1: Memory Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** By Thu 2026-10-22, `python -m evals.run --experiment E1` logs a real Recall@5 for the atomic-memory arm, measured on a frozen eval set, in `evals/results.csv`.

**Architecture:** The memory layer lives in `voice/app/memory/`. It covers Gemini calls, extraction, embeddings and Postgres access. It is the same code the voice server will use. The harness in `evals/` imports it rather than reimplementing it, which follows CLAUDE.md: "text mode must use the same pipeline as voice mode". Ground truth is per fact. Session transcripts tag the turns that state each fact, and probes name the facts they ask about. A retrieved memory counts as a hit if its `source_turns` include one of those gold turns.

**Tech Stack:** Python 3.14, google-genai 2.31.0 (Gemini Flash plus Gemini embeddings at 768 dims), psycopg 3.3.6, Supabase Postgres with pgvector, Supabase CLI 2.121.0 via npx, tiktoken 0.14.0, pytest.

**Spec:** [docs/spec.md](../../spec.md) (feature 4: memory; feature 8: eval harness) · Sprint: [docs/sprints/week-1.md](../../sprints/week-1.md) · Rules: [CLAUDE.md](../../../CLAUDE.md)

**Scope:** sprint P0 items 1–4. Stretch items 5 (E1 chunk baselines), 6 (`hybrid_search()`) and 8 (supersede in extraction) are not in this plan. Write a follow-up plan for them only if P0 lands with buffer left. They depend on how extraction v1 actually behaves. Item 7 (the memory-evals skill) is already done.

**Hours:** Task 1 ≈ 0.75 h · 2 ≈ 1 h · 3 ≈ 3 h · 4 ≈ 2 h · 5 ≈ 2 h · 6 ≈ 1 h · 7 ≈ 1 h · 8 ≈ 1 h. Total about 11.75 h, against 11.5 h committed in the sprint.

**Status (2026-10-16): pulled forward for query rewriting and `hybrid_search()`.** Several files below already exist. Add to them; don't overwrite them with this plan's versions.
- **Task 1, steps 3–9:** done, except `evals/requirements.txt` and the `evals/.cache/` ignore line. `Settings` also has `groq_api_key` and `groq_rewrite_model`, and `voice/requirements.txt` adds `groq==1.7.0`. The pinned models are `gemini-3.8-flash`, `gemini-embedding-2` and `openai/gpt-oss-20b`.
- **Task 3, step 1:** `llm.py` has `client()` only. Add `generate_json`.
- **Task 4, step 3 and Task 6, step 3:** `store.py` has `connect`, `to_pgvector` and `hybrid_search_many`. Append the other functions.
- **Task 5, step 1:** `types.py` has `SearchHit` only. Add the other types.
- **Task 5, step 7:** `embed.py` is done but differs on purpose. `gemini-embedding-2` rejects `task_type` and merges a list of plain strings into one vector, so the task goes in as a text prefix and each text is sent as its own `Content`. Keep that version.
- **Also new:** `migrations/*_hybrid_search.sql` (not pushed yet), `app/memory/rewrite.py`, `app/memory/retrieve.py` and `prompts/rewrite.md`. Task 6 should add database tests for `hybrid_search_many`.

**Status (2026-10-16, later): Tasks 2 and 3 done; Tasks 5–8 replaced by a fuller harness.** The ledger in `.superpowers/sdd/` lists every deviation as a ruling.
- **Task 2:** done as written, plus one validator rule: every planned fact must be stated in a user turn.
- **Task 3:** done. Companies that turned out to be real were renamed: Brightline Analytics → Velloway Analytics, Halcyon Health → Vireline Health, Fernway → Tarnwell, Parcelly → Pellwick, Kestrel Software → Vantlow Software, Brindle → Orchardly. maya-f14 moved to October 19. Step 12 (hand-check every probe) is still the user's.
- **Task 5:** `extract.py` follows the repo's `prompts/extract.md`, which returns memories and pipeline updates. It does not follow this plan's older prompt.
- **Tasks 6–8:** replaced by `evals/schema.py` (an `evals` schema built from the migrations), `evals/ingest.py` (chunk512, turn and atomic storage), `evals/experiments.py` (E1–E3) and `evals/run.py`. Nothing goes in `public`, so `evals/accounts.py` and Task 4's RLS tests are still to do.

## Global Constraints

- **Free tiers only.** Ask before adding any paid service or new dependency with its own account. Supabase and Gemini are already in the stack.
- **Never commit `.env` files.** Keep `voice/.env.example` current.
- **Row-level security stays on for every Supabase table.**
- **Embeddings:** Gemini, 768 dimensions (decided 2026-10-16).
- **Recall@5 scoring:** gold turns. Every memory row carries `source_turns` (decided 2026-10-16).
- **Every memory row carries:** kind, text, entities, importance 1–5, timestamps, source session, embedding, tsvector, `superseded_by`, plus `source_turns`.
- **Retrieval budget:** under 300 ms.
- **Rules from the two project skills:**
  - Any change to extraction, chunking, retrieval, ranking or embeddings: run the `memory-evals` skill and log the result.
  - Any prompt, persona card or user-facing copy: follow the `wren-voice` skill. Here that means the extraction prompt and the transcript-generation prompt.
- **`evals/results.csv`:** append-only, and written only by the harness. Columns: `date,commit,experiment,variant,change,recall_at_5,tokens_per_turn,stale_fact_rate,latency_p50_ms,latency_p95_ms`.
- **Eval set content:** fictional people and companies only, because the repo is public.
- **Model IDs:** live in env vars (`GEMINI_FLASH_MODEL`, `GEMINI_EMBED_MODEL`), pinned to exact IDs, never `-latest` aliases. Don't pass `temperature`, since newer Gemini Flash models deprecate it. The disk cache keeps runs comparable.
- **Pinned versions:**
  - fastapi 0.143.2
  - uvicorn[standard] 0.54.0
  - google-genai 2.31.0
  - psycopg[binary] 3.3.6
  - python-dotenv 1.2.4
  - tiktoken 0.14.0
  - Supabase CLI 2.121.0
- **Never pin Postgres extension versions in migrations.** Supabase now ignores the pin.
- **Supabase grants:** new tables aren't exposed to the Data API by default, so grant `authenticated` explicitly and never `anon`.

## Review Focus

1. **Approximate vector index plus per-user filter.** An HNSW scan filtered by `user_id` can return fewer than 5 rows when other users' rows are nearer, which silently lowers recall. `vector_search` turns on pgvector's iterative scan (`hnsw.iterative_scan = strict_order`). Task 6 tests that 60 nearer rows from another user don't crowd out the 5 results. A rolled-back trial run on 2026-10-16 showed that at this scale Postgres picks the per-user index plus an exact sort over HNSW, so searches are exact for now. HNSW takes over once a user has thousands of memories.
2. **One persona's memories showing up in another's results.** Task 4 tests this at the RLS level, and Task 6 tests the `vector_search` filter.
3. **Rerunning E1 duplicating rows.** Ingest must replace a user's data, not add to it. Task 6 runs ingest twice and checks the count.
4. **`results.csv` making every run "dirty".** Writing the CSV changes the working tree, so the commit check has to ignore `evals/results.csv`. Task 7 tests this.
5. **Probe questions that copy transcript wording.** That inflates recall. The validator flags any probe that shares 5 or more consecutive words with a gold turn. Task 2 tests this.

---

### Task 1: Accounts, config and dependencies

**Files:**
- Modify: `voice/requirements.txt`, `voice/.env.example`, `.gitignore`
- Create: `evals/requirements.txt`, `voice/app/config.py`, `voice/tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` (frozen dataclass with fields `gemini_api_key`, `gemini_flash_model`, `gemini_embed_model`, `database_url`, `supabase_url`, `supabase_secret_key`), `app.config.load_settings() -> Settings`, `app.config.MissingSetting(RuntimeError)`, `app.config.EMBED_DIM = 768`.

- [ ] **Step 1: Branch and commit the pending planning work**

```bash
git switch -c week-1-memory-foundations
git add CLAUDE.md docs/spec.md docs/sprints/week-1.md docs/superpowers/plans/ .claude/skills/ evals/results.csv evals/run.py
git commit -m "Add spec, week-1 sprint and plan, memory-evals and wren-voice skills"
```

- [ ] **Step 2 (user): Create the accounts**

1. In Supabase, create a free project named `wren-dev` in the nearest region. Leave "Automatically expose new tables" unchecked; the migration grants access explicitly. Save the database password.
2. From the project's **Connect** dialog, copy the **Session pooler** connection string. It works over IPv4, while the direct connection is IPv6-only on the free tier. Percent-encode any special characters in the password.
3. From **Project Settings → API Keys**, copy the project URL and a **secret** key.
4. In Google AI Studio, create a Gemini API key.

Note: free Supabase projects pause after 7 days without activity. Only synthetic data goes into this project, because free tiers may use submitted data.

- [ ] **Step 3: Add dependencies**

`voice/requirements.txt`:

```
fastapi==0.143.2
uvicorn[standard]==0.54.0
google-genai==2.31.0
psycopg[binary]==3.3.6
python-dotenv==1.2.4
```

`evals/requirements.txt`:

```
-r ../voice/requirements-dev.txt
tiktoken==0.14.0
```

Append to `.gitignore`:

```
# Eval harness cache (LLM outputs, embeddings, tokenizer files)
evals/.cache/
```

Run from the repo root with `voice/.venv` active:

```bash
pip install -r evals/requirements.txt
```

Expected: installs without building from source. All four new packages have cp314 Windows wheels or are pure Python.

- [ ] **Step 4: Write the failing config test**

`voice/tests/test_config.py`:

```python
from pathlib import Path

import pytest

from app import config

NAMES = [
    "GEMINI_API_KEY",
    "GEMINI_FLASH_MODEL",
    "GEMINI_EMBED_MODEL",
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
]


@pytest.fixture
def no_env_file(monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE", Path("does-not-exist.env"))


def test_loads_all_settings(monkeypatch, no_env_file):
    for name in NAMES:
        monkeypatch.setenv(name, f"value-of-{name}")
    settings = config.load_settings()
    assert settings.database_url == "value-of-DATABASE_URL"
    assert settings.gemini_embed_model == "value-of-GEMINI_EMBED_MODEL"


def test_missing_setting_names_the_variable(monkeypatch, no_env_file):
    for name in NAMES:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("DATABASE_URL", "  ")
    with pytest.raises(config.MissingSetting, match="DATABASE_URL"):
        config.load_settings()
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd voice && pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'config'`.

- [ ] **Step 6: Implement `voice/app/config.py`**

```python
"""Settings for the voice server and the eval harness, read from voice/.env."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
EMBED_DIM = 768  # fixed by the memories.embedding column


class MissingSetting(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_flash_model: str
    gemini_embed_model: str
    database_url: str
    supabase_url: str
    supabase_secret_key: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSetting(f"{name} is not set. Add it to voice/.env (see voice/.env.example).")
    return value


def load_settings() -> Settings:
    load_dotenv(ENV_FILE, override=False)
    return Settings(
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_flash_model=_require("GEMINI_FLASH_MODEL"),
        gemini_embed_model=_require("GEMINI_EMBED_MODEL"),
        database_url=_require("DATABASE_URL"),
        supabase_url=_require("SUPABASE_URL"),
        supabase_secret_key=_require("SUPABASE_SECRET_KEY"),
    )
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd voice && pytest -v`
Expected: PASS (3 tests, including `test_health`).

- [ ] **Step 8: Pick and pin the model IDs**

List the available models from the repo root, after putting `GEMINI_API_KEY` in `voice/.env`:

```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv('voice/.env'); from google import genai; c = genai.Client(api_key=os.environ['GEMINI_API_KEY']); [print(m.name) for m in c.models.list() if 'flash' in m.name or 'embedding' in m.name]"
```

Pick these:
- The newest stable (non-preview, non-`latest`) Flash model. As of 2026-10-16 that is likely `gemini-3.6-flash`.
- The newest text-embedding model that supports `output_dimensionality=768`.

Then confirm the embedding size:

```bash
python -c "import os; from dotenv import load_dotenv; load_dotenv('voice/.env'); from google import genai; from google.genai import types; c = genai.Client(api_key=os.environ['GEMINI_API_KEY']); r = c.models.embed_content(model=os.environ['GEMINI_EMBED_MODEL'], contents=['hello'], config=types.EmbedContentConfig(task_type='RETRIEVAL_QUERY', output_dimensionality=768)); print(len(r.embeddings[0].values))"
```

Expected: `768`.

- [ ] **Step 9: Update `voice/.env.example` and check the database connection**

Replace the Supabase block in `voice/.env.example` and add the model block. Put the IDs from Step 8 in place of `<flash-id>` and `<embedding-id>`:

```
# Gemini model IDs. Pin exact IDs so eval runs stay comparable.
GEMINI_FLASH_MODEL=<flash-id>
GEMINI_EMBED_MODEL=<embedding-id>

# Supabase (Postgres + pgvector)
SUPABASE_URL=
SUPABASE_SECRET_KEY=
# Session pooler connection string (Connect > Session pooler), password percent-encoded,
# with ?sslmode=require on the end so the driver never falls back to plaintext
DATABASE_URL=
```

Fill in the same keys in `voice/.env`, then check the connection:

```bash
python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('voice/.env'); print(psycopg.connect(os.environ['DATABASE_URL']).execute('select version()').fetchone()[0])"
```

Expected: a `PostgreSQL 17.x` (or 15.x) version string.

- [ ] **Step 10: Commit**

```bash
git add voice/requirements.txt voice/.env.example .gitignore evals/requirements.txt voice/app/config.py voice/tests/test_config.py
git commit -m "Add settings, Gemini and Postgres dependencies"
```

---

### Task 2: Eval set format, loader and validator

**Files:**
- Modify: `evals/__init__.py`
- Create: `evals/evalset.py`, `evals/tests/__init__.py`, `evals/tests/test_evalset.py`
- Create fixtures: `evals/tests/fixtures/mini/personas/ana.json`, `evals/tests/fixtures/mini/sessions/ana-s01.json`, `evals/tests/fixtures/mini/sessions/ana-s02.json`, `evals/tests/fixtures/mini/probes.json`

**Interfaces:**
- Produces, in `evals.evalset`:
  - frozen dataclasses `Fact(id, text, kind, supersedes)`, `SessionPlan(id, date, mode, outline, facts)`, `Persona(id, name, profile, facts: dict[str, Fact], sessions: tuple[SessionPlan, ...])`, `EvalTurn(id, speaker, text, facts)`, `Transcript(id, persona, date, mode, turns: tuple[EvalTurn, ...])`, `Probe(id, persona, question, answer, facts, tags)`, `EvalSet(personas, transcripts, probes)`
  - `DATA_DIR`
  - `load_eval_set(root: Path = DATA_DIR) -> EvalSet`
  - `gold_turns(evalset, probe) -> set[str]`
  - `stale_turns(evalset, probe) -> set[str]`
  - `shared_run(a: str, b: str, n: int = 5) -> str | None`
  - `validate(evalset) -> list[str]`
  - `summary(evalset) -> str`
  - CLI `python -m evals.evalset [--show-probes PERSONA]`
- Turn IDs are `"<session id>:tNN"`, for example `maya-s03:t07`.

- [ ] **Step 1: Make `evals` import the app's memory code**

Replace `evals/__init__.py`:

```python
"""Wren eval harness.

The harness imports the app's memory code from voice/app, so evals exercise the
same pipeline the app uses (CLAUDE.md: text mode and voice mode share one pipeline).
"""

import sys
from pathlib import Path

_VOICE_DIR = Path(__file__).resolve().parents[1] / "voice"
if str(_VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_DIR))
```

Create an empty `evals/tests/__init__.py`.

- [ ] **Step 2: Write the fixture eval set**

`evals/tests/fixtures/mini/personas/ana.json`:

```json
{
  "id": "ana",
  "name": "Ana Reyes",
  "profile": "Bootcamp graduate targeting junior analyst roles.",
  "facts": [
    {"id": "ana-f01", "text": "Oakridge Labs phone screen is on Tuesday October 6", "kind": "episode"},
    {"id": "ana-f02", "text": "Oakridge Labs phone screen moved to Thursday October 8", "kind": "episode", "supersedes": "ana-f01"},
    {"id": "ana-f03", "text": "Built a churn dashboard for a food bank during the bootcamp", "kind": "story"}
  ],
  "sessions": [
    {"id": "ana-s01", "date": "2026-10-01", "mode": "checkin", "outline": "Ana mentions the Oakridge screen and her food bank dashboard.", "facts": ["ana-f01", "ana-f03"]},
    {"id": "ana-s02", "date": "2026-10-05", "mode": "checkin", "outline": "Ana says the Oakridge screen moved.", "facts": ["ana-f02"]}
  ]
}
```

`evals/tests/fixtures/mini/sessions/ana-s01.json`:

```json
{
  "id": "ana-s01", "persona": "ana", "date": "2026-10-01", "mode": "checkin",
  "turns": [
    {"id": "ana-s01:t01", "speaker": "wren", "text": "Hi Ana. What's on your plate this week?", "facts": []},
    {"id": "ana-s01:t02", "speaker": "user", "text": "Oakridge booked my phone screen for Tuesday the sixth.", "facts": ["ana-f01"]},
    {"id": "ana-s01:t03", "speaker": "wren", "text": "Nice. Which story will you lead with?", "facts": []},
    {"id": "ana-s01:t04", "speaker": "user", "text": "Probably the food bank one, the churn dashboard I built at bootcamp.", "facts": ["ana-f03"]}
  ]
}
```

`evals/tests/fixtures/mini/sessions/ana-s02.json`:

```json
{
  "id": "ana-s02", "persona": "ana", "date": "2026-10-05", "mode": "checkin",
  "turns": [
    {"id": "ana-s02:t01", "speaker": "wren", "text": "Welcome back. How's the week going?", "facts": []},
    {"id": "ana-s02:t02", "speaker": "user", "text": "Oakridge pushed the screen to Thursday the eighth.", "facts": ["ana-f02"]}
  ]
}
```

`evals/tests/fixtures/mini/probes.json`:

```json
[
  {"id": "p-ana-01", "persona": "ana", "question": "When is my call with Oakridge now?", "answer": "Thursday October 8", "facts": ["ana-f02"], "tags": ["episode", "changed_fact"]},
  {"id": "p-ana-02", "persona": "ana", "question": "Which project shows off my analytics skills?", "answer": "The food bank churn dashboard", "facts": ["ana-f03"], "tags": ["story"]}
]
```

- [ ] **Step 3: Write the failing tests**

`evals/tests/test_evalset.py`:

```python
from dataclasses import replace
from pathlib import Path

from evals.evalset import gold_turns, load_eval_set, shared_run, stale_turns, validate

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_fixture_is_valid():
    assert validate(load_eval_set(FIXTURE)) == []


def test_gold_turns_follow_fact_tags():
    evalset = load_eval_set(FIXTURE)
    probe = evalset.probes[1]
    assert gold_turns(evalset, probe) == {"ana-s01:t04"}


def test_stale_turns_follow_supersedes_chain():
    evalset = load_eval_set(FIXTURE)
    probe = evalset.probes[0]
    assert gold_turns(evalset, probe) == {"ana-s02:t02"}
    assert stale_turns(evalset, probe) == {"ana-s01:t02"}


def test_probe_without_gold_turn_is_an_error():
    evalset = load_eval_set(FIXTURE)
    persona = evalset.personas["ana"]
    extra = replace(persona.facts["ana-f03"], id="ana-f99", text="Never said")
    personas = {"ana": replace(persona, facts={**persona.facts, "ana-f99": extra})}
    probe = replace(evalset.probes[1], facts=("ana-f99",))
    broken = replace(evalset, personas=personas, probes=(probe,))
    assert any("no turn states" in e for e in validate(broken))


def test_probe_that_copies_transcript_wording_is_flagged():
    evalset = load_eval_set(FIXTURE)
    probe = replace(evalset.probes[0], question="Oakridge pushed the screen to Thursday the eighth?")
    errors = validate(replace(evalset, probes=(probe,)))
    assert any("repeats" in e for e in errors)


def test_unknown_fact_tag_is_an_error():
    evalset = load_eval_set(FIXTURE)
    transcript = evalset.transcripts["ana-s02"]
    turn = replace(transcript.turns[1], facts=("ana-f42",))
    bad = replace(transcript, turns=(transcript.turns[0], turn))
    errors = validate(replace(evalset, transcripts={**evalset.transcripts, "ana-s02": bad}))
    assert any("unknown fact ana-f42" in e for e in errors)


def test_planned_fact_never_stated_is_an_error():
    evalset = load_eval_set(FIXTURE)
    transcript = evalset.transcripts["ana-s01"]
    bad = replace(transcript, turns=transcript.turns[:2])
    errors = validate(replace(evalset, transcripts={**evalset.transcripts, "ana-s01": bad}))
    assert any("never stated" in e and "ana-f03" in e for e in errors)


def test_shared_run_ignores_short_overlaps():
    assert shared_run("tell me about yourself", "so, tell me about yourself please") is None
    assert shared_run("the food bank churn dashboard", "I built the food bank churn dashboard") == "the food bank churn dashboard"
```

- [ ] **Step 4: Run them to verify they fail**

Run: `pytest evals/tests/test_evalset.py -v` (from the repo root)
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.evalset'`.

- [ ] **Step 5: Implement `evals/evalset.py`**

```python
"""The frozen eval set: personas, their facts, session transcripts and probes.

Files live in evals/data/. Facts are the unit of ground truth: transcripts tag
the turns that state a fact, and probes name the facts they ask about. Recall@5
checks whether a retrieved item cites one of those gold turns.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
OVERLAP_WORDS = 5  # a probe sharing this many consecutive words with a gold turn leaks its answer
SPEAKERS = ("user", "wren")


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    kind: str
    supersedes: str | None = None


@dataclass(frozen=True)
class SessionPlan:
    id: str
    date: str
    mode: str
    outline: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    profile: str
    facts: dict[str, Fact]
    sessions: tuple[SessionPlan, ...]


@dataclass(frozen=True)
class EvalTurn:
    id: str
    speaker: str
    text: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Transcript:
    id: str
    persona: str
    date: str
    mode: str
    turns: tuple[EvalTurn, ...]


@dataclass(frozen=True)
class Probe:
    id: str
    persona: str
    question: str
    answer: str
    facts: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class EvalSet:
    personas: dict[str, Persona]
    transcripts: dict[str, Transcript]
    probes: tuple[Probe, ...]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_eval_set(root: Path = DATA_DIR) -> EvalSet:
    personas = {}
    for path in sorted((root / "personas").glob("*.json")):
        raw = _read(path)
        facts = {f["id"]: Fact(f["id"], f["text"], f["kind"], f.get("supersedes")) for f in raw["facts"]}
        sessions = tuple(
            SessionPlan(s["id"], s["date"], s["mode"], s["outline"], tuple(s["facts"])) for s in raw["sessions"]
        )
        personas[raw["id"]] = Persona(raw["id"], raw["name"], raw["profile"], facts, sessions)

    transcripts = {}
    for path in sorted((root / "sessions").glob("*.json")):
        raw = _read(path)
        turns = tuple(EvalTurn(t["id"], t["speaker"], t["text"], tuple(t.get("facts", []))) for t in raw["turns"])
        transcripts[raw["id"]] = Transcript(raw["id"], raw["persona"], raw["date"], raw["mode"], turns)

    probes_path = root / "probes.json"
    probes = ()
    if probes_path.exists():
        probes = tuple(
            Probe(p["id"], p["persona"], p["question"], p["answer"], tuple(p["facts"]), tuple(p.get("tags", [])))
            for p in _read(probes_path)
        )
    return EvalSet(personas, transcripts, probes)


def _turns_stating(evalset: EvalSet, persona_id: str, fact_ids: set[str]) -> set[str]:
    return {
        turn.id
        for transcript in evalset.transcripts.values()
        if transcript.persona == persona_id
        for turn in transcript.turns
        if fact_ids & set(turn.facts)
    }


def gold_turns(evalset: EvalSet, probe: Probe) -> set[str]:
    return _turns_stating(evalset, probe.persona, set(probe.facts))


def _superseded_by_chain(persona: Persona, fact_ids: tuple[str, ...]) -> set[str]:
    stale: set[str] = set()
    frontier = list(fact_ids)
    while frontier:
        fact = persona.facts.get(frontier.pop())
        if fact and fact.supersedes and fact.supersedes not in stale:
            stale.add(fact.supersedes)
            frontier.append(fact.supersedes)
    return stale


def stale_turns(evalset: EvalSet, probe: Probe) -> set[str]:
    persona = evalset.personas[probe.persona]
    old = _superseded_by_chain(persona, probe.facts)
    return _turns_stating(evalset, probe.persona, old) - gold_turns(evalset, probe)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def shared_run(a: str, b: str, n: int = OVERLAP_WORDS) -> str | None:
    """The first run of n consecutive words that appears in both texts, if any."""
    words_a = _words(a)
    grams = {tuple(words_a[i : i + n]) for i in range(len(words_a) - n + 1)}
    words_b = _words(b)
    for i in range(len(words_b) - n + 1):
        gram = tuple(words_b[i : i + n])
        if gram in grams:
            return " ".join(gram)
    return None


def validate(evalset: EvalSet) -> list[str]:
    errors: list[str] = []
    plans = {}
    for persona in evalset.personas.values():
        for fact in persona.facts.values():
            if fact.supersedes and fact.supersedes not in persona.facts:
                errors.append(f"{fact.id}: supersedes unknown fact {fact.supersedes}")
        for plan in persona.sessions:
            plans[plan.id] = (persona, plan)
            for fact_id in plan.facts:
                if fact_id not in persona.facts:
                    errors.append(f"{plan.id}: plans unknown fact {fact_id}")
            if plan.id not in evalset.transcripts:
                errors.append(f"{plan.id}: no transcript yet (run python -m evals.generate)")

    turn_text = {}
    for transcript in evalset.transcripts.values():
        if transcript.id not in plans:
            errors.append(f"{transcript.id}: transcript has no session plan")
            continue
        persona, plan = plans[transcript.id]
        if transcript.persona != persona.id:
            errors.append(f"{transcript.id}: persona {transcript.persona} doesn't match plan {persona.id}")
        stated: set[str] = set()
        for turn in transcript.turns:
            if not turn.id.startswith(f"{transcript.id}:t"):
                errors.append(f"{turn.id}: turn ID must start with {transcript.id}:t")
            if turn.id in turn_text:
                errors.append(f"{turn.id}: duplicate turn ID")
            turn_text[turn.id] = turn.text
            if turn.speaker not in SPEAKERS:
                errors.append(f"{turn.id}: unknown speaker {turn.speaker}")
            for fact_id in turn.facts:
                if fact_id not in persona.facts:
                    errors.append(f"{turn.id}: unknown fact {fact_id}")
            stated |= set(turn.facts)
        missing = sorted(set(plan.facts) - stated)
        if missing:
            errors.append(f"{transcript.id}: planned facts never stated: {', '.join(missing)}")

    seen_probes: set[str] = set()
    for probe in evalset.probes:
        if probe.id in seen_probes:
            errors.append(f"{probe.id}: duplicate probe ID")
        seen_probes.add(probe.id)
        persona = evalset.personas.get(probe.persona)
        if persona is None:
            errors.append(f"{probe.id}: unknown persona {probe.persona}")
            continue
        for fact_id in probe.facts:
            if fact_id not in persona.facts:
                errors.append(f"{probe.id}: unknown fact {fact_id}")
        gold = gold_turns(evalset, probe)
        if not gold:
            errors.append(f"{probe.id}: no turn states its facts")
        for turn_id in sorted(gold):
            leak = shared_run(probe.question, turn_text.get(turn_id, ""))
            if leak:
                errors.append(f"{probe.id}: question repeats '{leak}' from {turn_id}")
    return errors


def summary(evalset: EvalSet) -> str:
    planned = sum(len(p.sessions) for p in evalset.personas.values())
    changed = sum(
        1 for probe in evalset.probes if probe.persona in evalset.personas and stale_turns(evalset, probe)
    )
    return (
        f"{len(evalset.personas)} personas, {len(evalset.transcripts)}/{planned} transcripts, "
        f"{len(evalset.probes)} probes ({changed} changed-fact)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the eval set, or print probes for hand-checking.")
    parser.add_argument("--show-probes", metavar="PERSONA", help="print each probe with its gold and stale turns")
    args = parser.parse_args(argv)
    evalset = load_eval_set()
    if args.show_probes:
        turns = {t.id: t for tr in evalset.transcripts.values() for t in tr.turns}
        for probe in evalset.probes:
            if probe.persona != args.show_probes:
                continue
            print(f"\n{probe.id}: {probe.question}\n  answer: {probe.answer}")
            for turn_id in sorted(gold_turns(evalset, probe)):
                print(f"  gold  {turn_id}: {turns[turn_id].text}")
            for turn_id in sorted(stale_turns(evalset, probe)):
                print(f"  stale {turn_id}: {turns[turn_id].text}")
    errors = validate(evalset)
    print(summary(evalset))
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest evals/tests/test_evalset.py -v`
Expected: PASS (8 tests).

- [ ] **Step 7: Commit**

```bash
git add evals/__init__.py evals/evalset.py evals/tests/
git commit -m "Add eval set format, loader and validator"
```

---

### Task 3: Eval set content and transcript generator

**Files:**
- Create: `voice/app/memory/__init__.py` (empty), `voice/app/memory/llm.py`
- Create: `evals/prompts/generate_session.md`, `evals/generate.py`, `evals/tests/test_generate.py`
- Create data: `evals/data/personas/maya.json`, `evals/data/personas/dev.json`, `evals/data/personas/sam.json`, `evals/data/sessions/*.json` (generated), `evals/data/probes.json`

**Interfaces:**
- Consumes: `app.config.Settings`, `load_settings()`. From `evals.evalset`: `DATA_DIR`, `Persona`, `SessionPlan`, `load_eval_set`, `validate`.
- Produces:
  - `app.memory.llm.client(settings) -> genai.Client`
  - `app.memory.llm.generate_json(prompt: str, schema: type[T], settings) -> T`, where `T` is a pydantic `BaseModel`
  - `evals.generate.build_prompt(persona, plan, earlier) -> str`
  - `evals.generate.to_transcript_json(persona_id, plan, generated) -> dict`
  - the committed eval set

- [ ] **Step 1: Write `voice/app/memory/llm.py`**

```python
"""Gemini calls shared by extraction, eval generation and (later) compaction."""

from functools import lru_cache
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import Settings

T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=4)
def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def client(settings: Settings) -> genai.Client:
    return _client(settings.gemini_api_key)


def generate_json(prompt: str, schema: type[T], settings: Settings) -> T:
    """One structured-output call. No temperature: newer Flash models deprecate it,
    and the eval harness caches outputs instead."""
    response = client(settings).models.generate_content(
        model=settings.gemini_flash_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
    )
    return schema.model_validate_json(response.text)
```

- [ ] **Step 2: Write the generation prompt**

Follow the `wren-voice` skill. The synthetic Wren lines must sound like Wren, because extraction will read them.

`evals/prompts/generate_session.md`:

```
You are writing a realistic transcript of one spoken session between a job seeker and Wren, a voice companion app. The transcript is test data for a memory system. It must state certain facts clearly enough to be remembered, inside natural conversation.

## Wren
{persona_card}

## The job seeker
Name: {name}
Background: {profile}

## Earlier sessions
{history}

## This session
Date: {date}
Mode: {mode}. A checkin is a casual check-in. A mock is a mock interview: Wren steps into an interviewer role, then steps out and debriefs.
What happens: {outline}

Facts the job seeker must state in this session:
{facts}

## Rules
- Write 14 to 24 turns, alternating speakers, starting with Wren.
- Wren follows its persona card: short spoken sentences, one question per turn, warm and honest.
- The job seeker talks like a real person speaking out loud, with some filler, tangents and small talk.
- State every listed fact in at least one job-seeker turn, in natural words. Never copy the fact text.
- Don't add other concrete facts about jobs, dates, companies or stories.
- In each turn's "facts" list, put the IDs of the listed facts that turn states. Leave it empty otherwise.
- Use only fictional people and companies.
```

- [ ] **Step 3: Write the failing generator tests**

`evals/tests/test_generate.py`:

```python
from pathlib import Path

from evals.evalset import load_eval_set
from evals.generate import GeneratedSession, GeneratedTurn, build_prompt, to_transcript_json

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_transcript_json_numbers_turns_and_cleans_facts():
    persona = load_eval_set(FIXTURE).personas["ana"]
    plan = persona.sessions[0]
    generated = GeneratedSession(
        turns=[
            GeneratedTurn(speaker="wren", text=" Hi Ana. ", facts=[]),
            GeneratedTurn(speaker="user", text="Screen is Tuesday.", facts=["ana-f01", "ana-f01"]),
        ]
    )
    data = to_transcript_json("ana", plan, generated)
    assert [t["id"] for t in data["turns"]] == ["ana-s01:t01", "ana-s01:t02"]
    assert data["turns"][0]["text"] == "Hi Ana."
    assert data["turns"][1]["facts"] == ["ana-f01"]
    assert (data["persona"], data["date"], data["mode"]) == ("ana", "2026-10-01", "checkin")


def test_prompt_includes_facts_history_and_persona_card():
    persona = load_eval_set(FIXTURE).personas["ana"]
    prompt = build_prompt(persona, persona.sessions[1], list(persona.sessions[:1]))
    assert "ana-f02: Oakridge Labs phone screen moved to Thursday October 8" in prompt
    assert "2026-10-01: Ana mentions the Oakridge screen" in prompt
    assert "You are Wren" in prompt
```

- [ ] **Step 4: Run them to verify they fail**

Run: `pytest evals/tests/test_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.generate'`.

- [ ] **Step 5: Implement `evals/generate.py`**

```python
"""Write missing session transcripts from persona outlines: python -m evals.generate

Each transcript is generated once and committed, so the eval set stays frozen.
Delete a file in evals/data/sessions/ to regenerate that session.
"""

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from evals.evalset import DATA_DIR, Persona, SessionPlan, load_eval_set

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "generate_session.md"
PERSONA_CARD = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "wren-voice" / "references" / "persona-card.md"
)


class GeneratedTurn(BaseModel):
    speaker: Literal["user", "wren"]
    text: str
    facts: list[str]


class GeneratedSession(BaseModel):
    turns: list[GeneratedTurn]


def build_prompt(persona: Persona, plan: SessionPlan, earlier: list[SessionPlan]) -> str:
    facts = "\n".join(f"- {fact_id}: {persona.facts[fact_id].text}" for fact_id in plan.facts)
    history = "\n".join(f"- {p.date}: {p.outline}" for p in earlier) or "- (this is the first session)"
    return PROMPT_FILE.read_text(encoding="utf-8").format(
        persona_card=PERSONA_CARD.read_text(encoding="utf-8"),
        name=persona.name,
        profile=persona.profile,
        history=history,
        date=plan.date,
        mode=plan.mode,
        outline=plan.outline,
        facts=facts,
    )


def to_transcript_json(persona_id: str, plan: SessionPlan, generated: GeneratedSession) -> dict:
    turns = [
        {"id": f"{plan.id}:t{i:02d}", "speaker": t.speaker, "text": t.text.strip(), "facts": sorted(set(t.facts))}
        for i, t in enumerate(generated.turns, start=1)
    ]
    return {"id": plan.id, "persona": persona_id, "date": plan.date, "mode": plan.mode, "turns": turns}


def main() -> int:
    from app.config import load_settings
    from app.memory.llm import generate_json

    settings = load_settings()
    evalset = load_eval_set()
    out_dir = DATA_DIR / "sessions"
    out_dir.mkdir(exist_ok=True)
    for persona in evalset.personas.values():
        for i, plan in enumerate(persona.sessions):
            path = out_dir / f"{plan.id}.json"
            if path.exists():
                continue
            generated = generate_json(build_prompt(persona, plan, list(persona.sessions[:i])), GeneratedSession, settings)
            data = to_transcript_json(persona.id, plan, generated)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {path.name} ({len(data['turns'])} turns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest evals/tests -v`
Expected: PASS (10 tests).

- [ ] **Step 7: Write persona Maya**

This is the complete persona that `dev.json` and `sam.json` must match in shape. `evals/data/personas/maya.json`:

```json
{
  "id": "maya",
  "name": "Maya Okafor",
  "profile": "Maya (she/her) finished an MS in Information Management in May 2026 and is targeting business development rep roles at SaaS companies.",
  "facts": [
    {"id": "maya-f01", "kind": "story", "text": "Led a five-person consulting-club project that cut a nonprofit's donor churn by 12 percent"},
    {"id": "maya-f02", "kind": "story", "text": "The consulting-club result was actually a 15 percent drop in donor churn once the final quarter's data came in", "supersedes": "maya-f01"},
    {"id": "maya-f03", "kind": "story", "text": "Cold-called 40 alumni in one week to fill a career-fair sponsor gap and landed three sponsors"},
    {"id": "maya-f04", "kind": "story", "text": "As campus IT help desk lead, calmed an angry professor by walking them through a live data-recovery fix"},
    {"id": "maya-f05", "kind": "story", "text": "Built a Tableau dashboard of library study-room bookings that the library still uses"},
    {"id": "maya-f06", "kind": "feedback", "text": "Rambles on 'tell me about yourself', running past two minutes"},
    {"id": "maya-f07", "kind": "feedback", "text": "Her openers land when she leads with a number"},
    {"id": "maya-f08", "kind": "feedback", "text": "Forgets to ask the interviewer questions at the end"},
    {"id": "maya-f09", "kind": "episode", "text": "Brightline Analytics recruiter screen booked for Thursday October 8 at 2pm"},
    {"id": "maya-f10", "kind": "episode", "text": "Brightline Analytics moved the recruiter screen to Friday October 9 at 11am", "supersedes": "maya-f09"},
    {"id": "maya-f11", "kind": "episode", "text": "Corvid Labs rejected her for the SDR role after the hiring-manager round"},
    {"id": "maya-f12", "kind": "episode", "text": "A Corvid Labs recruiter reached out about a new BDR team opening in November", "supersedes": "maya-f11"},
    {"id": "maya-f13", "kind": "episode", "text": "Quillbase sent a take-home: a 30-60-90 day plan due October 2"},
    {"id": "maya-f14", "kind": "episode", "text": "Tessellate final-round panel on October 13 with four interviewers"},
    {"id": "maya-f15", "kind": "vibe", "text": "Gets nervous before panel interviews; her mind goes blank with more than two people in the room"},
    {"id": "maya-f16", "kind": "vibe", "text": "Felt flat and discouraged the day after the Corvid rejection"},
    {"id": "maya-f17", "kind": "vibe", "text": "Felt energized after a mock where her opener landed"},
    {"id": "maya-f18", "kind": "episode", "text": "Wants a referral at Tessellate and knows a classmate there, Priya"}
  ],
  "sessions": [
    {"id": "maya-s01", "date": "2026-09-01", "mode": "checkin", "outline": "First session. Maya introduces her search, tells the consulting-club story, and admits panels make her nervous.", "facts": ["maya-f01", "maya-f15"]},
    {"id": "maya-s02", "date": "2026-09-04", "mode": "mock", "outline": "Mock BDR screen. Her 'tell me about yourself' rambles. She tells the alumni cold-calling story. Wren debriefs.", "facts": ["maya-f06", "maya-f03"]},
    {"id": "maya-s03", "date": "2026-09-10", "mode": "checkin", "outline": "Corvid Labs rejected her after the hiring-manager round. She feels flat.", "facts": ["maya-f11", "maya-f16"]},
    {"id": "maya-s04", "date": "2026-09-15", "mode": "mock", "outline": "Mock with a hiring-manager persona. She tells the help desk story and opens with a number, which lands. She feels energized.", "facts": ["maya-f04", "maya-f07", "maya-f17"]},
    {"id": "maya-s05", "date": "2026-09-22", "mode": "checkin", "outline": "Logistics: the Quillbase take-home and a newly booked Brightline screen.", "facts": ["maya-f13", "maya-f09"]},
    {"id": "maya-s06", "date": "2026-09-29", "mode": "mock", "outline": "Mock for Quillbase. She tells the Tableau dashboard story and forgets to ask questions at the end.", "facts": ["maya-f05", "maya-f08"]},
    {"id": "maya-s07", "date": "2026-10-03", "mode": "checkin", "outline": "Brightline moved her screen. She also corrects the consulting-club number.", "facts": ["maya-f10", "maya-f02"]},
    {"id": "maya-s08", "date": "2026-10-07", "mode": "checkin", "outline": "Surprise: Corvid Labs is back in touch about a different opening.", "facts": ["maya-f12"]},
    {"id": "maya-s09", "date": "2026-10-10", "mode": "checkin", "outline": "The Tessellate final round is a four-person panel. Wren nudges her toward a referral and she names a classmate.", "facts": ["maya-f14", "maya-f18"]},
    {"id": "maya-s10", "date": "2026-10-14", "mode": "mock", "outline": "Panel-style mock prep. She retells the alumni story and talks about panel nerves.", "facts": ["maya-f03", "maya-f15"]}
  ]
}
```

- [ ] **Step 8: Write personas Dev and Sam in the same shape**

Each persona needs:
- 10 sessions dated between 2026-09-01 and 2026-10-14, 3 of them `mock`.
- 15–20 facts: at least 5 `story`, 3 `feedback`, 5 `episode` and 2 `vibe`.
- The three traps, each written as a later fact with `supersedes` pointing at the earlier one:
  1. A rescheduled event.
  2. A story retold with different details.
  3. A company that rejects them and later re-engages.

The persona briefs (all people and companies fictional):

- **`dev.json`, Dev Raman (he/him):** former high-school math teacher moving into data analyst roles, with self-taught SQL and Python.
  - Traps: the Halcyon Health take-home deadline moves from October 5 to October 7. His grading-dashboard story is first told as built in Excel, later corrected to Google Sheets with Apps Script. Fernway rejects him after a technical screen, then re-engages him for a junior analyst opening.
  - Feedback: undersells his technical work; explains SQL clearly.
  - Vibe: discouraged after rejections, upbeat after a good technical screen.
  - Referral: a former colleague at Ledgerly.
- **`sam.json`, Sam Ortiz (they/them):** retail store manager of 6 years moving into customer success roles.
  - Traps: the Parcelly final round moves from Monday October 12 to Wednesday October 14. Their de-escalation story is first told ending with the customer leaving happy, later retold with the customer becoming a regular who wrote a review. Kestrel Software rejects them, then re-engages.
  - Feedback: strong stories but a weak "why this company"; talks fast when nervous.
  - Vibe: tired from working full-time during the search.
  - Referral: a neighbor who works at Brindle.

If a company name turns out to belong to a real company, rename it.

- [ ] **Step 9: Generate the transcripts**

Run: `python -m evals.generate`
Expected: `wrote maya-s01.json (… turns)` and so on, 30 files in `evals/data/sessions/`.

- [ ] **Step 10: Validate and regenerate failures**

Run: `python -m evals.evalset`
Expected at this point: `3 personas, 30/30 transcripts, 0 probes (0 changed-fact)` and no errors.

For each `planned facts never stated` or `unknown fact` error, delete that session file and rerun `python -m evals.generate`. If the same session fails twice, make its outline more explicit about the missing fact.

- [ ] **Step 11: Write the probes**

Write 20 probes per persona, 60 in all, in `evals/data/probes.json`. Phrase them in the first person, the way the user would ask Wren. Paraphrase rather than reuse transcript wording.

Each persona's 20:
- 7 `story` probes
- 4 `feedback` probes
- 5 `episode` probes, including the 3 trap probes tagged `changed_fact`
- 2 `vibe` probes
- 2 probes that span two facts, such as "Which companies still want to talk to me?"

The first entries:

```json
[
  {"id": "p-maya-01", "persona": "maya", "question": "When's my Brightline call now?", "answer": "Friday October 9 at 11am", "facts": ["maya-f10"], "tags": ["episode", "changed_fact"]},
  {"id": "p-maya-02", "persona": "maya", "question": "How much did donor churn drop in my consulting project?", "answer": "15 percent", "facts": ["maya-f02"], "tags": ["story", "changed_fact"]},
  {"id": "p-maya-03", "persona": "maya", "question": "Is Corvid still a possibility for me?", "answer": "Yes, a recruiter reached out about a November BDR opening", "facts": ["maya-f12"], "tags": ["episode", "changed_fact"]},
  {"id": "p-maya-04", "persona": "maya", "question": "What keeps tripping up my self-introduction?", "answer": "It runs long, past two minutes", "facts": ["maya-f06"], "tags": ["feedback"]}
]
```

Run: `python -m evals.evalset`
Expected: `3 personas, 30/30 transcripts, 60 probes (9 changed-fact)` and no errors. Reword any probe flagged with `repeats`.

- [ ] **Step 12 (user): Hand-check every probe**

Run `python -m evals.evalset --show-probes maya`, then `dev`, then `sam`. For each probe, check three things:
1. The gold turns really contain the answer.
2. The stale turns hold the old version.
3. The question can be answered from memory alone.

Fix the probe or delete and regenerate the session, then re-validate.

- [ ] **Step 13: Commit the frozen eval set**

```bash
git add voice/app/memory/ evals/prompts/ evals/generate.py evals/tests/test_generate.py evals/data/
git commit -m "Add eval set v1: 3 personas, 30 sessions, 60 probes"
```

---

### Task 4: Memory schema with RLS

The migration is already written, to the column list given on 2026-10-16: [supabase/migrations/20261016163502_memory_schema.sql](../../../supabase/migrations/20261016163502_memory_schema.sql). It parses with the Postgres 17 parser and passes checks for RLS, grants and foreign-key indexes. That file is the schema plan CLAUDE.md asks for. If it has to change during execution, show the user the changed SQL before pushing.

**Files:**
- Already written: `supabase/migrations/20261016163502_memory_schema.sql`
- Create: `voice/app/memory/store.py`, `evals/accounts.py`, `evals/tests/test_db.py`

**Interfaces:**
- Consumes: `app.config.Settings`, `load_settings()`.
- Produces:
  - Tables `public.profiles`, `public.pipeline`, `public.sessions`, `public.stories`, `public.memories`, all with RLS on and grants only to `authenticated` and `service_role`
  - `app.memory.store.connect(database_url: str) -> psycopg.Connection` (autocommit)
  - `evals.accounts.ensure_user(settings, email: str) -> uuid.UUID`
  - The `users` and `conn` pytest fixtures in `evals/tests/test_db.py`

- [ ] **Step 1: Review the migration and check pgvector**

Read the migration once against CLAUDE.md's memory design. Then check the pgvector version, because iterative scan needs 0.8 or later:

```bash
python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('voice/.env'); print(psycopg.connect(os.environ['DATABASE_URL']).execute(\"select default_version from pg_available_extensions where name = 'vector'\").fetchone()[0])"
```

Expected: `0.8.0` or later. If it's older, stop and tell the user. `vector_search`'s iterative scan won't work, so the HNSW indexes would starve filtered queries.

- [ ] **Step 2: Dry-run, then push**

Bash, from the repo root with `voice/.env` loaded into the shell:

```bash
set -a; source voice/.env; set +a
npx supabase@2.121.0 db push --dry-run --db-url "$DATABASE_URL"
npx supabase@2.121.0 db push --db-url "$DATABASE_URL"
```

Expected: the dry run lists `<timestamp>_memory_schema.sql`, and the push reports it applied. Then run `npx supabase@2.121.0 db advisors --help`. If the command accepts `--db-url`, run it and fix any security finding. If it doesn't, open **Advisors** in the dashboard.

- [ ] **Step 3: Write `voice/app/memory/store.py`, starting with the connection**

```python
"""Postgres access for memories.

Week 1 queries run as the database owner and filter by user_id explicitly.
Per-request RLS (running as `authenticated`) arrives with the /turn endpoint.
"""

import psycopg


def connect(database_url: str) -> psycopg.Connection:
    # prepare_threshold=None: Supabase's pooler doesn't support prepared statements in all modes.
    return psycopg.connect(database_url, autocommit=True, prepare_threshold=None)
```

- [ ] **Step 4: Write `evals/accounts.py`**

```python
"""Eval and test users, created through the Supabase Auth admin API.

Every table's user_id references auth.users, so personas and RLS tests need real
users. The admin API is the supported way to create them (not raw SQL on auth.*).
"""

import json
import urllib.request
from urllib.parse import urlsplit
from uuid import UUID

from app.config import Settings

PAGE_SIZE = 200


def _origin(url: str) -> str:
    """https://<ref>.supabase.co, even if the setting was copied with a path like /rest/v1/."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _admin(settings: Settings, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        _origin(settings.supabase_url) + "/auth/v1/admin/" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "apikey": settings.supabase_secret_key,
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def find_user(settings: Settings, email: str) -> UUID | None:
    page = 1
    while True:
        users = _admin(settings, "GET", f"users?page={page}&per_page={PAGE_SIZE}").get("users", [])
        for user in users:
            if user.get("email") == email:
                return UUID(user["id"])
        if len(users) < PAGE_SIZE:
            return None
        page += 1


def ensure_user(settings: Settings, email: str) -> UUID:
    existing = find_user(settings, email)
    if existing:
        return existing
    created = _admin(settings, "POST", "users", {"email": email, "email_confirm": True})
    return UUID(created["id"])
```

- [ ] **Step 5: Write the RLS tests**

`evals/tests/test_db.py`:

```python
"""Database tests. They write to the dev project, so they only run with WREN_DB_TESTS=1."""

import json
import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("WREN_DB_TESTS") != "1", reason="set WREN_DB_TESTS=1 to run database tests"
)


@pytest.fixture(scope="module")
def settings():
    from app.config import load_settings

    return load_settings()


@pytest.fixture(scope="module")
def conn(settings):
    from app.memory.store import connect

    with connect(settings.database_url) as connection:
        yield connection


@pytest.fixture(scope="module")
def users(settings, conn):
    from evals.accounts import ensure_user

    a = ensure_user(settings, "rls-test-a@example.com")
    b = ensure_user(settings, "rls-test-b@example.com")
    yield a, b
    for user_id in (a, b):
        conn.execute("delete from public.sessions where user_id = %s", (user_id,))


def _act_as(conn, user_id):
    conn.execute("set local role authenticated")
    claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
    conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))


def test_rls_hides_other_users_rows(conn, users):
    a, b = users
    with conn.transaction(force_rollback=True):
        session_id = conn.execute(
            "insert into public.sessions (user_id, external_id, mode, started_at, transcript) "
            "values (%s, 'rls-test', 'checkin', now(), '[]') returning id",
            (a,),
        ).fetchone()[0]
        _act_as(conn, b)
        assert conn.execute("select count(*) from public.sessions where id = %s", (session_id,)).fetchone()[0] == 0
        _act_as(conn, a)
        assert conn.execute("select count(*) from public.sessions where id = %s", (session_id,)).fetchone()[0] == 1


def test_rls_blocks_writing_rows_for_someone_else(conn, users):
    a, b = users
    with conn.transaction(force_rollback=True):
        _act_as(conn, b)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into public.sessions (user_id, mode, started_at, transcript) "
                "values (%s, 'checkin', now(), '[]')",
                (a,),
            )


def test_anon_has_no_access(conn):
    with conn.transaction(force_rollback=True):
        conn.execute("set local role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from public.memories")
```

- [ ] **Step 6: Run the database tests**

Bash: `WREN_DB_TESTS=1 pytest evals/tests/test_db.py -v`
PowerShell: `$env:WREN_DB_TESTS = "1"; pytest evals/tests/test_db.py -v`
Expected: PASS (3 tests). If `set local role authenticated` fails with a permission error, stop. Fetch Supabase's [Monitoring and Debugging](https://supabase.com/docs/guides/monitoring-and-debugging.md) docs before changing anything.

Then run `pytest evals/tests -v` with `WREN_DB_TESTS` unset.
Expected: the 3 database tests are skipped and the rest pass.

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/ voice/app/memory/store.py evals/accounts.py evals/tests/test_db.py
git commit -m "Add memory schema with RLS on every table"
```

---

### Task 5: Extraction and embeddings

**Files:**
- Create: `voice/app/memory/types.py`, `voice/app/memory/extract.py`, `voice/app/memory/prompts/extract.md`, `voice/app/memory/embed.py`, `voice/tests/test_extract.py`, `evals/ingest.py`, `evals/tests/test_ingest.py`
- Modify: `.claude/skills/memory-evals/SKILL.md` (the temperature line)

**Interfaces:**
- Consumes: `app.config.Settings`, `EMBED_DIM`; `app.memory.llm.generate_json`, `client`; `evals.evalset.Transcript`.
- Produces:
  - `app.memory.types`: `Turn(id, speaker, text)`, `Session(id, started_at, mode, turns)`, `ExtractedMemory(kind, text, entities: tuple, importance: int, source_turns: tuple)`, `SearchHit(id, kind, text, source_turns, score)`, `MEMORY_KINDS`
  - `app.memory.extract`: `format_transcript(session) -> str`, `build_prompt(session) -> str`, `prompt_text() -> str`, `ExtractionResult`, `parse_extraction(result, session) -> list[ExtractedMemory]`, `extract_memories(session, settings) -> list[ExtractedMemory]`
  - `app.memory.embed.embed_texts(texts: list[str], task: Literal["document", "query"], settings) -> list[list[float]]`
  - `evals.ingest.to_app_session(transcript) -> Session`

- [ ] **Step 1: Write `voice/app/memory/types.py`**

```python
"""Plain data types shared by extraction, storage and retrieval."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Speaker = Literal["user", "wren"]
MEMORY_KINDS = ("story", "feedback", "episode", "vibe")


@dataclass(frozen=True)
class Turn:
    id: str
    speaker: Speaker
    text: str


@dataclass(frozen=True)
class Session:
    id: str  # external id, e.g. "maya-s03" in evals
    started_at: datetime
    mode: str  # "checkin" or "mock"
    turns: tuple[Turn, ...]


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    text: str
    entities: tuple[str, ...]
    importance: int
    source_turns: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    id: str
    kind: str
    text: str
    source_turns: tuple[str, ...]
    score: float
```

- [ ] **Step 2: Write the extraction prompt**

Follow the `wren-voice` rules for background prompts: feelings in the user's own words, no clinical labels, and no crisis content stored as everyday memory.

`voice/app/memory/prompts/extract.md`:

```
You read one finished session between a job seeker (User) and Wren, a voice companion that helps them rehearse interviews. Pull out the memories worth keeping for future sessions.

Session date: {date}
Session mode: {mode}

Transcript (each turn starts with its ID in brackets):
{transcript}

Write atomic memories. Each memory is one claim, short enough to read in one breath, written about the user in the third person ("The user ..."), and understandable without the transcript.

Kinds:
- story: something the user did that they could tell in an interview, such as a project, a conflict or a result. Keep names, numbers and outcomes.
- feedback: a pattern in how the user answers, such as rambling, underselling or a strong opener. Say who noticed it, if Wren or an interviewer did.
- episode: something that happened in their search, such as an interview booked, moved or done, a rejection, an offer, or a recruiter getting in touch. Include the company, the stage and the date in plain words. Also write exactly one episode that sums up this session in one sentence.
- vibe: how the user said they felt, in their own words and tied to a situation ("nervous before panel interviews"). Never use clinical or diagnostic labels.

Rules:
- One claim per memory. Split compound sentences.
- Turn relative dates ("Thursday", "next week") into absolute dates, using the session date.
- If the user corrects or changes something they said earlier, write the new version as its own memory.
- Only include what the user said or clearly confirmed. Never record Wren's suggestions as facts about the user.
- importance: 5 for interview dates, offers, rejections and signature stories; 3 for useful detail; 1 for small talk worth little.
- entities: the companies, people, roles and tools the memory mentions, as written.
- source_turns: the IDs of the turns the memory comes from. Use only IDs that appear in the transcript.
- Leave out anything about self-harm or crisis. That is handled elsewhere and must never become an everyday memory.
- Skip greetings, and small talk with no lasting value.
```

- [ ] **Step 3: Write the failing extraction tests**

`voice/tests/test_extract.py`:

```python
from datetime import datetime, timezone

from app.memory.extract import ExtractionResult, build_prompt, format_transcript, parse_extraction
from app.memory.types import Session, Turn

SESSION = Session(
    id="s1",
    started_at=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
    mode="checkin",
    turns=(
        Turn("s1:t01", "wren", "How was the week?"),
        Turn("s1:t02", "user", "Brightline moved my screen to Friday."),
    ),
)


def _item(**overrides):
    item = {"kind": "episode", "text": "The user's Brightline screen moved to Friday October 9.",
            "entities": ["Brightline"], "importance": 5, "source_turns": ["s1:t02"]}
    item.update(overrides)
    return item


def _parse(*items):
    return parse_extraction(ExtractionResult.model_validate({"memories": list(items)}), SESSION)


def test_format_transcript_labels_turns_and_speakers():
    assert format_transcript(SESSION) == "[s1:t01] Wren: How was the week?\n[s1:t02] User: Brightline moved my screen to Friday."


def test_build_prompt_fills_date_mode_and_transcript():
    prompt = build_prompt(SESSION)
    assert "Session date: 2026-10-03" in prompt
    assert "Session mode: checkin" in prompt
    assert "[s1:t02] User: Brightline moved my screen to Friday." in prompt


def test_unknown_turn_ids_are_dropped():
    (memory,) = _parse(_item(source_turns=["s1:t02", "s1:t99"]))
    assert memory.source_turns == ("s1:t02",)


def test_memory_with_no_valid_turns_is_dropped():
    assert _parse(_item(source_turns=["s9:t01"])) == []


def test_importance_is_clamped_to_1_through_5():
    low, high = _parse(_item(importance=0), _item(importance=9))
    assert (low.importance, high.importance) == (1, 5)


def test_unknown_kind_is_dropped():
    assert _parse(_item(kind="diagnosis")) == []


def test_text_whitespace_and_duplicate_entities_are_cleaned():
    (memory,) = _parse(_item(text="  The user   moved\nthe screen. ", entities=["Brightline", " Brightline", ""]))
    assert memory.text == "The user moved the screen."
    assert memory.entities == ("Brightline",)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `cd voice && pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.memory.extract'`.

- [ ] **Step 5: Implement `voice/app/memory/extract.py`**

```python
"""Turn a finished session into atomic memories, one claim each.

Runs after the session, never on the live path. Profile and pipeline get their own
tables later; until then, schedule changes are kept as episode memories too.
"""

from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.memory.llm import generate_json
from app.memory.types import MEMORY_KINDS, ExtractedMemory, Session

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "extract.md"
SPEAKER_LABELS = {"user": "User", "wren": "Wren"}


class _Item(BaseModel):
    kind: str
    text: str
    entities: list[str]
    importance: int
    source_turns: list[str]


class ExtractionResult(BaseModel):
    memories: list[_Item]


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def format_transcript(session: Session) -> str:
    return "\n".join(f"[{t.id}] {SPEAKER_LABELS[t.speaker]}: {t.text}" for t in session.turns)


def build_prompt(session: Session) -> str:
    return prompt_text().format(
        date=session.started_at.date().isoformat(), mode=session.mode, transcript=format_transcript(session)
    )


def parse_extraction(result: ExtractionResult, session: Session) -> list[ExtractedMemory]:
    """Keep only well-formed memories that cite real turns of this session."""
    known = {t.id for t in session.turns}
    memories = []
    for item in result.memories:
        text = " ".join(item.text.split())
        turns = tuple(dict.fromkeys(t for t in item.source_turns if t in known))
        if item.kind not in MEMORY_KINDS or not text or not turns:
            continue
        entities = tuple(dict.fromkeys(e.strip() for e in item.entities if e.strip()))
        memories.append(ExtractedMemory(item.kind, text, entities, min(5, max(1, item.importance)), turns))
    return memories


def extract_memories(session: Session, settings: Settings) -> list[ExtractedMemory]:
    return parse_extraction(generate_json(build_prompt(session), ExtractionResult, settings), session)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd voice && pytest -v`
Expected: PASS (10 tests).

- [ ] **Step 7: Implement `voice/app/memory/embed.py`**

```python
"""Gemini embeddings at a fixed 768 dimensions, the size of memories.embedding."""

from typing import Literal

from google.genai import types

from app.config import EMBED_DIM, Settings
from app.memory.llm import client

BATCH_SIZE = 100
TASK_TYPES = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}


def embed_texts(texts: list[str], task: Literal["document", "query"], settings: Settings) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        response = client(settings).models.embed_content(
            model=settings.gemini_embed_model,
            contents=texts[start : start + BATCH_SIZE],
            config=types.EmbedContentConfig(task_type=TASK_TYPES[task], output_dimensionality=EMBED_DIM),
        )
        vectors.extend(list(e.values) for e in response.embeddings)
    if any(len(v) != EMBED_DIM for v in vectors):
        raise ValueError(f"{settings.gemini_embed_model} returned vectors that aren't {EMBED_DIM}-dimensional")
    return vectors
```

- [ ] **Step 8: Write the failing ingest-conversion test**

`evals/tests/test_ingest.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

from evals.evalset import load_eval_set
from evals.ingest import to_app_session

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_to_app_session_keeps_ids_speakers_and_date():
    session = to_app_session(load_eval_set(FIXTURE).transcripts["ana-s01"])
    assert session.id == "ana-s01"
    assert session.started_at == datetime(2026, 10, 1, 18, tzinfo=timezone.utc)
    assert session.mode == "checkin"
    assert [(t.id, t.speaker) for t in session.turns][:2] == [("ana-s01:t01", "wren"), ("ana-s01:t02", "user")]
```

Run: `pytest evals/tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.ingest'`.

- [ ] **Step 9: Implement `evals/ingest.py`**

```python
"""Load eval transcripts into the database through the app's own memory code."""

from datetime import datetime

from app.memory.types import Session, Turn
from evals.evalset import Transcript


def to_app_session(transcript: Transcript) -> Session:
    return Session(
        id=transcript.id,
        started_at=datetime.fromisoformat(f"{transcript.date}T18:00:00+00:00"),
        mode=transcript.mode,
        turns=tuple(Turn(t.id, t.speaker, t.text) for t in transcript.turns),
    )
```

Run: `pytest evals/tests -v`
Expected: PASS, with the database tests skipped.

- [ ] **Step 10: Smoke-test extraction on one real session**

```bash
python - <<'EOF'
import evals
from app.config import load_settings
from app.memory.embed import embed_texts
from app.memory.extract import extract_memories
from evals.evalset import load_eval_set
from evals.ingest import to_app_session

settings = load_settings()
session = to_app_session(load_eval_set().transcripts["maya-s07"])
memories = extract_memories(session, settings)
for m in memories:
    print(m.kind, m.importance, m.source_turns, m.text)
print(len(embed_texts([m.text for m in memories], "document", settings)[0]))
EOF
```

Expected: roughly 4–10 memories. They should include an episode about Brightline moving to Friday October 9 and a story with the 15 percent figure, each citing `maya-s07:tNN` turns. The last line prints `768`. If memories bundle several claims, or record Wren's suggestions as the user's facts, tighten the prompt and rerun this step.

- [ ] **Step 11: Fix the temperature advice in the memory-evals skill**

In `.claude/skills/memory-evals/SKILL.md`, replace:

```
- Steps that call an LLM (extraction, query rewriting, HyDE) vary between runs. Use temperature 0 and cache outputs by input and prompt version, so reruns measure the change and not sampling noise. If you invalidate the cache, say so in `change`.
```

with:

```
- Steps that call an LLM (extraction, query rewriting, HyDE) vary between runs, and newer Gemini Flash models no longer accept a temperature setting. Cache outputs by model, prompt version and input (the harness does this in `evals/.cache/`), so reruns measure the change and not sampling noise. If you clear the cache, say so in `change`.
```

- [ ] **Step 12: Commit**

```bash
git add voice/app/memory/ voice/tests/test_extract.py evals/ingest.py evals/tests/test_ingest.py .claude/skills/memory-evals/SKILL.md
git commit -m "Add extraction v1 and Gemini embeddings"
```

---

### Task 6: Store memories and vector search

**Files:**
- Modify: `voice/app/memory/store.py`, `evals/ingest.py`, `evals/tests/test_db.py`

**Interfaces:**
- Consumes: `connect`; `Session`, `ExtractedMemory`, `SearchHit`, `Turn`; `to_app_session`; the `users` and `conn` fixtures.
- Produces:
  - `store.to_pgvector(vector) -> str`
  - `store.delete_user_data(conn, user_id) -> None`
  - `store.insert_session(conn, user_id, session) -> UUID`
  - `store.insert_memories(conn, user_id, session_db_id, memories, embeddings) -> list[UUID]`
  - `store.vector_search(conn, user_id, query_embedding, k=5) -> list[SearchHit]`
  - `evals.ingest.ingest_persona(conn, user_id, transcripts, extract, embed) -> int`, where `extract: Callable[[Session], list[ExtractedMemory]]` and `embed: Callable[[list[str]], list[list[float]]]`

- [ ] **Step 1: Write the failing database tests**

Append to `evals/tests/test_db.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def _unit(i: int) -> list[float]:
    vector = [0.0] * 768
    vector[i] = 1.0
    return vector


def _near(i: int) -> list[float]:
    """Close to _unit(7), and distinct from one another."""
    vector = _unit(7)
    vector[100 + i] = 0.01
    return vector


def _seed(conn, user_id, vectors: list[list[float]], offset: int) -> list:
    from app.memory.store import insert_memories, insert_session
    from app.memory.types import ExtractedMemory, Session, Turn

    turn_id = f"db-test-{offset}:t01"
    session = Session(f"db-test-{offset}", datetime(2026, 10, 1, tzinfo=timezone.utc), "checkin",
                      (Turn(turn_id, "user", "hi"),))
    session_db_id = insert_session(conn, user_id, session)
    memories = [ExtractedMemory("story", f"memory {offset + i}", (), 3, (turn_id,)) for i in range(len(vectors))]
    return insert_memories(conn, user_id, session_db_id, memories, vectors)


@pytest.fixture
def clean(conn, users):
    from app.memory.store import delete_user_data

    for user_id in users:
        delete_user_data(conn, user_id)
    yield users
    for user_id in users:
        delete_user_data(conn, user_id)


def test_vector_search_returns_k_rows_from_own_user_only(conn, clean):
    from app.memory.store import vector_search

    a, b = clean
    a_ids = {str(i) for i in _seed(conn, a, [_unit(i) for i in range(6)], 0)}
    _seed(conn, b, [_near(i) for i in range(60)], 100)  # 60 rows nearer the query than any of a's
    hits = vector_search(conn, a, _unit(7), k=5)
    assert len(hits) == 5
    assert {h.id for h in hits} <= a_ids


def test_vector_search_skips_superseded_memories(conn, clean):
    from app.memory.store import vector_search

    a, _ = clean
    old, new = _seed(conn, a, [_unit(0), _unit(1)], 0)
    conn.execute("update public.memories set superseded_by = %s where id = %s", (new, old))
    assert str(old) not in {h.id for h in vector_search(conn, a, _unit(0), k=5)}


def test_ingest_twice_replaces_instead_of_duplicating(conn, clean):
    from app.memory.types import ExtractedMemory
    from evals.evalset import load_eval_set
    from evals.ingest import ingest_persona

    a, _ = clean
    transcripts = [t for t in load_eval_set(FIXTURE).transcripts.values() if t.persona == "ana"]

    def fake_extract(session):
        return [ExtractedMemory("episode", f"{session.id} summary", (), 3, (session.turns[0].id,))]

    def fake_embed(texts):
        return [_unit(i) for i in range(len(texts))]

    assert ingest_persona(conn, a, transcripts, fake_extract, fake_embed) == 2
    assert ingest_persona(conn, a, transcripts, fake_extract, fake_embed) == 2
    count = conn.execute("select count(*) from public.memories where user_id = %s", (a,)).fetchone()[0]
    assert count == 2
```

Move the `datetime` and `Path` imports up to the top of the file, next to the existing imports.

- [ ] **Step 2: Run them to verify they fail**

Run: `WREN_DB_TESTS=1 pytest evals/tests/test_db.py -v`
Expected: the 3 new tests FAIL with `ImportError: cannot import name 'insert_memories'`. The 3 RLS tests still pass.

- [ ] **Step 3: Add the storage functions to `voice/app/memory/store.py`**

Append:

```python
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from app.memory.types import ExtractedMemory, SearchHit, Session

USER_TABLES = ("memories", "stories", "sessions", "pipeline", "profiles")


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def delete_user_data(conn: psycopg.Connection, user_id: UUID) -> None:
    with conn.transaction():
        for table in USER_TABLES:
            conn.execute(
                sql.SQL("delete from {} where user_id = %s").format(sql.Identifier("public", table)), (user_id,)
            )


def insert_session(conn: psycopg.Connection, user_id: UUID, session: Session) -> UUID:
    transcript = [{"id": t.id, "speaker": t.speaker, "text": t.text} for t in session.turns]
    return conn.execute(
        "insert into public.sessions (user_id, external_id, mode, started_at, transcript) "
        "values (%s, %s, %s, %s, %s) returning id",
        (user_id, session.id, session.mode, session.started_at, Jsonb(transcript)),
    ).fetchone()[0]


def insert_memories(
    conn: psycopg.Connection,
    user_id: UUID,
    session_db_id: UUID,
    memories: list[ExtractedMemory],
    embeddings: list[list[float]],
) -> list[UUID]:
    if len(memories) != len(embeddings):
        raise ValueError(f"{len(memories)} memories but {len(embeddings)} embeddings")
    ids = []
    with conn.transaction():
        for memory, embedding in zip(memories, embeddings):
            row = conn.execute(
                "insert into public.memories "
                "(user_id, kind, text, entities, importance, source_session, source_turns, embedding) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s::extensions.vector) returning id",
                (user_id, memory.kind, memory.text, list(memory.entities), memory.importance,
                 session_db_id, list(memory.source_turns), to_pgvector(embedding)),
            ).fetchone()
            ids.append(row[0])
    return ids


def vector_search(
    conn: psycopg.Connection, user_id: UUID, query_embedding: list[float], k: int = 5
) -> list[SearchHit]:
    """Cosine search over the user's current memories, nearest first.

    HNSW is approximate and the user filter applies after it, so iterative scan
    (pgvector 0.8+) keeps searching until k rows pass the filter. strict_order keeps
    exact distance order. ORDER BY must be the distance alone, or Postgres can't use
    the HNSW index at all.
    """
    vector = to_pgvector(query_embedding)
    with conn.transaction():
        conn.execute("set local hnsw.iterative_scan = strict_order")
        rows = conn.execute(
            "select id, kind, text, source_turns, 1 - (embedding <=> %s::extensions.vector) as score "
            "from public.memories where user_id = %s and superseded_by is null "
            "order by embedding <=> %s::extensions.vector limit %s",
            (vector, user_id, vector, k),
        ).fetchall()
    return [SearchHit(str(r[0]), r[1], r[2], tuple(r[3]), float(r[4])) for r in rows]
```

Move the new imports up next to `import psycopg` at the top of the file.

- [ ] **Step 4: Add `ingest_persona` to `evals/ingest.py`**

Append:

```python
from collections.abc import Callable
from uuid import UUID

import psycopg

from app.memory.store import delete_user_data, insert_memories, insert_session
from app.memory.types import ExtractedMemory


def ingest_persona(
    conn: psycopg.Connection,
    user_id: UUID,
    transcripts: list[Transcript],
    extract: Callable[[Session], list[ExtractedMemory]],
    embed: Callable[[list[str]], list[list[float]]],
) -> int:
    """Replace this user's data with memories extracted from the transcripts. Returns the memory count."""
    delete_user_data(conn, user_id)
    count = 0
    for transcript in sorted(transcripts, key=lambda t: t.id):
        session = to_app_session(transcript)
        session_db_id = insert_session(conn, user_id, session)
        memories = extract(session)
        insert_memories(conn, user_id, session_db_id, memories, embed([m.text for m in memories]))
        count += len(memories)
    return count
```

Move the new imports up to the top of the file.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `WREN_DB_TESTS=1 pytest evals/tests/test_db.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add voice/app/memory/store.py evals/ingest.py evals/tests/test_db.py
git commit -m "Store memories and add per-user vector search"
```

---

### Task 7: Scoring, results file and cache

**Files:**
- Create: `evals/score.py`, `evals/results.py`, `evals/cache.py`, `evals/tests/test_score.py`, `evals/tests/test_results.py`, `evals/tests/test_cache.py`

**Interfaces:**
- Produces:
  - `evals.score`: `is_hit(ranked_turns, gold, k=5) -> bool`, `is_stale(ranked_turns, gold, stale, k=5) -> bool`, `percentile(values, q) -> float`, `count_tokens(text) -> int`, `ProbeOutcome(probe_id, hit, stale: bool | None, tokens, latency_ms)`, `summarize(outcomes) -> dict`. The dict keys are the five metric columns.
  - `evals.results`: `COLUMNS`, `RESULTS_PATH`, `git_commit(repo=REPO_ROOT) -> str`, `utc_now() -> str`, `append_rows(rows, path=RESULTS_PATH)`
  - `evals.cache`: `CACHE_DIR`, `cache_key(*parts) -> str`, `cached_json(namespace, key, compute, cache_dir=CACHE_DIR)`, `cached_batch(namespace, keys, compute, cache_dir=CACHE_DIR)`

- [ ] **Step 1: Write the failing tests**

`evals/tests/test_score.py`:

```python
from evals.score import ProbeOutcome, count_tokens, is_hit, is_stale, percentile, summarize

GOLD = {"s2:t02"}
STALE = {"s1:t02"}


def test_hit_only_counts_the_top_k():
    ranked = [("x",)] * 5 + [("s2:t02",)]
    assert not is_hit(ranked, GOLD, k=5)
    assert is_hit(ranked[1:], GOLD, k=5)


def test_stale_when_old_version_outranks_current():
    assert is_stale([("s1:t02",), ("s2:t02",)], GOLD, STALE)


def test_not_stale_when_current_version_comes_first():
    assert not is_stale([("s2:t02",), ("s1:t02",)], GOLD, STALE)


def test_stale_when_old_version_appears_without_current():
    assert is_stale([("x",), ("s1:t02",)], GOLD, STALE)


def test_not_stale_when_neither_version_is_retrieved():
    assert not is_stale([("x",), ("y",)], GOLD, STALE)


def test_percentile_is_nearest_rank():
    values = [float(v) for v in range(1, 21)]
    assert percentile(values, 50) == 10.0
    assert percentile(values, 95) == 19.0


def test_summarize_leaves_stale_rate_blank_without_changed_probes():
    outcomes = [ProbeOutcome("p1", True, None, 40, 120.0), ProbeOutcome("p2", False, None, 60, 200.0)]
    result = summarize(outcomes)
    assert result["recall_at_5"] == 0.5
    assert result["tokens_per_turn"] == 50.0
    assert result["stale_fact_rate"] == ""
    assert result["latency_p95_ms"] == 200.0


def test_count_tokens_counts_something():
    assert 0 < count_tokens("The user moved the Brightline screen to Friday.") < 20
```

`evals/tests/test_results.py`:

```python
import csv
import subprocess

import pytest

from evals.results import COLUMNS, append_rows, git_commit

ROW = {c: "" for c in COLUMNS} | {"experiment": "E1", "variant": "atomic", "change": "a, b"}


def test_append_writes_header_once(tmp_path):
    path = tmp_path / "results.csv"
    append_rows([ROW], path)
    append_rows([ROW], path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["change"] == "a, b"
    assert "\r" not in path.read_text(encoding="utf-8")


def test_append_rejects_unknown_columns(tmp_path):
    with pytest.raises(ValueError):
        append_rows([ROW | {"surprise": "1"}], tmp_path / "results.csv")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


def test_git_commit_marks_dirty_except_for_results_csv(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "results.csv").write_text("header\n")
    (tmp_path / "code.py").write_text("x = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    assert not git_commit(tmp_path).endswith("-dirty")
    (tmp_path / "evals" / "results.csv").write_text("header\nrow\n")
    assert not git_commit(tmp_path).endswith("-dirty")
    (tmp_path / "code.py").write_text("x = 2\n")
    assert git_commit(tmp_path).endswith("-dirty")
```

`evals/tests/test_cache.py`:

```python
from evals.cache import cache_key, cached_batch, cached_json


def test_cached_json_computes_once(tmp_path):
    calls = []

    def compute():
        calls.append(1)
        return {"n": 1}

    key = cache_key("model", "prompt", "input")
    assert cached_json("x", key, compute, tmp_path) == {"n": 1}
    assert cached_json("x", key, compute, tmp_path) == {"n": 1}
    assert len(calls) == 1


def test_cache_key_changes_with_any_part():
    assert cache_key("a", "b") != cache_key("a", "c")
    assert cache_key("ab", "c") != cache_key("a", "bc")


def test_cached_batch_only_computes_missing(tmp_path):
    asked = []

    def compute(indexes):
        asked.append(indexes)
        return [[float(i)] for i in indexes]

    keys = [cache_key(str(i)) for i in range(3)]
    assert cached_batch("v", keys[:2], compute, tmp_path) == [[0.0], [1.0]]
    assert cached_batch("v", keys, compute, tmp_path) == [[0.0], [1.0], [2.0]]
    assert asked == [[0, 1], [2]]
```

Note: in the second call, `compute` receives index `2`, its position in the new key list, and returns `[[2.0]]`.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest evals/tests/test_score.py evals/tests/test_results.py evals/tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError` for `evals.score`, `evals.results` and `evals.cache`.

- [ ] **Step 3: Implement `evals/cache.py`**

```python
"""Disk cache for LLM outputs and embeddings, so reruns measure code changes, not sampling noise."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def cache_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def cached_json(namespace: str, key: str, compute: Callable[[], Any], cache_dir: Path = CACHE_DIR) -> Any:
    path = cache_dir / namespace / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = compute()
    _write(path, value)
    return value


def cached_batch(
    namespace: str, keys: list[str], compute: Callable[[list[int]], list[Any]], cache_dir: Path = CACHE_DIR
) -> list[Any]:
    """Like cached_json for a list. compute() gets the positions that aren't cached yet, in one call."""
    paths = [cache_dir / namespace / f"{key}.json" for key in keys]
    values = [json.loads(p.read_text(encoding="utf-8")) if p.exists() else None for p in paths]
    missing = [i for i, p in enumerate(paths) if not p.exists()]
    if missing:
        for i, value in zip(missing, compute(missing)):
            _write(paths[i], value)
            values[i] = value
    return values
```

- [ ] **Step 4: Implement `evals/score.py`**

```python
"""Metrics for memory evals. Definitions live in the memory-evals skill."""

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from statistics import mean

from evals.cache import CACHE_DIR

# Keep tiktoken's downloaded encoding next to the harness cache.
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(CACHE_DIR / "tiktoken"))

import tiktoken  # noqa: E402


def is_hit(ranked_turns: list[tuple[str, ...]], gold: set[str], k: int = 5) -> bool:
    return any(gold & set(turns) for turns in ranked_turns[:k])


def is_stale(ranked_turns: list[tuple[str, ...]], gold: set[str], stale: set[str], k: int = 5) -> bool:
    """True if a superseded version ranks above the current one, or shows up without it."""
    for turns in ranked_turns[:k]:
        cited = set(turns)
        if gold & cited:
            return False
        if stale & cited:
            return True
    return False


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile for q in (0, 100]."""
    if not values:
        raise ValueError("percentile of no values")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


@lru_cache(maxsize=1)
def _encoding():
    # cl100k_base is a fixed yardstick for comparing rows, not the conversation model's tokenizer.
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


@dataclass(frozen=True)
class ProbeOutcome:
    probe_id: str
    hit: bool
    stale: bool | None  # None when the probe has no superseded facts
    tokens: int
    latency_ms: float


def summarize(outcomes: list[ProbeOutcome]) -> dict:
    changed = [o.stale for o in outcomes if o.stale is not None]
    latencies = [o.latency_ms for o in outcomes]
    return {
        "recall_at_5": round(mean(o.hit for o in outcomes), 3),
        "tokens_per_turn": round(mean(o.tokens for o in outcomes), 1),
        "stale_fact_rate": round(mean(changed), 3) if changed else "",
        "latency_p50_ms": round(percentile(latencies, 50), 1),
        "latency_p95_ms": round(percentile(latencies, 95), 1),
    }
```

- [ ] **Step 5: Implement `evals/results.py`**

```python
"""evals/results.csv: one row per variant per run, append-only. See the memory-evals skill."""

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "evals" / "results.csv"
COLUMNS = (
    "date", "commit", "experiment", "variant", "change",
    "recall_at_5", "tokens_per_turn", "stale_fact_rate", "latency_p50_ms", "latency_p95_ms",
)
# Writing results must not make the next run look like it ran on uncommitted code.
IGNORED_FOR_DIRTY = {"evals/results.csv"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def git_commit(repo: Path = REPO_ROOT) -> str:
    sha = _git(repo, "rev-parse", "--short", "HEAD").strip()
    changed = [line[3:].strip() for line in _git(repo, "status", "--porcelain").splitlines()]
    dirty = [path for path in changed if path not in IGNORED_FOR_DIRTY]
    return f"{sha}-dirty" if dirty else sha


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_rows(rows: list[dict], path: Path = RESULTS_PATH) -> None:
    for row in rows:
        if set(row) != set(COLUMNS):
            raise ValueError(f"row keys {sorted(row)} don't match the results.csv columns")
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest evals/tests -v`
Expected: PASS, with the database tests skipped. The first run downloads the cl100k_base encoding into `evals/.cache/tiktoken`.

- [ ] **Step 7: Commit**

```bash
git add evals/score.py evals/results.py evals/cache.py evals/tests/test_score.py evals/tests/test_results.py evals/tests/test_cache.py
git commit -m "Add eval scoring, results writer and disk cache"
```

---

### Task 8: E1 atomic arm, first real run

**Files:**
- Create: `evals/e1.py`, `evals/tests/test_run.py`, `docs/weekly-log.md`
- Modify: `evals/run.py` (rewrite), `CLAUDE.md` (Commands), `docs/sprints/week-1.md` (item 7 status)
- Append (by the harness): `evals/results.csv`

**Interfaces:**
- Consumes everything above.
- Produces:
  - `evals.e1.run(evalset, settings, change) -> list[dict]` (results rows)
  - `evals.run.RUNNERS: dict[str, Callable]`
  - `evals.run.main(argv=None) -> int`. Exit codes: 0 ok, 1 invalid eval set, 2 experiment not implemented.

- [ ] **Step 1: Write the failing CLI tests**

`evals/tests/test_run.py`:

```python
from pathlib import Path

import pytest

from evals import run
from evals.evalset import load_eval_set

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture
def harness(monkeypatch):
    appended = []
    monkeypatch.setattr(run, "load_eval_set", lambda: load_eval_set(FIXTURE))
    monkeypatch.setattr(run, "load_settings", lambda: None)
    monkeypatch.setattr(run, "append_rows", lambda rows: appended.append(rows))
    return appended


def test_change_is_required():
    with pytest.raises(SystemExit):
        run.main(["--experiment", "E1"])


def test_unimplemented_experiment_logs_nothing(harness):
    assert run.main(["--experiment", "E4", "--change", "x"]) == 2
    assert harness == []


def test_failed_run_logs_nothing(harness, monkeypatch):
    def boom(evalset, settings, change):
        raise RuntimeError("rate limited halfway through")

    monkeypatch.setitem(run.RUNNERS, "E1", boom)
    with pytest.raises(RuntimeError):
        run.main(["--experiment", "E1", "--change", "x"])
    assert harness == []


def test_successful_run_appends_rows(harness, monkeypatch):
    monkeypatch.setitem(run.RUNNERS, "E1", lambda evalset, settings, change: [{"change": change}])
    assert run.main(["--experiment", "E1", "--change", "baseline"]) == 0
    assert harness == [[{"change": "baseline"}]]
```

Run: `pytest evals/tests/test_run.py -v`
Expected: FAIL. `test_change_is_required` fails because `--change` is still optional, and the others fail on the missing `RUNNERS`.

- [ ] **Step 2: Implement `evals/e1.py`**

```python
"""E1: what unit should be stored? Week 1 runs the atomic-memory arm only."""

import json
import time
from dataclasses import asdict

from app.config import EMBED_DIM, Settings
from app.memory.embed import embed_texts
from app.memory.extract import extract_memories, prompt_text
from app.memory.store import connect, vector_search
from app.memory.types import ExtractedMemory, Session
from evals.accounts import ensure_user
from evals.cache import cache_key, cached_batch, cached_json
from evals.evalset import EvalSet, gold_turns, stale_turns
from evals.ingest import ingest_persona
from evals.results import git_commit, utc_now
from evals.score import ProbeOutcome, count_tokens, is_hit, is_stale, summarize

K = 5


def _fingerprint(session: Session) -> str:
    return json.dumps([session.mode, session.started_at.isoformat(), [[t.id, t.speaker, t.text] for t in session.turns]])


def cached_extract(settings: Settings):
    def extract(session: Session) -> list[ExtractedMemory]:
        key = cache_key(settings.gemini_flash_model, prompt_text(), _fingerprint(session))
        raw = cached_json("extract", key, lambda: [asdict(m) for m in extract_memories(session, settings)])
        return [
            ExtractedMemory(r["kind"], r["text"], tuple(r["entities"]), r["importance"], tuple(r["source_turns"]))
            for r in raw
        ]

    return extract


def cached_embed(settings: Settings):
    def embed(texts: list[str]) -> list[list[float]]:
        keys = [cache_key(settings.gemini_embed_model, str(EMBED_DIM), "document", t) for t in texts]
        return cached_batch("embed", keys, lambda missing: embed_texts([texts[i] for i in missing], "document", settings))

    return embed


def run_atomic_arm(evalset: EvalSet, settings: Settings) -> list[ProbeOutcome]:
    extract, embed = cached_extract(settings), cached_embed(settings)
    outcomes = []
    with connect(settings.database_url) as conn:
        users = {}
        for persona_id in sorted(evalset.personas):
            users[persona_id] = ensure_user(settings, f"eval-{persona_id}@example.com")
            transcripts = [t for t in evalset.transcripts.values() if t.persona == persona_id]
            print(f"{persona_id}: {ingest_persona(conn, users[persona_id], transcripts, extract, embed)} memories")
        for probe in evalset.probes:
            # Query embeddings stay uncached: they sit on the live path, so their latency counts.
            start = time.perf_counter()
            query = embed_texts([probe.question], "query", settings)[0]
            hits = vector_search(conn, users[probe.persona], query, k=K)
            latency_ms = (time.perf_counter() - start) * 1000
            ranked = [h.source_turns for h in hits]
            gold, stale = gold_turns(evalset, probe), stale_turns(evalset, probe)
            outcomes.append(
                ProbeOutcome(
                    probe.id,
                    is_hit(ranked, gold, K),
                    is_stale(ranked, gold, stale, K) if stale else None,
                    sum(count_tokens(h.text) for h in hits),
                    latency_ms,
                )
            )
    return outcomes


def run(evalset: EvalSet, settings: Settings, change: str) -> list[dict]:
    commit, started = git_commit(), utc_now()
    metrics = summarize(run_atomic_arm(evalset, settings))
    return [{"date": started, "commit": commit, "experiment": "E1", "variant": "atomic", "change": change, **metrics}]
```

- [ ] **Step 3: Rewrite `evals/run.py`**

```python
"""Eval harness entry point: python -m evals.run --experiment E1 --change "what changed and why"

Rows are appended to evals/results.csv only after a run finishes, so a run that
fails partway (a free-tier rate limit, say) logs nothing. See the memory-evals skill.
"""

import argparse
import sys

from app.config import load_settings
from evals import e1
from evals.evalset import load_eval_set, validate
from evals.results import append_rows

EXPERIMENTS = {
    "E1": "What unit should be stored?",
    "E2": "What should be searched with?",
    "E3": "How should results be ranked?",
    "E4": "Does compaction pay off?",
    "E5": "Which embedding model?",
}
RUNNERS = {"E1": e1.run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Wren memory experiment.")
    parser.add_argument("--experiment", required=True, choices=sorted(EXPERIMENTS))
    parser.add_argument("--change", required=True, help="One line: what changed and why. Logged in results.csv.")
    args = parser.parse_args(argv)

    runner = RUNNERS.get(args.experiment)
    if runner is None:
        print(f"{args.experiment} ({EXPERIMENTS[args.experiment]}): not implemented yet. Nothing was logged.")
        return 2

    evalset = load_eval_set()
    errors = validate(evalset)
    if errors:
        print("The eval set is invalid, so nothing was run:\n" + "\n".join(errors))
        return 1

    rows = runner(evalset, load_settings(), args.change)
    append_rows(rows)
    for row in rows:
        print(", ".join(f"{key}={value}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests**

Run: `pytest voice/tests evals/tests -v`
Expected: PASS, with the database tests skipped. Then run `WREN_DB_TESTS=1 pytest evals/tests/test_db.py -v`.
Expected: PASS (6 tests).

- [ ] **Step 5: Update CLAUDE.md's Commands and commit the code**

Replace the "Evals" block under `## Commands` in `CLAUDE.md` with:

```markdown
Evals (run from the repo root, with `voice/.venv` active):

- One-time: `pip install -r evals/requirements.txt`
- Check the eval set: `python -m evals.evalset` (add `--show-probes maya` to hand-check probes)
- Generate missing transcripts: `python -m evals.generate`
- Run an experiment: `python -m evals.run --experiment E1 --change "what changed and why"` (only E1 so far)
- Tests: `pytest voice/tests evals/tests`. Database tests also need `WREN_DB_TESTS=1` in the shell.

Database (Supabase CLI via npx, from the repo root):

- New migration: `npx supabase@2.121.0 migration new <name>`
- Apply: `npx supabase@2.121.0 db push --db-url "$DATABASE_URL"`
```

```bash
git add evals/e1.py evals/run.py evals/tests/test_run.py CLAUDE.md
git commit -m "Wire E1 atomic arm into the eval harness"
```

Committing first means the results row records a clean commit.

- [ ] **Step 6: Run E1 for real**

Run: `python -m evals.run --experiment E1 --change "E1 atomic baseline: extraction v1, vector top-5"`
Expected:
- Three `<persona>: N memories` lines, then one row printed with `variant=atomic`.
- `commit` has no `-dirty` suffix.
- `recall_at_5` and `stale_fact_rate` are between 0 and 1.
- `latency_p95_ms` is a few hundred ms or less.

This is the first row, so there's no baseline to compare against yet. Report it with the memory-evals skill's table, using "none (first run)" in the baseline column. If `latency_p95_ms` is over 300, note it as a finding for week 2 and don't tune now.

Rerun the same command once more.
Expected: an identical `recall_at_5` (extraction and document embeddings come from the cache) and a second appended row.

- [ ] **Step 7: Log the week and commit the results**

Create `docs/weekly-log.md`:

```markdown
# Weekly log

## Week 1 (2026-10-16 to 2026-10-22): memory foundations

- Hours: <actual hours>
- E1 atomic Recall@5: <value> on 60 probes (<commit>). Stale-fact rate <value>, p95 latency <value> ms.
- Slipped: <what didn't land, and why>
- Next: E1 chunk baselines, then hybrid_search() (week 2).
```

Fill in the real values from the run. Then, in `docs/sprints/week-1.md`, change item 7's priority cell from `Stretch` to `Done` and its "Done when" to `Done 2026-10-16.`

```bash
git add evals/results.csv docs/weekly-log.md docs/sprints/week-1.md
git commit -m "Log first E1 result: atomic memories baseline"
```

---

## Self-review notes

- **Spec coverage:** this week covers feature 4 in part and feature 8 in part.
  - Feature 4: extraction with every required field plus `source_turns`, superseded rows excluded from search, and retrieval under 300 ms measured.
  - Feature 8: the fixed eval set with planted traps, a one-command run through the app's own pipeline, deterministic Recall@5, and reruns that match.
  - Deferred on purpose, per the sprint's "Not this week": the LLM judge for correct use, `hybrid_search()`, superseding during extraction, and the chunk baselines.
- **Consistent names:** `Transcript` and `EvalTurn` (in evals) are kept separate from `Session` and `Turn` (in the app). `to_app_session` is the only bridge between them. `ingest_persona(conn, user_id, transcripts, extract, embed)` has the same signature in Task 6 and Task 8.
