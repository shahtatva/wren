# Whole Replies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wren's replies come out whole. No reply stops mid-sentence or comes back empty, and no sentence is split into two recordings.

**What the 2026-10-23 session showed:**
- **Replies stopped mid-sentence.** Some stopped mid-word ("...interested in A", "...prep for that Airc"), others were just "I", and some were empty. The turn log called every one of them "complete".
- **The cause is the main chat model,** `qwen/qwen3.8-27b` on Groq. It often ends a reply after a word or two, or sends nothing. It does so with finish reason `stop`, no hidden reasoning, and 10–16 tokens, far below the 400 cap.
  - Replaying two failing moments: 0 of 4 replies finished, with reasoning off and on.
  - `openai/gpt-oss-120b` finished 4 of 4, and `openai/gpt-oss-20b` 4 of 4 (one of them stacked three answers).
- **Cut-off lines in the history make it worse,** because the model copies them. A "finish your sentence" instruction didn't help (0 of 2). Recording the cut differently didn't help on qwen either; gpt-oss handled today's format (3 of 3).
- **The voice changed mid-sentence** because the splitter cuts the first sentence at a comma once it has 5 words. Each piece is a separate Aura-2 recording, so the second half starts a new take. 2 of the session's 8 full replies were split this way.

The replay scripts are in `logs/session-5/` (git-ignored): `truncation_probe.py`, `cutoff_variants.py`, `cutoff_variants2.py`, `model_compare.py`. Run them from `voice/` with its venv, for example `python ../logs/session-5/model_compare.py`.

**Architecture:**
- **Whole sentences only:** the splitter ends a piece at a sentence end, or at the 30-word cap for a run-on, never at a comma.
- **Repair a broken reply before it's spoken:**
  - `run_turn` speaks each whole sentence as soon as it's complete, as today. It checks only the tail the model leaves when its stream ends.
  - A tail that doesn't end a sentence is dropped. It's never spoken or saved.
  - If that leaves the reply with nothing (it was empty, or only a fragment), the turn asks the model once more.
  - If the second answer is broken too, Wren says the existing line "I lost my train of thought. Could you say that again?"
- **New main model:** `openai/gpt-oss-120b`, then `openai/gpt-oss-20b` when it's rate-limited, then Gemini as today. This is configuration only.

**Tech Stack:** Python 3.14, pytest. No new dependencies.

**Spec:**
- `docs/superpowers/specs/2026-10-16-voice-loop-design.md`: "Build notes" item 4 (the chat model), "Latency budget" (first segment), "Turn lifecycle"
- `CLAUDE.md`
- the wren-voice skill (one question per turn, short spoken sentences)

## Global Constraints

- **TTFA targets:** p50 ≤ 1.0 s, p95 ≤ 1.5 s.
  - The model switch is expected to cost about 150–250 ms at p50: gpt-oss-120b's first word took 418–777 ms in the replay, against 336–622 ms for qwen when qwen answered at all.
  - Measure it, report it, and let the owner decide. A reply that stops after a word is worse than a slower one.
- **The repair adds no wait to a healthy reply.** The first whole sentence is spoken as soon as it's complete, as today.
- Text mode keeps using the same `run_turn`, so `POST /turn` gets the repair too.
- Every turn still logs its timing record. A new `reply_repair` field says when the repair acted.
- Wren's words follow the wren-voice skill. The fallback line is existing copy.
- No memory-eval run: nothing here touches retrieval.
- Free tiers only.
- Work on a branch `whole-replies` off `master`.

## Review Focus

1. **A reply with no final punctuation,** like "Sounds good": it's taken as a fragment and asked again. The cost is a retry, never a cut sentence (Task 2, `test_a_reply_that_stops_mid_sentence_is_asked_again`).
2. **A reply ending in "…", a closing quote or a bracket:** it still counts as a finished sentence (Task 2, `test_ends_a_sentence_accepts_ellipses_and_closing_quotes`).
3. **The first sentence still goes out before the reply is finished:** there's no extra wait on healthy turns (Task 2, `test_the_first_sentence_is_yielded_before_the_reply_ends`).
4. **A run-on of more than 30 words:** it's still cut at the cap and spoken (Task 1, the existing `test_long_run_on_splits_at_max_words`).
5. **A barge-in while the turn is asking again:** the retry happens before any audio, so the usual merge applies (Task 2 runs inside the same cancellable `run_turn`).

---

### Task 1: Whole sentences only

**Files:**
- Modify: `voice/app/voice/segment.py` (remove `_clause_end` and `first_min_words`; update the module docstring)
- Test: `voice/tests/test_segment.py`

- [ ] **Step 1: Write the failing test,** replacing `test_first_segment_breaks_early_at_a_clause`:

```python
def test_a_first_sentence_with_a_comma_stays_whole():
    seg = Segmenter()
    pieces = seg.feed("As an AI, I generate text based on patterns, and sometimes it stops. Next. ") + seg.flush()
    assert pieces == ["As an AI, I generate text based on patterns, and sometimes it stops.", "Next."]
```

- [ ] **Step 2: Run it and see it fail.** `cd voice && pytest tests/test_segment.py -v`. Expected: it fails, because the first piece ends at "patterns,".
- [ ] **Step 3: Implement.** Drop the clause rule. Sentence ends and the 30-word cap stay.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** "Speak whole sentences: no splits at commas"

### Task 2: Repair a broken reply before it's spoken

**Files:**
- Modify: `voice/app/turn.py` (`run_turn` uses `Segmenter` directly; new `ends_a_sentence`; `RETRY_LINE` with the existing copy)
- Modify: `voice/app/voice/metrics.py` (`TurnTiming.reply_repair: str | None`, included in `record()`)
- Test: `voice/tests/test_turn.py`

