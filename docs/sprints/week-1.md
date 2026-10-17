# Sprint plan: week 1, memory foundations

**Dates:** Fri 2026-10-16 to Thu 2026-10-22 · **Team:** 1 (the builder) · **Capacity:** about 15 h

**Sprint goal:** by Thursday, a real Recall@5 for E1's atomic-memory arm is logged on a frozen eval set, so the experiments can start in week 2.

**Source:** [spec.md](../spec.md) and [concept.md](../concept.md). The six-week table in concept.md didn't survive the export, so this week follows the rules the concept states instead: memory before voice, experiments from week 2, and 15–20 h a week.

## Capacity

| | Hours |
| --- | --- |
| Available | 15 |
| Committed (P0) | 11.5 (77%) |
| Buffer for interview prep, debugging and setup surprises | 3.5 |

Stretch items only start once all P0 work is done, and they take their time from the buffer.

## Decide on day 1 (both block P0 work)

- **Embedding model and dimension.** This blocks the schema. Recommendation: Gemini embeddings (free tier) at 768 dimensions. E5 can add a second embedding column later.
- **How Recall@5 is scored.** This blocks the eval set and extraction. Recommendation: each probe records the session turns that hold its answer, and every memory and chunk stores its source turn IDs. A retrieved item counts as a hit if it cites a gold turn. That keeps scoring deterministic and identical for the chunk and atomic arms.

## Backlog

| Priority | Item | Est. | Done when | Depends on |
| --- | --- | --- | --- | --- |
| **P0** | **1. Eval set v1.** 3 personas with fact sheets, about 30 scripted sessions generated with Gemini Flash, about 60 probes with gold answers and gold turns. Planted traps: a story told twice, a rescheduled interview, a company that rejects and then re-engages. Frozen in `evals/data/`. | 4 h | Committed. Every probe is hand-checked and points at its gold turns. | Gemini key; scoring decision |
| **P0** | **2. Memory schema.** Migration for profile, pipeline, story bank, sessions with raw transcripts, and memories carrying every field CLAUDE.md lists, plus source turns. Adds pgvector and full-text indexes, with RLS on every table. Show the plan before applying it. | 3 h | Applies cleanly to the Supabase project. One persona can't read another's rows. | Supabase project; embedding decision |
| **P0** | **3. Extraction v1.** Turns a transcript into atomic memories (one claim each, with kind, entities and importance) via Gemini Flash structured output. Each memory is embedded and inserted. | 2.5 h | All 30 sessions extracted. A spot check of 20 memories finds one claim each with the right kind. | 1, 2 |
| **P0** | **4. Harness: E1 atomic arm.** `python -m evals.run --experiment E1` embeds each probe, takes the vector top 5, scores Recall@5, and appends a row to `evals/results.csv`. | 2 h | A real Recall@5 number is in `results.csv`. | 1, 3 |
| Stretch | **5. E1 chunk baselines:** 512-token chunks and one chunk per turn, using the same embeddings and scorer. | 1.5 h | Three E1 rows are comparable. | 4 |
| Stretch | **6. `hybrid_search()`:** vector plus full-text search for each query, merged with reciprocal rank fusion. | 2 h | Callable from the harness under 300 ms. | 2 |
| Stretch | **7. memory-evals project skill:** how to run, log and compare an experiment. | 1 h | The skill exists and CLAUDE.md's rule works. | 4 |
| Stretch | **8. Supersede in extraction:** a newer contradicting fact sets `superseded_by`. | 1.5 h | The rescheduled-interview trap resolves to the new date. | 3 |

Work on P0 in order. Item 1 needs no infrastructure, so start it today while the Supabase and Gemini accounts get set up. Cut stretch from the bottom. Whatever is left of item 5 becomes the first task of week 2.

## Not this week

Voice loop, web UI, `/turn`, pipeline entry by voice, and the proactive opener. Per the concept, memory comes before voice.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Gemini free-tier rate limits | Items 1 and 3 stall | Cache every LLM output to disk. Generate once and commit the eval set. |
| Probes reuse the transcript's wording | Inflated Recall@5 | Paraphrase probes by hand. Include probes that need the newer of two facts. |
| Schema rework after extraction | Lost hours | Show the schema plan first (CLAUDE.md rule). Keep later migrations additive. |
| Interview prep eats the week | P0 slips | Drop all stretch first. Minimum viable eval set: 2 personas, 20 sessions, 40 probes, finished in week 2. |

## Definition of done

- [ ] Committed, with no secrets. Any new keys are added to `.env.example`.
- [ ] RLS is on for every new table.
- [ ] `pytest` passes and migrations apply cleanly from scratch.
- [ ] Every extraction or retrieval change has a row in `evals/results.csv`.
- [ ] A weekly log entry records hours spent, Recall@5, and what slipped and why.

## Key dates

| Date | Event |
| --- | --- |
| Fri 10-16 | Sprint start. Make both day-1 decisions and start item 1. |
| Mon 10-19 | Check-in: items 1 and 2 should be done. If not, cut stretch now. |
| Thu 10-22 | Sprint end. Log the E1 number, write the weekly log entry and retro, and plan week 2. |
