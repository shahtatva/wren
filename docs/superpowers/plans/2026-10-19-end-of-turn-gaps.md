# End-of-Turn Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the voice server from ending a user's turn while they're still talking, and let words spoken right after a turn ends join that turn instead of being dropped.

**Architecture:** The end-of-turn policy (`EndOfTurn`) gets one new input: whether the server's silence detector hears speech right now. While it does, a final that would end the turn doesn't, and Deepgram's UtteranceEnd waits (at most `utterance_end_wait_s`, so noisy rooms still commit). In the session, speech that resumes within `join_window_s` of a spoken turn's commit marks that reply as joinable. If real words follow while Wren is speaking, the reply is cancelled as "continued" and the words merge into the user's turn, as they already do while Wren is still thinking.

**Tech Stack:** Python 3.14, FastAPI WebSocket session, pytest with the fakes in `voice/tests/voice_fakes.py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-16-voice-loop-design.md`, sections "Turn lifecycle" and "End of turn with Nova-3". Findings: session 4 browser tests (sample "I have a recruiter screening with Dolan [0.56 s] scheduled for [dip] Thursday"):
1. Nova-3 missed "Dolan". UtteranceEnd fired 1 s after "with" while the user was still talking, and the turn ended mid-sentence (`EndOfTurn.on_utterance_end` never consults the silence detector).
2. A Finalize result arrived after the user had started "Thursday". With the check-in hold at 0 s it committed anyway, so the merge guard (which only fires on a new speech start while thinking) missed it. "Thursday" arrived after Wren's audio started and, as one word, was dropped as a backchannel.
3. In another run the turn ended at a real 0.56 s pause, and the user resumed just as Wren's first audio went out. The merge guard lost that race by tens of milliseconds.

## Global Constraints

- TTFA targets: p50 ≤ 1.0 s, p95 ≤ 1.5 s. The live path must not get slower for a user who has finished talking.
- Barge-in: playback pauses within 300 ms of the user's first sound (browser-side; unchanged here).
- Echo of Wren's own words and backchannels ("mm-hm", "yeah", "okay", "right") must never become, or join, a user turn.
- Typed turns use the same pipeline but are never "mid-sentence": joining applies to spoken turns only.
- Every turn still logs its timing record (`log_turn` and `turns.jsonl`).
- No new dependencies; free tiers only.

## Review Focus

1. **A noisy room** where the silence detector never goes quiet: a turn must still commit, through UtteranceEnd after at most `utterance_end_wait_s` (Task 1 test `test_utterance_end_commits_after_the_wait_if_speech_never_stops`).
2. **Echo of Wren's reply** heard right after a spoken turn ends: it must not join the turn (Task 2 test `test_echo_right_after_a_turn_does_not_join_it`).
3. **"Okay" or "yeah"** said as Wren starts answering: it's a backchannel, so Wren resumes (Task 2 test `test_a_backchannel_right_after_a_turn_does_not_join_it`).
4. **A typed message** followed by speech: no join, the normal interruption rules apply (Task 2 test `test_a_typed_turn_is_never_joined`).
5. **The page** must show one merged user line, not the fragment plus the merge. The server sends `reply_end` with outcome `continued` even if a `complete` one already went out (Task 2 join test asserts it). Live check in Task 3.

---

### Task 1: The end-of-turn policy waits while the user is speaking

**Files:**
- Modify: `voice/app/voice/endpoint.py` (`EndOfTurn.__init__`, `on_transcript`, `on_utterance_end`)
- Modify: `voice/app/voice/tuning.py` (new field)
- Modify: `voice/app/voice/session.py` (`VoiceSession.__init__` builds `EndOfTurn`; `_on_stt` passes `speaking`)
- Test: `voice/tests/test_endpoint.py`, `voice/tests/test_session.py`

**Interfaces:**
- Produces:
  - `EndOfTurn(hold_s: float, utterance_end_wait_s: float = 1.0)`
  - `EndOfTurn.on_transcript(t: Transcript, now: float, speaking: bool = False) -> Commit | None`
  - `EndOfTurn.on_utterance_end(e: UtteranceEnd, now: float, speaking: bool = False) -> Commit | None`
  - `Tuning.utterance_end_wait_s: float = 1.0`
  - `speaking` defaults to False, so existing callers and tests keep today's behavior.

