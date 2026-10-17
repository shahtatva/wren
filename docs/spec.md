# Wren: MVP spec

Draft, 2026-10-16 · Built from [concept.md](concept.md) · Architecture: [CLAUDE.md](../CLAUDE.md)

## Problem

Job seekers rehearse alone, with tools that forget them. Each mock interview starts from zero. Dates and feedback live somewhere else, and habits like rambling go uncaught. Logistics and feelings come up together. The builder has run a dozen-plus preps this fall.

## Users

- **Primary:** user zero, the builder, targeting BDR and analyst roles.
- **Secondary:** a few consenting 18+ testers, friends who are also job hunting.
- **Audience, not users:** hiring teams (Tolan first), who will judge the memory work.

## Goals

1. **Remembers:** Recall@5 ≥ 0.85, beating the transcript-chunk baseline (E1).
2. **Feels live:** time to first audio p50 ≤ 1.0 s, p95 ≤ 1.5 s.
3. **Gets used:** 15+ real sessions, with at most one "I already told you that" per 10 sessions.
4. **Publishable:** E1–E3 rerun from one command into the write-up.

**Phasing:** memory and evals first, in text mode, then voice, following the concept's gates.

## Non-goals

- **Accounts for strangers, payments, app stores.** One user plus testers is enough.
- **3D character, integrations, push, resume writing.** None of them test memory.
- **Nightly compaction, E4–E5.** They need about 30 sessions of data first.
- **Speech-to-speech.** The cascade keeps control of the context every turn.

## MVP features and acceptance criteria

1. **Voice with barge-in**
   - [ ] Replies stream within the time-to-first-audio target, which is logged every turn.
   - [ ] Barge-in stops playback in 300 ms, cancels the reply, keeps only what played.
   - [ ] Text mode uses the same `/turn` pipeline and takes over if the mic or connection fails.
2. **Check-in and mock-interview modes**
   - [ ] A mock targets a pipeline company, with a persona fitted to its role and stage.
   - [ ] The user can step out to talk to Wren at any point.
   - [ ] Each mock ends in a debrief: what landed, what drifted, how it felt.
3. **Pipeline entry by voice**
   - [ ] "Sprout screen Thursday" stores an exact date and stage. Wren reads it back.
   - [ ] "Moved to Friday" updates it and keeps history. Vague dates get a question.
4. **Memory: extraction, context, retrieval**
   - [ ] After each session, extraction writes atomic memories with all CLAUDE.md fields.
   - [ ] Extraction runs off the live path and never delays a reply.
   - [ ] A contradiction sets `superseded_by` on the old row instead of deleting it.
   - [ ] Context is rebuilt every turn and can be inspected. Retrieval p95 stays under 300 ms.
5. **"What Wren knows" page**
   - [ ] It shows every memory, the profile and pipeline by kind, with source and date.
   - [ ] Edits apply next turn; deleted memories never return; RLS scopes rows to users.
6. **Proactive opener**
   - [ ] The first line names the next event within 7 days and offers one concrete action.
   - [ ] After a past interview, Wren asks how it went.
   - [ ] With nothing upcoming, it opens with a plain check-in and never invents an event.
7. **Boundaries**
   - [ ] The user confirms 18+ first. Wren says it is an AI at the start and whenever asked.
   - [ ] A crisis mention stops coaching and gives real resources (988 in the US).
   - [ ] It passes 100% of scripted crisis probes. Mood shifts tone only, never a diagnosis.
   - [ ] No romance or therapy framing. Debriefs point to real people where that fits.
8. **Eval harness (E1–E3)**
   - [ ] A fixed set: about 30 synthetic sessions (3 job seekers) and 60 probe questions.
   - [ ] Planted traps: a story told twice, a rescheduled interview, a company that re-engages.
   - [ ] One command runs each experiment via text mode into `evals/results.csv`.
   - [ ] Rerunning the same config gives the same Recall@5.

## Success metrics

| Metric | Definition | Target |
| --- | --- | --- |
| **Recall@5** | Probes with a correct memory in the top 5 | ≥ 0.85 (stretch 0.90) |
| **Time to first audio** | End of user turn → first audio byte sent | p50 ≤ 1.0 s, p95 ≤ 1.5 s |
| Retrieval latency | Query rewrite → ranked results | p95 < 300 ms |
| Correct use | LLM judge: reply used memory correctly | ≥ 0.80 |
| Stale-fact rate | Probes answered with a superseded fact | ≤ 5% |
| "Already told you" | Moments per 10 real sessions | ≤ 1 |

Check the first five after every memory change, and the last one weekly. Targets are hypotheses: revise them once, after the first eval run.

## Open questions

- **Which free host runs `voice/` within the latency budget?** (Engineering; blocks voice.)
- **How are testers separated under RLS?** Supabase magic links? (Engineering; blocks testers.)
- **Which embedding model ships before E5?** (Engineering; non-blocking.)
- **Which crisis resources outside the US?** (Product; blocks testers.)