**Interfaces:**
- Produces:
  - **`ends_a_sentence(text: str) -> bool`:** true when the text ends in `.`, `!`, `?` or `…`, optionally followed by closing quotes or brackets.
  - **`run_turn`:**
    - It yields pieces from `Segmenter.feed()` as they complete.
    - At the end of the stream, it yields the `flush()` tail only if it ends a sentence; otherwise the tail is dropped.
    - If nothing was yielded at all, it calls `chat` once more with the same messages and a fresh segmenter.
    - If that yields nothing either, it yields `RETRY_LINE = "I lost my train of thought. Could you say that again?"`.
    - It sets `timing.reply_repair` to `"dropped_tail"`, `"retried"` or `"gave_up"`, and logs a warning with the model.

- [ ] **Step 1: Write the failing tests** in `voice/tests/test_turn.py`, with a fake chat that plays one scripted reply per call:

```python
class ScriptedChat:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    async def __call__(self, messages, timing):
        reply = self.replies[self.calls]
        self.calls += 1
        timing.model = "fake-model"
        for delta in reply:
            yield delta


def turn_out(chat, timing):
    return asyncio.run(_collect(run_turn(conversation(), "Elaborate, please.", retrieve=_no_memories, chat=chat, timing=timing)))


def test_a_reply_that_stops_mid_sentence_is_asked_again():
    chat, timing = ScriptedChat(["I have a"], ["I keep notes between sessions."]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["I keep notes between sessions."]
    assert chat.calls == 2 and timing.reply_repair == "retried"


def test_an_empty_reply_is_asked_again():
    chat, timing = ScriptedChat([], ["Take your time."]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Take your time."]


def test_a_trailing_fragment_after_whole_sentences_is_dropped():
    chat, timing = ScriptedChat(["Let's get back to your search. How"]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Let's get back to your search."]
    assert chat.calls == 1 and timing.reply_repair == "dropped_tail"


def test_two_broken_replies_end_with_the_retry_line():
    chat, timing = ScriptedChat(["I"], ["I"]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["I lost my train of thought. Could you say that again?"]
    assert timing.reply_repair == "gave_up"


def test_ends_a_sentence_accepts_ellipses_and_closing_quotes():
    assert all(map(ends_a_sentence, ["Done.", "Really?", "Wait…", 'He said "go."', "(Sure.)"]))
    assert not any(map(ends_a_sentence, ["I have a", "Nice to meet you,", ""]))


def test_the_first_sentence_is_yielded_before_the_reply_ends():
    release = asyncio.Event()

    async def slow_chat(messages, timing):
        yield "First sentence here. "
        await release.wait()  # the rest of the reply hasn't come yet
        yield "Second."

    async def first_piece():
        gen = run_turn(conversation(), "hi", retrieve=_no_memories, chat=slow_chat, timing=TurnTiming(3, "checkin", "voice"))
        piece = await asyncio.wait_for(anext(gen), 1.0)
        await gen.aclose()
        return piece

    assert asyncio.run(first_piece()) == "First sentence here."
```

Add `_no_memories`, an async retrieve that returns `Retrieved([], 0.0)`.

- [ ] **Step 2: Run them and see them fail.** `cd voice && pytest tests/test_turn.py -v`. Expected: the repair tests fail (the fragment is yielded, and there's no retry), and `ends_a_sentence` fails to import. The streaming test may already pass; it pins today's behavior.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** "Repair a reply that stops mid-sentence or comes back empty"

### Task 3: Switch the main chat model

**Files:**
- Modify: `voice/.env.example` (`GROQ_CHAT_MODEL=openai/gpt-oss-120b`, `GROQ_CHAT_FALLBACK_MODEL=openai/gpt-oss-20b`, with comments saying why)
- Modify (the owner's machine, not committed): the same two lines in `voice/.env`
- Modify: `docs/superpowers/specs/2026-10-16-voice-loop-design.md`, "Build notes" item 4

- [ ] **Step 1: Replay check.** Run `logs/session-5/model_compare.py` against the new main model: both failing moments, three times each. Expected: every reply finishes a sentence.
- [ ] **Step 2: Voice check (wren-voice skill).** Run `logs/session-5/persona_samples.py` with the new model. Check the replies against the skill's "Before you finish" list: one question per turn, short spoken sentences, no lists, and correct crisis and under-18 handling.
- [ ] **Step 3: Update both `.env` files.** Note that `gpt-oss-20b` is also the query-rewrite model, so they share one rate limit, but only while the main model is rate-limited.
- [ ] **Step 4: Commit:** "Make gpt-oss-120b Wren's main chat model: qwen ended replies early"

### Task 4: Check it live

- [ ] **Step 1: Bench, before and after,** with a throwaway `WREN_DEV_USER_ID` and the screen kept on. Run `cd voice && python -m app.tools.bench --barge-in` on `master`, then on the branch. Report TTFA p50 and p95 for both. Check Windows events 506 and 507 for standby in that window.
- [ ] **Step 2: Browser sessions.** Run `python web/e2e/session_e2e.py --mode both`. Expected: no new failures.
- [ ] **Step 3: A long session,** about 30 turns, like the 2026-10-23 one, with interruptions. Expected: no reply ends mid-sentence, and the turn log shows how often `reply_repair` fired.
- [ ] **Step 4: Update the design doc's latency budget,** removing "Early clause break", and record the measured cost. Commit: "Document whole replies"
- [ ] **Step 5: Whole-branch review.** Show the findings to the owner before fixing anything or merging.