- [ ] **Step 1: Write the failing endpoint tests** in `voice/tests/test_endpoint.py`:
  - `test_a_candidate_end_waits_while_the_user_is_speaking_again`: with `EndOfTurn(hold_s=0.0)`, a final `"scheduled for"` (end 6.2) with `from_finalize=True` and `speaking=True` returns `None` and `has_words()` stays true. A later final `"Thursday."` (end 7.1, `from_finalize=True`, `speaking=False`) returns `Commit(text="scheduled for Thursday.", speech_end=7.1)`.
  - `test_utterance_end_waits_while_the_user_is_still_speaking`: after a final `"I have a recruiter screening with"` (end 3.7) at now 3.8, `on_utterance_end(UtteranceEnd(3.7), now=4.8, speaking=True)` returns `None`, and `poll(5.5)` returns `None`.
  - `test_utterance_end_commits_after_the_wait_if_speech_never_stops`: same setup with `EndOfTurn(hold_s=0.0, utterance_end_wait_s=1.0)`. `poll(5.81)` returns `Commit(text="I have a recruiter screening with", speech_end=3.7)`.
  - `test_new_words_cancel_an_utterance_end_wait`: after the waiting UtteranceEnd, `on_transcript(interim("Dolan", 4.6), now=5.0)` returns `None`. `poll(6.0)` returns `None` and `has_words()` is true.

- [ ] **Step 2: Run them and see them fail**

Run: `cd voice && pytest tests/test_endpoint.py -v`
Expected: the four new tests FAIL (unexpected keyword `speaking` / `utterance_end_wait_s`); the existing tests pass.

- [ ] **Step 3: Implement in `voice/app/voice/endpoint.py`**
  - Store `utterance_end_wait_s`.
  - In `on_transcript`, a `speech_final` or `from_finalize` final returns `None` while `speaking` (finals stay buffered) and otherwise goes to `_candidate_end` as today.
  - In `on_utterance_end`, when `speaking` and there are finals, set `self._deadline = now + self.utterance_end_wait_s` and return `None`. The existing `poll` commits when it passes, and new words already clear `_deadline`.
  - Update the module docstring's policy paragraph to match.

- [ ] **Step 4: Run them and see them pass**

