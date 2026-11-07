# Room Noise Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wren keeps talking through other people's voices and only stops for the owner's own voice at the phone.

**Architecture:** The page's detector and the server both learn how loud the room's talkers get while Wren speaks. Voice counts as the owner's only when it is clearly louder than that, for long enough. The page pauses Wren only for that voice. The server lets words interrupt or cancel a reply only with that voice behind them, keeps ignored words out of the next turn, and ends any pause that gets no words within a cap.

**Tech Stack:** TypeScript (`node --test`), Python 3.14 (pytest, FastAPI TestClient), Playwright with Chrome's fake mic for the browser run. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-room-noise-design.md`. Parent: `docs/superpowers/specs/2026-10-16-voice-loop-design.md`.

## Global Constraints

- The spec's 300 ms pause target holds for a clear start.
- The server's end-of-speech detection (`EnergyVad` and Finalize) and the end-of-turn logic (`endpoint.py`) are unchanged.
- No protocol change: no new message types or fields.
- Text mode is unchanged. Typed turns never depend on the mic.
- Constants are named and mirrored between `web/lib/voice/vad.ts` and `voice/app/voice/vad.py`: `ROOM_FRAMES = 400` (8 s of 20 ms frames), `ROOM_MIN_FRAMES = 50`, `ROOM_PERCENTILE = 0.8` (the 80th, so short overlaps by the owner — under a fifth of the time — never become the yardstick), `ROOM_MARGIN = 2.0`, onset 200 ms of voice within a 260 ms window.
- The owner pushes nothing, and production changes only in Task 5. Pushing master redeploys Render.

## Review Focus

1. **A phone whose echo cancellation leaks Wren's voice.** The yardstick rises with the echo, so the owner's voice might no longer clear it, and voice interruption would stop working. Expected: the room yardstick never sets the bar above today's for steady echo, so the owner's voice still pauses Wren wherever it does today (Task 1 test).
2. **The room goes quiet again.** Once the talkers leave, the yardstick falls within about 8 s, and a normal voice pauses Wren as in a quiet room (Task 1 test).
3. **The owner talks over Wren often.** The yardstick must not learn the owner's own voice from short overlaps (Task 1 test).
4. **The first second of a session in a loud room,** before Wren has said anything. The yardstick is learned from the frames before the opening's first audio, so the opening isn't paused (Task 1 client test).
5. **Words that arrive after the cap has resumed Wren,** from a slow transcript. They still interrupt if the server heard the owner's voice within the last 2 s (Task 3 test).

---

### Task 1: The page judges voice against the room's talkers

