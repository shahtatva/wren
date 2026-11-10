# Proactive opener design

Draft, 2026-11-06 · For [spec.md](../../spec.md) feature 6 ("Proactive opener") · Rules: [CLAUDE.md](../../../CLAUDE.md) and the wren-voice skill

## Build notes (2026-11-06)

Built from [plans/2026-11-06-proactive-opener.md](../plans/2026-11-06-proactive-opener.md). Heard with `python -m app.tools.samples --group opener`, on `openai/gpt-oss-120b` for every reply, with no fallbacks.

1. **The starting copy, 18 openers (three per lead).**
   - Flagged 1 of 18: a coming-up opener said "11 am" in digits, copied from the time in its prompt.
   - Two questions 0, gave up 0. One opener was reshaped: a pick-up asked before its last sentence, and the second ask fit.
   - How-did-it-go, same-day, pick-up and plain did their jobs in all three runs.
   - First said "I'm an AI." with no name, all three times. Coming-up crammed the facts into fragments, and two of three ended a "let's" statement with a question mark.
2. **Two lines tuned.**
   - First now asks Wren to introduce itself by name. Three of three said "I'm Wren, an AI…" and then asked one question.
   - Coming-up now names the day but not the time, and makes fitting feedback the thing to practice. No more digits, but two rounds still joined the clauses with a semicolon, and one round wrote a single run-on sentence.
3. **After the branch review.** The tuning had dropped the instruction to put feedback kindly, which this design requires, and semicolons break wren-voice's rule.
   - Coming-up now asks for two short sentences and kind feedback, and the samples tool flags any semicolon. Three of three came out as two short sentences with no semicolon, flagged 0.
   - At a first session, the code now also checks that the opener says Wren is an AI, so that boundary no longer rests on the prompt alone. Three of three passed on the first ask.
4. **One opener per lead, as heard:**
   - first: "I'm Wren, an AI. What role are you focusing on right now?"
   - how-did-it-go: "How did the Velloway Analytics recruiter screen go?"
   - same-day: "Let's keep sharpening your consulting club story, focusing on the result. Ready to rehearse that part now?"
   - coming-up: "Sprout Social BDR screen is tomorrow, Friday. Want to practice a concise 'tell me about yourself' answer?"
   - pick-up: "We can pick up the consulting club story, Maya. Want to focus on ending with the result?"
   - plain: "How's the job search going, Maya?"
5. **The hold costs little.** Across 33 openers the longest hold was 106 ms, and the median was 0 to 25 ms per round. The reply usually arrives in one burst after the model's reasoning.
6. **Left for later.** Spec feature 6's boxes stay open until extraction runs after live sessions and gives a real account these facts.
7. **The database tests ran last.** The dev Supabase project was paused during the build, so the new reads first ran against Postgres on 2026-11-10, after the branch was rebased onto the room-noise work. All 12 database tests passed.

## Why

Wren speaks first when a session starts. Today that line is a general greeting. `_signals()` in `voice/app/turn.py` asks for "a short greeting, then one question about their search", with the next seven days of pipeline in view and no memories. The first line is the cheapest place to show that Wren remembers. It should name what matters this week and offer one concrete next step.

## What Wren says

The code picks one lead, top row first, and the chat model words it.

| Lead | When | What Wren does | Example |
| --- | --- | --- | --- |
| first | No earlier session | Says it's an AI, then asks one question about the search | "Hi, I'm Wren, an AI here to help with your job search. How's it going so far?" |
| how-did-it-go | A pipeline event happened since the last session | Asks how it went | "How did the Sprout screen go yesterday?" |
| same-day | The last session started earlier today, and it has a summary | Picks up where they left off | "Back again. Want to keep going on your consulting story?" |
| coming-up | An event in the next seven days | Names the soonest one, adds the recent feedback that fits it, and offers one thing to practice | "Your Sprout screen is Thursday, and you ran long on 'tell me about yourself.' Want to tighten it?" |
| pick-up | The last session has a summary | Says what they worked on and offers to pick it back up | "Last time we worked on your consulting story. Want to pick it back up?" |
| plain | Nothing else applies | A plain check-in that names no event | "Hey, good to have you back. How's the search going?" |

Definitions:

- **Happened since the last session:** a current pipeline row whose time is after the last session started and no later than now, at most seven days back. A row with no time counts as the end of its day. With several, the latest one.
- **Coming up:** a current row from now through seven days out, the soonest first. A row today counts if its time is still ahead, or if it has no time.
- **Same day:** the last session started on today's date, on the turn's local clock (`convo.tz`, as `build_messages()` uses). A same-day session with no summary falls through to plain, so a second visit never repeats the coming-up line.
- **Recent feedback:** up to three current `feedback` memories, newest first by the start of the session they came from, or by `created_at` when that session is gone. The model works in the one that fits the event, or none. Only coming-up uses feedback.

The ladder is the same in check-in and mock mode. With memory off, `NoMemory` reports no earlier session, so Wren gives the first lead and says it's an AI, as it does today.

## Shape

The limit is the user's: one or two sentences, under twenty words in total.

- At most one question, as the last sentence.
- Start with the point. No hello sentence before it. A name is fine.
- Never invent an event, a date or a piece of feedback. The plain lead names no interview at all.
- Don't mention how long they've been away. wren-voice: never guilt-trip about absence.
- Feedback is said kindly and specifically: "you ran long on", not "you ramble". Vibe memories are never used.
- The persona card's spoken rules hold: numbers as words, and no lists, symbols or parentheses.