Run: `cd voice && pytest tests/test_endpoint.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing session test** in `voice/tests/test_session.py`:
  - `test_a_final_that_arrives_after_speech_resumed_does_not_split_the_turn`:
    1. In a `speaking_session`, send silence, then 5 `LOUD` frames so the silence detector hears speech.
    2. Emit `final("I have a screening scheduled for", 0.1, 0.9)`. Assert no `user_turn` within `wire.quiet(0.3)`.
    3. Send 0.4 s of silence, then emit `final("Thursday.", 1.0, 1.4)`.
    4. Assert exactly one new `user_turn`, turn 2, text `"I have a screening scheduled for Thursday."`.

- [ ] **Step 6: Wire it in `voice/app/voice/session.py`**
  - In `__init__`: `EndOfTurn(tuning.hold_s(hello.mode), tuning.utterance_end_wait_s)`.
  - In `_on_stt`: pass `speaking=self.vad.speaking` to both `on_utterance_end` and `on_transcript`.
  - In `voice/app/voice/tuning.py`: add `utterance_end_wait_s: float = 1.0`, with a comment saying it's the most an UtteranceEnd waits while the silence detector still hears speech (noisy rooms).

- [ ] **Step 7: Run the voice suite**

Run: `cd voice && pytest -q`
Expected: all pass (185 before this plan, plus the 5 new tests).

- [ ] **Step 8: Commit**

```bash
git add voice/app/voice/endpoint.py voice/app/voice/tuning.py voice/app/voice/session.py voice/tests/test_endpoint.py voice/tests/test_session.py
git commit -m "Don't end a turn while the silence detector hears speech"
```

### Task 2: Words right after a spoken turn join it, even once Wren has started

**Files:**
- Modify: `voice/app/voice/session.py` (`__init__`, `_on_audio`, `_on_stt`, new `_join`)
- Modify: `voice/app/voice/tuning.py` (new field)
- Test: `voice/tests/test_session.py`

**Interfaces:**
- Consumes: Task 1's `speaking` handling. Commits now happen only when the user is quiet, so resumed speech after a commit always starts a new speech-start event.
- Produces:
  - `Tuning.join_window_s: float = 1.0`
  - `VoiceSession._joining: int | None`: the turn number whose reply the user's resumed speech may join.
  - `async VoiceSession._join(reply: Reply) -> None`

- [ ] **Step 1: Write the failing session tests** in `voice/tests/test_session.py`. Each uses `speaking_session` and a FakeChat whose turn-2 reply has played its first segment (`playback start` sent, so the state is "speaking"):
  - `test_words_right_after_a_spoken_turn_join_it_even_after_wren_starts`:
    1. A spoken turn commits `"I have a screening scheduled for"`.
    2. Within 1 s, 5 `LOUD` frames arrive, then 0.4 s of silence, then `final("Thursday.", 1.0, 1.3)`.
    3. Expect `interrupt` for turn 2, then `reply_end` turn 2 with outcome `continued`.
    4. Then `user_turn` turn 3 with text `"I have a screening scheduled for Thursday."`.
    5. The chat's third call's last message content equals that text.
  - `test_a_backchannel_right_after_a_turn_does_not_join_it`: same timing, but the words are `interim("okay")`. Expect `resume` for turn 2 (send a `barge_in` first, as in `test_backchannel_during_pause_resumes`), with no `interrupt` and no `reply_end` outcome `continued`.
  - `test_echo_right_after_a_turn_does_not_join_it`: same timing, but the words are the first words of Wren's turn-2 reply. Expect no `interrupt` and no new `user_turn` within `wire.quiet(0.4)`.
  - `test_a_typed_turn_is_never_joined`: a `user_text` turn, then `LOUD` frames and `final("Thursday.")` within 1 s. Expect no `reply_end` with outcome `continued`, because a lone word isn't an interruption under today's rules.

- [ ] **Step 2: Run them and see them fail**

Run: `cd voice && pytest tests/test_session.py -v -k "join or backchannel_right or echo_right or typed_turn_is_never"`
Expected: the join test FAILS (no `interrupt`, because "Thursday." is dropped as one word). The other three may already pass; they pin today's behavior.

- [ ] **Step 3: Implement in `voice/app/voice/session.py`**
  - **In `_on_audio`, when the silence detector reports `"start"`** and the state is "speaking" or "paused": if the current reply is a spoken turn (`timing.input == "voice"`) and `now - reply.timing.committed <= tuning.join_window_s`, set `self._joining = reply.no`. Keep the existing "thinking" branch first.
  - **In `_on_stt`, in the speaking/paused branch, before `is_interruption`:** if `self._joining == reply.no` and `real_words(text, reply.full_text())` is non-empty, `await self._join(reply)` and fall through to the endpoint as the interrupt path does.
  - **`_join(reply)`:**
    1. Send `{"type": "interrupt", "turn": reply.no}` so the browser drops queued audio.
    2. Set `reply.end_sent = False`, so `_finish` sends `reply_end` with outcome `continued` even after a `complete`.
    3. Then do what `_continue(reply)` does, and clear `self._joining`.
  - **In `voice/app/voice/tuning.py`:** add `join_window_s: float = 1.0`, with a comment: speech resuming within this long after a spoken turn ends belongs to that turn.

- [ ] **Step 4: Run them and see them pass**

Run: `cd voice && pytest tests/test_session.py -v`
Expected: all PASS, including the existing barge-in, backchannel and echo tests.

- [ ] **Step 5: Run the voice suite**

Run: `cd voice && pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add voice/app/voice/session.py voice/app/voice/tuning.py voice/tests/test_session.py
git commit -m "Let words right after a spoken turn join it"
```

### Task 3: Document the rules and check them live

**Files:**
- Modify: `docs/superpowers/specs/2026-10-16-voice-loop-design.md` ("Turn lifecycle" table, and step 1 of "How the policy decides")

- [ ] **Step 1: Update the design doc**
  - Add two rows to the "Turn lifecycle" table:
    - listening | A final or UtteranceEnd arrives while the silence detector hears speech | Don't commit. Wait for quiet; UtteranceEnd waits at most `utterance_end_wait_s` (1 s).
    - speaking or paused | Real words, with speech that resumed within `join_window_s` (1 s) of a spoken turn's commit | Stop playback, cancel as `continued`, merge the words into that turn.
  - In "How the policy decides", note that a candidate end needs the silence detector to be quiet.

- [ ] **Step 2: Check latency with the bench**

Run (both servers up, `WREN_DEV_USER_ID` set): `cd voice && python -m app.tools.bench --barge-in`
Expected: 6 of 6 spoken turns whole, TTFA p50 ≤ 1,000 ms and p95 ≤ 1,500 ms. Session 3's final bench was p50 957 ms and p95 1,150 ms, measured with memory off; compare with the same memory state.

- [ ] **Step 3: Check the sample in the browser, twice**

Run: `python web/e2e/session_e2e.py --mode voice`, then again.
Expected: "the spoken sentence ends up as one answered turn" and "the page shows the user's words once" PASS in both runs. The word-error check can still fail when Chrome's processing drops the name, which is decision 17 and out of scope here.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-10-16-voice-loop-design.md
git commit -m "Document the end-of-turn rules for speech that resumes"
```
