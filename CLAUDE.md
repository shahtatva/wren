# Wren: voice job-hunt companion

Wren is a voice-first companion for job seekers. It rehearses interviews out loud, remembers every story, company and piece of feedback, and notices how the user is holding up. It is a portfolio project, so **the memory system and its measured results matter more than features.**

Full concept: `docs/concept.md` · Spec: `docs/spec.md` · Current sprint: `docs/sprints/`

## Repo layout

- `web/`: Next.js (App Router, TypeScript, Tailwind), installable PWA. Deployed on Vercel.
- `voice/`: FastAPI. Holds the WebSocket voice loop and the `/turn` logic. **Not deployed on Vercel**, because Vercel functions can't hold long-lived audio connections. It runs on a free container host instead.
- `evals/`: Python eval harness. Results go in `evals/results.csv`.
- `supabase/`: SQL migrations. Postgres with pgvector and full-text search.
- `docs/`: concept, spec, sprints, research, write-up, weekly log.

## Voice pipeline (cascade, not speech-to-speech)

Browser mic → WebSocket → Deepgram Nova-3 streaming speech-to-text → `/turn` → LLM → Deepgram Aura-2 streaming text-to-speech → browser playback.

- Conversation LLM: a fast model on Groq. Background work (extraction, compaction): Gemini Flash.
- Barge-in: when the user starts speaking, stop playback and cancel the reply in flight.
- A cascade is used on purpose, so the context window can be controlled every turn.

## Memory design (do not change without running evals)

- **Extract, don't chunk.** After each session, extract atomic memories (one claim each). Keep raw transcripts only for audit and evals.
- **Memory kinds:**
  - profile (SQL row)
  - pipeline (SQL table)
  - story bank (SQL table + embedding)
  - feedback patterns (embedded memory)
  - episodes (embedded memory)
  - vibe (recent window only; it decays)
- **Structured vs fuzzy:** dates, stages and companies go in tables. Stories, patterns and feelings get embedded.
- **Every memory row carries:** kind, text, entities, importance 1–5, timestamps, source session, embedding, tsvector, and `superseded_by`.
- **Rebuild the context from scratch every turn** from:
  - persona card and tone guidance
  - profile
  - next 7 days of pipeline
  - rolling summary plus the last 6 turns
  - the top ~8 retrieved memories
  - live signals: mode, time of day, days since the last session
- **Retrieval:**
  1. A fast model writes 2–3 search questions from the latest user message.
  2. `hybrid_search()` runs vector and full-text search for each question.
  3. Results are merged with reciprocal rank fusion, then boosted by recency and importance.
  4. Budget: under 300 ms.
- **Compaction (nightly):** merge near-duplicates, let newer facts supersede older ones, turn past interview dates into "how did it go?" prompts, and drop low-value entries.

## Rules

- Never put API keys in client code. The browser talks only to `voice/`.
- Never commit `.env` files. Keep the `.env.example` files current.
- Any change to extraction, chunking, retrieval, ranking or embeddings: run the `memory-evals` skill and log the result.
- Any prompt, persona card or user-facing copy: follow the `wren-voice` skill.
- Text mode must use the same pipeline as voice mode. Tests depend on it.
- Log time-to-first-audio on every voice turn. Don't slow down the live path.
- Row-level security stays on for every Supabase table.
- Free tiers only. Ask before adding any paid service or new dependency with its own account.
- Show a plan before large changes (new architecture, schema changes, the voice loop).

## Wren's boundaries (product, not just copy)

18+ only. Wren discloses that it is an AI. No romance or therapy framing, and no diagnosing. Mood adjusts tone only. A crisis mention routes to real resources. Wren nudges the user toward real people: friends for mock interviews, referrals.

## Skills and when to use them

| Task | Skill |
| --- | --- |
| Spec, sprints, research synthesis, metrics | product-management |
| Schema, migrations, indexes, RLS, SQL functions | Supabase Agent Skills |
| UI and visual design | frontend-design |
| React/Next.js performance review | vercel-react-best-practices |
| Browser tests | webapp-testing |
| Deploying `web/` | Vercel plugin |
| Running and logging memory experiments | memory-evals (project skill) |
| Persona, prompts, copy | wren-voice (project skill) |

## Commands

Web (Next.js 16, run from `web/`):

- Install: `npm install`
- Dev server: `npm run dev` (http://localhost:3000)
- Lint / build: `npm run lint`, `npm run build`
- Next.js 16 differs from older versions. Check `web/node_modules/next/dist/docs/` before using a Next API (see `web/AGENTS.md`).

Voice server (FastAPI, run from `voice/`):

- One-time setup: `python -m venv .venv`, then activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere), then `pip install -r requirements-dev.txt`
- Run: `uvicorn app.main:app --reload` (health check: http://localhost:8000/health)
- Tests: `pytest`

Evals (run from the repo root):

- `python -m evals.run --experiment E1` (E1–E5; placeholder until the harness exists)

Env: copy `web/.env.example` to `web/.env.local` and `voice/.env.example` to `voice/.env`.
