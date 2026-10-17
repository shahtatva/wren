---
name: memory-evals
description: Run and log Wren's memory evals, using the E1–E5 harness in evals/ with results in evals/results.csv. Use this whenever a change touches how memories are extracted, chunked, embedded, retrieved, ranked or compacted. That includes extraction prompts or models, memory fields that search reads, hybrid_search() and its SQL or indexes, query rewriting, rank fusion, recency or importance boosts, top-k, and embedding model or dimensions. Also use it when asked to run an experiment, compare memory variants, or report recall@5, tokens per turn, stale-fact rate or retrieval latency, even if the word "eval" never comes up.
---

# Memory evals

Wren's memory system is the point of the project, and `evals/results.csv` is the evidence that it works. A change to extraction, storage, embeddings, search or ranking can make recall worse while the app still seems fine in conversation. So every such change gets measured against a baseline and logged. This skill covers when to run the harness, what the metrics mean, and how to log and report a run.

## When to run it

Run evals after any change to:

- **Extraction:** the prompt or model that turns transcripts into atomic memories, the fields it fills, or how it scores importance.
- **Chunking or storage unit:** what one stored, searchable item is (E1).
- **Query side:** turning the user's message into search queries (E2).
- **Retrieval and ranking:** `hybrid_search()`, vector or full-text search settings, rank fusion, recency and importance boosts, or how many results are kept (E3).
- **Compaction:** merging duplicates, superseding old facts, or dropping entries (E4).
- **Embeddings:** the model, the dimensions, or what text gets embedded (E5).

UI, voice transport, speech-to-text, text-to-speech and persona copy don't need evals, unless they change what reaches retrieval. If you're unsure, run it. A run is cheap compared with a regression nobody notices.

## Experiments

| ID | Question | Variants (one results row each) |
| --- | --- | --- |
| E1 | What unit should be stored? | 512-token transcript chunks · one chunk per turn · extracted atomic memories |
| E2 | What should be searched with? | raw message · 2–3 rewritten questions · hypothetical answer (HyDE) |
| E3 | How should results be ranked? | vector only · vector + full-text with rank fusion · plus recency and importance boost |
| E4 | Does compaction pay off? | no compaction · nightly merge and supersede |
| E5 | Which embedding model? | free local model · Gemini embeddings |

Run the experiment whose question your change touches. If the change spans layers, or you can't tell which one it affects, run E1–E3.

## Running

From the repo root:

```
python -m evals.run --experiment E3 --change "RRF k 60 -> 20: weight top ranks more"
```

`--change` is one line saying what changed and why. It becomes the `change` column. The harness runs every variant of the experiment against the frozen eval set in `evals/data/`, through the same text-mode pipeline the app uses, and appends one row per variant to `evals/results.csv`.

If the harness says an experiment isn't implemented, stop and tell the user. Don't compute or estimate metrics by hand to fill the gap. A number in results.csv has to come from a harness run, or the write-up built on it can't be trusted.

## Workflow

1. **Get a baseline first.** Find the latest row for this experiment and variant. If there isn't one, or other memory code has changed since it was logged, run the experiment on the unchanged code before you touch anything. A stale baseline mixes your change with whatever else moved.
2. **Make the change, then commit it**, unless the user doesn't want a commit yet. The `commit` column is what makes a row reproducible. A run on uncommitted code is logged as `<sha>-dirty`. That's fine for exploring, but don't cite dirty rows in the write-up.
3. **Run** the experiment with a specific `--change` line.
4. **Compare and report**, using the format below.
5. **Keep the rows.** results.csv belongs in the same commit as the change it measured, or in the commit right after it.

## results.csv

The header, with one row per variant per run:

```
date,commit,experiment,variant,change,recall_at_5,tokens_per_turn,stale_fact_rate,latency_p50_ms,latency_p95_ms
```

| Column | Meaning |
| --- | --- |
| `date` | Run time in UTC, as ISO 8601 (`2026-10-20T19:04:00Z`) |
| `commit` | Short git SHA the code ran at, with `-dirty` if there were uncommitted changes |
| `experiment` | `E1` to `E5` |
| `variant` | Which arm ran, such as `chunk512`, `turn`, `atomic`, `vector`, `rrf`, `rrf_boost` |
| `change` | The `--change` text, quoted if it contains commas |
| metrics | Defined below. Leave a metric blank if this run didn't measure it. Never fill a gap with 0. |

The file is append-only. Never edit or delete a past row. If a row turns out to be wrong, for example because of a scorer bug, append a fresh run and say so in `change` ("rerun after scorer fix"). The bad rows are part of the history the write-up tells.

## Metrics

**Recall@5** (`recall_at_5`, 0–1, higher is better). Retrieve the top 5 items for each probe question in the eval set. A probe is a hit if any of those items carries the probe's gold answer, as labeled in the eval set. Recall@5 is hits divided by probes. This is the headline number, because a reply can't use a memory that wasn't retrieved. The target is 0.85 or higher.

**Tokens per turn** (`tokens_per_turn`, lower is better at equal recall). The mean number of tokens of retrieved text injected into the context per turn. It's the cost side of recall: whole transcript chunks can score well while flooding the context window. Only compare rows counted with the same tokenizer.

**Stale-fact rate** (`stale_fact_rate`, 0–1, lower is better). This only covers probes whose answer changed over time, such as an interview that moved or a story retold with new details. A probe counts as stale if the superseded version ranks above the current one, or appears in the top 5 without it. The rate is stale probes divided by changed-fact probes. It shows whether corrections actually win. The target is 5% or lower.

**Latency** (`latency_p50_ms`, `latency_p95_ms`). Wall-clock retrieval time per probe, from receiving the message to returning the ranked list, including any query-rewrite model call. The budget is p95 under 300 ms, because retrieval sits on the live path and a voice reply should start within about a second. Latency depends on the machine and network, so only compare rows from the same setup and say when the setup changed. Time to first audio is a separate metric that comes from the voice server's logs, not from this harness.

## Reporting a run

Show the user a table like this:

| Metric | Baseline (`a1b2c3d`) | This change (`e4f5a6b`) | Δ |
| --- | --- | --- | --- |
| Recall@5 | 0.817 | 0.850 | +0.033 |
| Tokens per turn | 412 | 398 | −14 |
| Stale-fact rate | 0.083 | 0.042 | −0.041 |
| Latency p95 (ms) | 241 | 268 | +27 |

Then give a one-line verdict (keep, revert or inconclusive) and the reason. To judge it:

- A drop in Recall@5 is a regression even if the other metrics improve, unless the user chooses that trade on purpose.
- A p95 latency over 300 ms fails the budget, whatever recall gained.
- With about 60 probes, one probe is worth about 0.017. A difference of one or two probes on a variant that involves an LLM call is within noise. Call it inconclusive and suggest a rerun.

## Keeping runs comparable

- Don't edit the eval set as part of a change you're measuring. If the eval set has to change, say so in `change` ("eval set: +10 probes"), then re-baseline every experiment before comparing anything.
- Steps that call an LLM (extraction, query rewriting, HyDE) vary between runs. Use temperature 0 and cache outputs by input and prompt version, so reruns measure the change and not sampling noise. If you invalidate the cache, say so in `change`.
- If a free-tier rate limit kills a run partway through, the partial run isn't a result. Don't log it.