**Checked in code.** The opener is held until it's whole, then checked: two segments at most (each segment is a sentence), under twenty words, and a question only as the last sentence (`_asks()`). At a first session it must also say Wren is an AI. A miss is asked for once more, before any audio. If that misses too, Wren says the lead's fixed line. An opener makes at most two model calls, as every reply does today. The turn records the second ask in `reply_repair`, so the samples and `turns.jsonl` show how often it happens.

Holding should cost only the time to write the opener's last sentence, likely tens of milliseconds on Groq. Openings aren't in the TTFA targets, since they have no end of speech. So the turn logs how long the opener was held (`held_ms`, from its first whole sentence to when it's spoken), and the samples run reports it. A before-and-after comparison of whole openings would be too noisy to show the cost.

**Fixed lines.** These are the fallback after two misses, and they pass the same check. They leave out the stage, which is free text ("applied", "offer") and doesn't always read as an event.

- first: `FIRST_OPENING_LINE`, as today.
- how-did-it-go: "How did things go with {company}?"
- coming-up: "Your next step with {company} is {day}. Want to get ready for it?" The day is today, tomorrow, a weekday, or "next" and a weekday at seven days out.
- same-day, pick-up and plain: `OPENING_LINE`, as today.

## Where the facts come from

Everything loads in `open_session()`'s one transaction, before `ready`. There's no search and no model call, so the opener starts no later than it does today. There's no schema change: every column it reads exists.

`Background` gains three fields:

- **recent:** current pipeline rows dated from seven days back through today. `turn.py` decides by the clock which of them happened since the last session.
- **feedback:** up to three current feedback memories, as defined above, each with its session's date, so Wren never guesses "last week".
- **last_summary:** `sessions.summary` of the session just before this one, or None. An older session's summary never stands in for it.

`Background.pipeline` is unchanged, and later turns don't see the new fields. Per-turn context and retrieval stay as they are.

**Nothing writes these yet.** In the live app, extraction runs only inside the eval harness, so a real user has no memories, pipeline rows or session summaries. Until extraction runs after each session, which is the next piece of work, live sessions only ever get the first and plain leads. That step should also fill `sessions.summary` from extraction's one-sentence session episode, leaving out crisis content as extraction already does. Saved transcripts can be extracted then, so nothing from earlier sessions is lost.

## The prompt

The opening keeps today's path: `run_turn(convo, None)`, no retrieval, and `conversation.md` built on the one persona card. Only the opening lines of `_signals()` change. They carry the lead's instruction, its facts and the shape rules. For coming-up, the feedback notes fill the memories block ("What you remember that may matter now"), which is empty at the opening today. Tune the wording with the samples tool, not by guessing.

## Voice and text

Voice and typed sessions both open on the socket's `ready`, through `run_turn()`, so text mode gets the same opener. POST /turn always carries the user's words, so it has no opener, as today.

## Tests and checks

- **Unit tests** (`voice/tests/test_turn.py`): the lead for each row, including an event earlier today that already happened, a row with no time, a same-day session after an event, and nothing known. Each lead's prompt lines and facts. Feedback only for coming-up. The second ask and each fixed line. No retrieval at the opening, as today.
- **Database tests** (`WREN_DB_TESTS=1`): the new reads skip superseded rows, other kinds and other users' rows under RLS. Feedback comes newest first and stops at three. The summary is only ever the previous session's.
- **Samples:** an `opener` group in `app.tools.samples`, with one Maya scene per lead, flagged when an opener runs past two sentences or twenty words. Today's `opening` scenario moves into it. Run `python -m app.tools.samples --group opener --runs 3` and read every reply, as wren-voice asks after a prompt change.

## Evals

No eval run. E1–E3 measure extraction, the search questions and the ranking for the user's words, and none of them change. The opener's reads don't go through `hybrid_search()`, and the memory-evals skill exempts changes that don't change what reaches retrieval.

## Non-goals

- Wiring extraction after live sessions. It's the next piece of work.
- Push notifications. The concept rules them out.
- Compaction's "how did it go?" prompts. The how-did-it-go lead reads the pipeline directly.
- Choosing feedback by search. Revisit if the latest three often don't fit the event.
- Changing what later turns see.
- A gentler opener after a crisis mention. It waits for spec feature 7 (see the known gap below).

## Who decided what

The user decided:

- The four kinds of lead and their order: how it went, coming up with feedback and an offer, where you left off, and a plain check-in.
- A second visit on the same day skips the feedback and picks up where they left off.
- One or two sentences, under twenty words in total.
- Build the opener now, test it on Maya's sample scenes, and wire live extraction next.
- After a crisis mention, the opener waits for spec feature 7. It's a known gap until then.

These were the builder's calls:

- The code picks the lead, and the model only words it.
- The first session says it's an AI and names no event, because both won't fit in twenty words.
- Feedback is the latest three, with no search.
- "Where you left off" comes from `sessions.summary`, because CLAUDE.md keeps raw transcripts for audit and evals.
- The hold-and-check, with one second ask and the fixed lines.
- No eval run.

## Left to the builder

- Query names and shapes, and where the lead choice lives. A pure function of the background and the clock is easiest to test.
- The prompt's exact wording.
- Whether the shape check sits inside `_reply()`'s second ask or wraps it, as long as the two-call limit holds.
- Knobs for the limits in `Tuning`.

## Known gap: after a crisis mention

Today the code checks for crisis words only during a mock. In a check-in, the persona card handles them. So after a session where the user mentioned a crisis, the next opener can still lead with an interview and an offer to practice.

The user chose to leave this until crisis handling moves into code (spec feature 7), which has to land before testers. That work should mark a session where a crisis came up, so the next opener can skip the pipeline and open gently, with no offer to practice.