**Files:**
- Modify: `web/lib/voice/vad.ts` (add `RoomTalk`; `Vad.push` gains `roomFrame`; onset 200 ms within 260 ms)
- Modify: `web/lib/voice/client.ts` (pass `roomFrame`; learn from the frames before the opening's first audio)
- Test: `web/tests/vad.test.ts`, `web/tests/client.test.ts`

**Interfaces:**
- Produces:
  - `export class RoomTalk { observe(rms: number): void; level(): number | null }`. `level()` is the `ROOM_PERCENTILE` of the last `ROOM_FRAMES` observed levels, or `null` with fewer than `ROOM_MIN_FRAMES`.
  - `Vad.push(rms, nowMs, wrenSpeaking, frameMs = 20, roomFrame = wrenSpeaking): "onset" | null`. Room frames feed `RoomTalk`. While Wren speaks, the bar is `max(today's speaking bar, room.level() × ROOM_MARGIN)`.
  - `new Vad({ startMs = 200 })` with a 260 ms window. Today's floor, `MIN_RMS` and speaking boost stay as the quiet-room bar.
- Client: from a `ready` with `resumed: false` until the first audio frame, mic frames go in as room frames (`roomFrame = audible || this.openingPending`).

- [ ] **Step 1: Write the failing tests** in `web/tests/vad.test.ts`, with a `talk(seconds, loud, quiet)` helper that alternates 0.3 s at `loud` with 0.2 s at `quiet`, like people taking turns:
  - `the room's talkers don't pause Wren`: 2 s of talk (0.04/0.002) as room frames before Wren speaks, then 8 s of the same while Wren speaks. Expect no onset while Wren speaks.
  - `your voice over the room's talkers still pauses Wren`: after that, 0.4 s at 0.2. Expect an onset 200 ms after it starts, with `onsetAt` at its start.
  - `a voice as loud as the room's talkers doesn't count`: after the talk, 0.4 s at 0.06. Expect no onset.
  - `the bar falls again once the room is quiet`: 8 s of talk, then 9 s at 0.001, all while Wren speaks, then 0.3 s at 0.02. Expect an onset.
  - `echo that leaks back doesn't raise the bar past today's`: 8 s of 0.01 while Wren speaks (echo), then 0.3 s at 0.1. Expect an onset, as today.
  - `short overlaps don't teach it your voice`: 8 s of talk with a 0.3 s burst at 0.2 every 2 s. The bursts after the first second each give an onset.
  - Update today's tests for the 200 ms onset. `vad onset needs 200ms of voice`: voice from 400 ms gives an onset at 580. Shift the dip test and the once-per-stretch test the same way.
- [ ] **Step 2: Write the failing client test** in `web/tests/client.test.ts`:
  - `room talk before and during the opening doesn't pause Wren`: start the client with frames at the talk pattern (rms 0.04 and 0.002) from `ready` until the first audio, then keep them going while Wren speaks. Expect no `barge_in` sent.
  - Then `speakOver()` (rms 0.5). Expect one `barge_in`.
  - The harness's `FakePlayer.audible` must be settable, so frames before the opening's audio aren't audible.
- [ ] **Step 3: Run them and see them fail.** `cd web && npm test`. Expected: the new tests fail (onsets on the room's talk), and the updated timing tests fail at 480 against 580.
- [ ] **Step 4: Implement** `RoomTalk` and the new `Vad.push` in `vad.ts`, then `openingPending` in `client.ts`, set on `ready` when `resumed` is false and cleared on the first audio frame. Update the file's header comment: why the room's talkers are the yardstick, and the 200 ms measured trade.
- [ ] **Step 5: Run `npm test` and `npm run lint`.** Expected: all pass.
- [ ] **Step 6: Commit:** "Pause Wren only for voice clearly louder than the room's talkers"

### Task 2: The server's yardstick

**Files:**
- Modify: `voice/app/voice/vad.py` (add `RoomTalk` and `ClearVoice`; `EnergyVad` unchanged)
- Test: `voice/tests/test_vad.py`

**Interfaces:**
- Produces, mirroring Task 1's constants:
  - `RoomTalk.observe(level: float) -> None`.
  - `RoomTalk.level() -> float | None`.
  - `RoomTalk.clearly_you(level: float) -> bool`: `level > max(MIN_YOU, room × ROOM_MARGIN)`, or `level > MIN_YOU` before the room is known. `MIN_YOU = 200.0`, the same as `EnergyVad`'s start floor.
  - `ClearVoice.push(clear: bool, now: float) -> None`. It marks `last_at = now` once 200 ms of clear frames fall within 260 ms.
  - `ClearVoice.heard_within(now: float, seconds: float) -> bool`.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_room_level_is_how_loud_its_talkers_get`: 400 frames, 60% at 600 and 40% at 3000. Expect `level()` = 3000. With 49 frames, expect `None`.
  - `test_clearly_you_needs_twice_the_room_and_the_floor`: with the room at 3000, 5999 is not you and 6001 is. Before the room is known, 201 is you and 199 isn't.
  - `test_clear_voice_needs_200ms_within_260ms`: 9 clear frames give no mark, the 10th does. A gap of 3 frames inside the window still counts.
  - `test_heard_within`: marked at 1.0, `heard_within(2.9, 2.0)` is true, and `heard_within(3.1, 2.0)` is false.
- [ ] **Step 2: Run** `cd voice && pytest tests/test_vad.py -q`. Expected: import errors for `RoomTalk` and `ClearVoice`.
- [ ] **Step 3: Implement** both classes. The percentile comes from a sorted copy of a 400-item deque, which is cheap at 50 frames a second.
- [ ] **Step 4: Run** `pytest tests/test_vad.py -q`. Expected: pass.
- [ ] **Step 5: Commit:** "Teach the server how loud the room's talkers get"

### Task 3: The session uses the yardstick

**Files:**
- Modify: `voice/app/voice/session.py` (`_on_audio`, `_on_stt`, `_tick`)
- Modify: `voice/app/voice/tuning.py` (`pause_cap_s: float = 2.5`, `heard_you_s: float = 2.0`)
- Test: `voice/tests/test_session.py`

**Interfaces:**
- Consumes: Task 2's `RoomTalk`, `ClearVoice` and `rms`.
- In the session:
  - `self.room = RoomTalk()` and `self.you = ClearVoice()`, made once per session. They aren't reset on reconnect.
  - `_on_audio` computes the frame's level once. It observes the room while Wren is speaking or preparing the opening (`state == "speaking"`, or `state == "thinking"` with `reply.user_text is None`). It pushes `self.room.clearly_you(level)` into `self.you`.
  - `_heard_you(now) -> bool` returns `self.you.heard_within(now, tuning.heard_you_s)`.

- [ ] **Step 1: Write the failing tests.** Add helpers: `ROOM = b"\x00\x06" * 320` (about 1536 RMS, talk a metre away) and `send_room(ws, stt, seconds)`.
  - `test_room_talk_while_wren_prepares_doesnt_cancel_the_reply`: play the opening while sending room frames. Then a typed turn whose chat hangs before its first token. Send 1 s of room frames. Expect no `reply_end` with outcome `continued`, and the reply arrives when the chat goes on.
  - `test_a_pause_with_no_words_resumes_despite_room_noise`: room frames during the opening. During reply 2, `barge_in`, then room frames keep coming. Expect `resume` within 3 s (`confirm_window_s`, or at most `pause_cap_s`).
  - `test_background_words_while_wren_speaks_are_ignored`: room frames during the opening and reply 2. While reply 2 speaks, emit a final "did you see the game last night". Expect no `interrupt`. After reply 2 completes, the owner's next turn is "Tell me more" (LOUD frames, then that final), and its `user_turn` text is exactly "Tell me more".
  - `test_your_words_interrupt_without_a_page_pause`: while reply 2 speaks, 0.3 s of LOUD frames, then a final "wait, stop". Expect `interrupt`.
  - `test_words_soon_after_your_voice_still_count`: LOUD frames, then 1.5 s of room frames, then the final. Expect `interrupt`. At 2.5 s, expect no interrupt.
  - Update the existing tests where words arrive while Wren speaks or prepares, with no page `barge_in`: they send 0.3 s of LOUD frames first, since words now need voice behind them. `test_words_before_the_opening_still_replace_it` is one of them. List each in the ledger.
- [ ] **Step 2: Run** `cd voice && pytest tests/test_session.py -q`. Expected: the new tests fail (cancelled, never resumed, interrupted by background words), and the updated ones pass already.
- [ ] **Step 3: Implement:**
  - In `_on_audio`: the room and `you` updates. The `heard == "start"` cancel while Wren prepares a reply needs `_heard_you(now)`. `speech_resumed()` and `_last_speech` are unchanged.
  - In `_on_stt`: in state `speaking`, a transcript that passes `is_interruption` interrupts only with `_heard_you(now)`, and otherwise returns early, before the endpoint. State `paused` interrupts as today. In state `thinking`, `real_words` cancel only with `_heard_you(now)`, and otherwise return early.
  - In `_tick`: resume once `now - paused_at >= confirm_window_s`, and either no clear voice in the last 0.3 s, or `now - paused_at >= pause_cap_s`. Keep the comment's reasoning about Deepgram's lag.
- [ ] **Step 4: Run the voice suite:** `pytest -q`. Expected: all pass, 675 plus the new tests.
- [ ] **Step 5: Commit:** "Let only the owner's voice interrupt Wren or hold it paused"

### Task 4: Prove it in the browser, and document it

**Files:**
- Modify: `web/e2e/session_e2e.py` (`--chatter DB`, `--chatter-style crowd|talker`)
- Modify: `docs/superpowers/specs/2026-10-16-voice-loop-design.md`: a "Build notes: noisy rooms (2026-11-06)" section pointing to the new design, and corrections where the parent states the 100 ms onset, or that any transcript interrupts.

- [ ] **Step 1: Add the chatter track** to `build_mic`:
  - Build talk from `sample.wav`, time-reversed: four overlapping copies at 0, 1.7, 3.1 and 4.6 s offsets for `crowd`, or one copy with 1.5 s gaps between repeats for `talker`.
  - Loop it under the whole track, scaled so its voiced RMS sits DB below the sample's voiced RMS.
  - Add the check `the page never paused Wren for the room`: no `barge_in` sent outside the sample's span, padded 1 s each side. Report the chatter level in the "fake mic" section.
- [ ] **Step 2: Run, with both servers on the worktree and a throwaway dev id:**
  - `python web/e2e/session_e2e.py --mode both`
  - `--mode voice --chatter 12 --chatter-style talker`
  - `--mode voice --chatter 12 --chatter-style crowd`
  - Expected: the new check passes; the opening and reply play to the end; the sample becomes one answered turn. Today's environment also needs `--no-apm` for the WER check to pass; run each chatter style both ways and ledger the results.
- [ ] **Step 3: Update the parent design doc** as listed above.
- [ ] **Step 4: Commit:** "Test Wren against a talking room in the browser, and document it"

### Task 5: Ship it

- [ ] **Step 1:** Final whole-branch review, with a fresh reviewer, before anything ships. Fix Critical and Important findings test-first.
- [ ] **Step 2:** Fast-forward master to the branch, after checking master hasn't moved. Scan the outgoing commits for `.env` values. Push. Render redeploys itself.
- [ ] **Step 3:** Leak-check a local production build with the Render URL, then `npx vercel@64.5.0 deploy --prod` from `web/`.
- [ ] **Step 4:** Production checks: `prod_check` and the served-asset scan. Then the owner tries it on the phone in a room with people talking.
