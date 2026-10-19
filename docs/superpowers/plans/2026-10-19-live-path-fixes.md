# Live-Path Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the deploy-blocking bugs the voice-loop review found (I1–I4). Also fix three smaller bugs that matter on a public server (M2, M13, M14), and bring the persona card back in line with the wren-voice boundaries.

**Architecture:**
- **Database (I1, I2):** the live connection connects with a 5 s timeout and TCP keepalives, and runs every query under a 3 s statement timeout. When a query finds the connection dropped, it reconnects and runs once more. Appending turns skips turn ids already saved, so a retry can't duplicate them.
- **Deadlines (I2):** `run_turn` waits at most `retrieval_timeout_s` (1.5 s) for memories, then answers without them. Each chat model gets 4 s to send its first token before the next model is tried. The Gemini client gives up after 60 s instead of never.
- **Playback (I3):** time the browser spends paused extends that reply's playback deadline. A `barge_in` for a reply the server isn't playing gets a `resume`. On each new user turn, the page drops any audio left over from earlier replies.
- **Session cap (I4):** the cap closes the socket with its own code, 4410, and the page doesn't reconnect after it.
- **Smaller fixes:**
  - Sound with no words no longer cancels the opening (M2).
  - Bad client messages are logged without their contents (M13).
  - The leak check scans the whole web build (M14).
  - The persona card states every wren-voice boundary in full.

**Tech Stack:** Python 3.14 (FastAPI, psycopg 3.3, google-genai 2.31.0, groq), pytest with the fakes in `voice/tests/voice_fakes.py`. Next.js 16 client, tested with `node --test`. No new dependencies.

**Spec:**
- `docs/superpowers/specs/2026-10-16-voice-loop-design.md`: "Wire protocol", "Turn lifecycle" and "Fallbacks and limits"
- `CLAUDE.md`
- the wren-voice skill, for the persona card

The findings come from the reviewer-agent review of `voice-loop` (decision 22, 2026-10-19). Each task names the finding it fixes.

## Global Constraints

- TTFA targets: p50 ≤ 1.0 s, p95 ≤ 1.5 s. A user who has finished talking must not wait longer than today.
- Barge-in: playback pauses within 300 ms of the user's first sound. That happens in the browser and doesn't change here.
- Text mode keeps using the same `run_turn` as voice mode.
- Every turn still logs its timing record (`log_turn` and `turns.jsonl`).
- Wren's words and the persona card follow the wren-voice skill.
- No new dependencies. Free tiers only.
- **No memory-eval run.** Nothing here changes what reaches retrieval or how it ranks, and the memory-evals skill exempts voice transport. The harness calls `retrieve()` directly, so the new deadline isn't on its path. The statement timeout applies only to the live connection; the harness shares `store.connect` but doesn't ask for it.
- **Live checks run with memory off.** Use a throwaway `WREN_DEV_USER_ID` in the server's process environment only, as in session 4, so test sessions never land in the owner's account. The database paths are covered by `WREN_DB_TESTS=1`, which rolls everything back.
- Work on the branch `live-path-fixes` off `master`, with one commit per task.

## Review Focus

1. **A database that stays down**, not just one dropped connection: one retry, then memory off with the existing "database unreachable" reason. Wren still talks (Task 1, `test_a_database_that_stays_down_turns_memory_off_after_one_retry`).
2. **A retrieval slower than the deadline:** the reply goes ahead on time without memories and records `retrieval_fallback="timeout"` (Task 2, `test_a_slow_retrieval_is_dropped_at_the_deadline`).
3. **A reply paused twice by echo, then really interrupted:** the server still holds the reply and confirms the interruption from words (Task 3, `test_a_twice_paused_reply_can_still_be_interrupted`).
4. **The cap firing while Wren is speaking:** the socket closes with 4410, and Wren's turn is saved with only the words that played (Task 4, `test_the_cap_mid_reply_saves_what_was_heard`).
5. **Words while the opening is still being prepared:** they still replace the opening and become the user's turn. Only sound without words leaves it alone (Task 5, `test_words_before_the_opening_still_replace_it`).

## Before you start

- [ ] `git switch -c live-path-fixes master`
- [ ] **Baseline.** Start the voice server with a throwaway `WREN_DEV_USER_ID`, set in the server's process environment only. Start the web app too. Then run:
  - `cd voice && python -m app.tools.bench --barge-in`
  - `python web/e2e/session_e2e.py --mode both`

  Keep both summaries for Task 8. Session 4's results for comparison, on the same setup: text 22 of 22, and voice 21 of 24, with the known end-of-turn and Chrome-processing failures.

---

### Task 1: The live database connection survives a drop (I1)

**Files:**
- Modify: `voice/app/memory/store.py` (`connect`, `append_turns`)
- Modify: `voice/app/memory/live.py` (`PostgresMemory._run`, plus a `LIVE_STATEMENT_TIMEOUT_MS = 3000` constant)
- Test: `voice/tests/test_memory_live.py`, `voice/tests/test_store_db.py`

**Interfaces:**
- Produces:
  - **`store.connect(database_url: str, *, statement_timeout_ms: int | None = None) -> psycopg.Connection`**
    - Also passes `connect_timeout=5`, `keepalives=1`, `keepalives_idle=30`, `keepalives_interval=10` and `keepalives_count=3`.
    - With `statement_timeout_ms`, it sets the session's statement timeout with `select set_config('statement_timeout', %s, false)`, because `SET` takes no bind parameters. `DATABASE_URL` is the session pooler, so the setting holds for the connection's life.
  - **`store.append_turns(conn, session_id, turns, *, schema="public") -> None`:** same signature. In one `UPDATE`, it skips turns whose `id` is already in the transcript (jsonb containment on `id`), and keeps the order of the rest.
  - **`PostgresMemory._run(work)`:**
    - On `psycopg.OperationalError`, it closes the connection, reconnects and runs `work` once more. A second `OperationalError` propagates.
    - It connects with `statement_timeout_ms=LIVE_STATEMENT_TIMEOUT_MS`.
    - Retrying is safe: every `work` either runs inside `as_user`'s transaction, which rolls back with a dead connection, or is idempotent.

- [ ] **Step 1: Write the failing tests** in `voice/tests/test_memory_live.py`. Import `SimpleNamespace`, `Settings`, `store` and `PostgresMemory`.

```python
class DeadConn:
    """A connection a pooler or NAT dropped while idle: psycopg finds out only on use."""
    closed = broken = False

    def execute(self, *args, **kwargs):
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    def close(self):
        self.closed = True


class LiveConn:
    closed = broken = False

    def execute(self, *args, **kwargs):
        return SimpleNamespace(fetchone=lambda: [True])

    def close(self):
        self.closed = True


SETTINGS = Settings(
    gemini_api_key="g", gemini_flash_model="f", gemini_embed_model="e", groq_api_key="q",
    groq_rewrite_model="r", database_url="postgresql://x", supabase_url="u", supabase_secret_key="s",
)


def connections(monkeypatch, *conns):
    made = []

    def connect(url, **kwargs):
        made.append(kwargs)
        return conns[len(made) - 1]

    monkeypatch.setattr(store, "connect", connect)
    return made


def test_the_session_after_a_dropped_connection_still_has_memory(monkeypatch):
    connections(monkeypatch, DeadConn(), LiveConn())
    memory = asyncio.run(MemoryProvider(postgres=PostgresMemory(SETTINGS)).for_user(USER))
    assert memory.status == "on"


def test_a_database_that_stays_down_turns_memory_off_after_one_retry(monkeypatch):
    made = connections(monkeypatch, DeadConn(), DeadConn(), LiveConn())
    memory = asyncio.run(MemoryProvider(postgres=PostgresMemory(SETTINGS)).for_user(USER))
    assert memory.status == "off: database unreachable (OperationalError)"
    assert len(made) == 2


def test_live_connections_get_a_statement_timeout(monkeypatch):
    made = connections(monkeypatch, LiveConn())
    PostgresMemory(SETTINGS).unavailable_reason(USER)
    assert made == [{"statement_timeout_ms": 3000}]
```

Then add this to `voice/tests/test_store_db.py`:

```python
def test_appending_the_same_turns_again_keeps_one_copy(db):
    conn, a, _ = db
    first = [{"id": "s:t01", "speaker": "user", "text": "hi"}, {"id": "s:t02", "speaker": "wren", "text": "Hey."}]
    with as_user(conn, a):
        session_id = start_session(conn, a, "checkin", NOW)
        append_turns(conn, session_id, first)
        append_turns(conn, session_id, first)  # a retry after the first write had landed
        append_turns(conn, session_id, [first[1], {"id": "s:t03", "speaker": "user", "text": "Friday."}])
        _, _, transcript = load_transcript(conn, session_id)
    assert [t["id"] for t in transcript] == ["s:t01", "s:t02", "s:t03"]
```

- [ ] **Step 2: Run them and see them fail.**
  - `cd voice && pytest tests/test_memory_live.py -v`. Expected: the three new tests fail.
  - `WREN_DB_TESTS=1 pytest tests/test_store_db.py -v`. Expected: the new test fails with six entries.
- [ ] **Step 3: Implement** `connect`, `append_turns` and `_run` as above.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q` and `WREN_DB_TESTS=1 pytest -q tests/test_store_db.py`. Expected: all pass.
- [ ] **Step 5: Commit:** "Reconnect the live database once when a connection has dropped"

### Task 2: Every call on the live path has a deadline (I2)

**Files:**
- Modify: `voice/app/voice/tuning.py` (`retrieval_timeout_s: float = 1.5`, with a comment: the most a turn waits for memories before answering without them)
- Modify: `voice/app/turn.py` (`run_turn`)
- Modify: `voice/app/chat.py` (`CHAT_FIRST_TOKEN_S = 4.0`, `make_chat`)
- Modify: `voice/app/memory/llm.py` (`GEMINI_TIMEOUT_MS = 60_000`, `_client`)
- Test: `voice/tests/test_turn.py`, `voice/tests/test_chat.py`, `voice/tests/test_llm.py`

**Interfaces:**
- Produces:
  - **`run_turn`:** awaits `retrieve` under `asyncio.wait_for(..., tuning.retrieval_timeout_s)`. On a timeout it logs a warning, sets `timing.retrieval_fallback = "timeout"`, and answers with no memories. Catch `TimeoutError` before the existing `except Exception`, because `TimeoutError` is an `OSError`.
  - **`make_chat(settings, voice, *, max_tokens=400, groq=None, gemini=None, first_token_s: float = CHAT_FIRST_TOKEN_S)`:** a model whose first token doesn't arrive within `first_token_s` counts as failing before its first token. It's logged, `timing.chat_fallback` keeps its first value (here `"TimeoutError"`), and the next model is tried. Groq's 10 s client timeout stays as the stall guard after the first token.
  - **`llm._client(api_key)`:** `genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))`. HttpOptions takes milliseconds. Extraction and eval generation share this client, and their calls take about 10–20 s.

- [ ] **Step 1: Write the failing tests.** In `voice/tests/test_turn.py`, add `from time import monotonic`, because the module's `time` is `datetime.time`:

```python
def test_a_slow_retrieval_is_dropped_at_the_deadline():
    async def retrieve(text):
        await asyncio.sleep(2)
        return Retrieved(HITS, 2_000.0)

    chat = FakeChat(["Got it."])
    timing = TurnTiming(turn=3, mode="checkin", input="voice")
    started = monotonic()
    out = asyncio.run(_collect(run_turn(
        conversation(), "hello", retrieve=retrieve, chat=chat, timing=timing, tuning=Tuning(retrieval_timeout_s=0.05),
    )))
    assert out == ["Got it."] and monotonic() - started < 1.0
    assert timing.retrieval_fallback == "timeout" and timing.memories == 0
    assert "Nothing relevant yet." in chat.messages[0]["content"]
```

In `voice/tests/test_chat.py`:

```python
def test_a_model_without_a_first_token_in_time_falls_back():
    stream = FakeGroqStream(["never"], hang_after=0)
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    chat = make_chat(SETTINGS, VOICE, groq=FakeGroq(stream), gemini=FakeGemini(["Okay."]), first_token_s=0.05)
    assert asyncio.run(asyncio.wait_for(collect(chat, timing), 2)) == ["Okay."]  # never hang the suite
    assert timing.chat_fallback == "TimeoutError" and timing.model == "gemini-test-flash"
    assert stream.closed
```

In `voice/tests/test_llm.py`:

```python
def test_the_gemini_client_times_out(monkeypatch):
    made = {}
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: made.update(kwargs) or "client")
    llm._client.cache_clear()
    try:
        assert llm._client("key") == "client"
    finally:
        llm._client.cache_clear()
    assert made["api_key"] == "key" and made["http_options"].timeout == 60_000
```

- [ ] **Step 2: Run them and see them fail.** `cd voice && pytest tests/test_turn.py tests/test_chat.py tests/test_llm.py -v`. Expected: the three new tests fail.
- [ ] **Step 3: Implement** the three deadlines as above.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** "Put a deadline on every call in the live path"

### Task 3: Paused time extends the playback deadline (I3)

**Files:**
- Modify: `voice/app/voice/session.py`:
  - `Reply`: a new `paused_s` field
  - `_tick`: the resume branch adds the pause to `paused_s`; the playback-deadline check adds `paused_s`
  - `_on_barge_in`
- Modify: `web/lib/voice/client.ts`:
  - `onMessage`: the `user_turn` case
  - `connect`
- Test: `voice/tests/test_session.py`

**Interfaces:**
- Produces:
  - **`Reply.paused_s: float = 0.0`:** the total time the browser held this reply paused. The playback deadline becomes `first_audio_sent + audio_s + paused_s + playback_grace_s`.
  - **`barge_in` rules:**
    - For the current reply while "speaking": pause, as today.
    - For the current reply while already "paused": ignore it.
    - Anything else, meaning no reply, another turn or a finished reply: answer `{"type": "resume", "turn": msg.turn}`, so the browser never holds a pause nobody will end.
  - **Client:**
    - A `user_turn` message drops any audio still queued (`this.player?.flush()`) and clears `pausing`. That audio belongs to replies the server has already finished or interrupted.
    - `connect()` clears `pausing` alongside its existing `player.reset()`, which fixes M10.

- [ ] **Step 1: Write the failing tests** in `voice/tests/test_session.py`. With `FAST` tuning, the grace is 0.5 s, the confirm window is 0.3 s, and a one-segment reply is 200 ms of audio:

```python
def test_pauses_extend_the_playback_deadline():
    chat = FakeChat([OPENING, ["Lead with the number and then the result."]])
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("reply_end", turn=2)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 50, "onset_ms": 100})
        for _ in range(50):  # about 1 s of murmuring: longer than the audio plus the grace
            ws.send_bytes(LOUD)
            time.sleep(0.02)
        send_silence(ws, stt, 0.4)
        wire.until("resume", turn=2, timeout=3)
        assert not [m for m in wire.quiet(0.3) if m.get("outcome") == "complete"]  # the browser is still playing
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "end"})
        wire.until("metrics", turn=2, outcome="complete")
    finally:
        session.__exit__(None, None, None)


def test_a_twice_paused_reply_can_still_be_interrupted():
    chat = FakeChat([OPENING, ["Lead with the number and then the result."]])
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("reply_end", turn=2)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        for played in (40, 80):  # two pauses with no words, as echo on speakers causes
            ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": played, "onset_ms": 100})
            wire.until("resume", turn=2, timeout=3)
        wire.quiet(0.2)  # past the old deadline: 200 ms of audio plus 0.5 s of grace
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 120, "onset_ms": 100})
        stt.emit(interim("wait, I meant Monday"))
        wire.until("interrupt", turn=2)
    finally:
        session.__exit__(None, None, None)


def test_a_barge_in_for_a_finished_reply_gets_a_resume():
    with voice_session(make_runtime(FakeChat([OPENING]), stt=FakeStt())) as (ws, wire, _):
        play(ws, wire, turn=1)
        ws.send_json({"type": "barge_in", "turn": 1, "seg": 0, "played_ms": 300, "onset_ms": 100})
        wire.until("resume", turn=1, timeout=2)
```

- [ ] **Step 2: Run them and see them fail.** `cd voice && pytest tests/test_session.py -v -k "pauses_extend or twice_paused or finished_reply"`. Expected: all three fail. The first two fail because the server finishes the reply early; the third gets no answer.
- [ ] **Step 3: Implement the server side** as above.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass, including `test_a_reply_finishes_even_if_the_browser_never_reports_playback`.
- [ ] **Step 5: Implement the client side** as above. `client.ts` has no unit harness, so Task 8's browser run checks it. Run `cd web && npm test`. Expected: all pass.
- [ ] **Step 6: Commit:** "Count paused time in the playback deadline and answer stale barge-ins"

### Task 4: The 45-minute cap ends the session for good (I4)

**Files:**
- Modify: `voice/app/voice/session.py` (a `SESSION_OVER_CLOSE = 4410` constant; `_tick` closes with it)
- Modify: `web/lib/voice/protocol.ts` (`CLOSE_CODES`, `reconnects`)
- Modify: `web/lib/voice/client.ts` (`ws.onclose` uses `reconnects`)
- Test: `voice/tests/test_session.py`, `web/tests/protocol.test.ts`

**Interfaces:**
- Produces:
  - **Close code 4410, "the session is over":** the server sends the `session_over` error, then closes the socket with 4410.
  - **`export const CLOSE_CODES = { badHello: 4400, unauthorized: 4401, sessionOver: 4410 }`**
  - **`export function reconnects(code: number): boolean`:** false for 4401 and 4410, true otherwise. When it's false, the page goes idle and keeps the notice it's already showing, so "That's our time…" stays on screen.

- [ ] **Step 1: Write the failing tests.** In `voice/tests/test_session.py`, import `replace` from `dataclasses`:

```python
def test_the_session_cap_closes_with_its_own_code():
    rt = make_runtime(FakeChat([OPENING]), stt=None, tuning=replace(FAST, session_cap_s=1.0))
    with voice_session(rt) as (ws, wire, _):
        over, _ = wire.until("error", code="session_over", timeout=3)
        assert over["message"] == "That's our time for this session. Start a new one whenever you're ready."
        assert wire.next(timeout=3) == {"type": "_closed", "code": 4410}


def test_the_cap_mid_reply_saves_what_was_heard():
    memory = RecordingMemory()
    chat = FakeChat([OPENING, ["First sentence here is long enough to cut. ", "Second one. "]], hang_after={1: 2})
    rt = make_runtime(chat, stt=FakeStt(), memory=memory, tuning=replace(FAST, session_cap_s=1.5))
    with voice_session(rt) as (ws, wire, ready):
        play(ws, wire, turn=1)
        ws.send_json({"type": "user_text", "text": "Give me feedback"})
        wire.until("segment_end", turn=2, seg=1)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        wire.until("error", code="session_over", timeout=3)
        deadline = time.monotonic() + 3
        while not memory.closed and time.monotonic() < deadline:  # the shutdown saves, then closes the session
            time.sleep(0.02)
    assert memory.closed == [ready["session_id"]]
    wren = [t for t in memory.appended if t["speaker"] == "wren"][-1]
    assert wren["text"] == "First sentence here is long enough to cut." and wren.get("interrupted") is True
```

In `web/tests/protocol.test.ts`:

```ts
test("only a refusal or the session cap stops the page from reconnecting", () => {
  assert.equal(reconnects(CLOSE_CODES.unauthorized), false);
  assert.equal(reconnects(CLOSE_CODES.sessionOver), false);
  assert.equal(reconnects(1006), true);
  assert.equal(reconnects(1000), true);
});
```

- [ ] **Step 2: Run them and see them fail.**
  - `cd voice && pytest tests/test_session.py -v -k cap`. Expected: the close-code test fails, with code 1000 instead of 4410. The save test already passes; it pins the behavior.
  - `cd web && npm test`. Expected: the import of `reconnects` fails.
- [ ] **Step 3: Implement** both sides.
- [ ] **Step 4: Run both suites.** `cd voice && pytest -q`, then `cd web && npm test`. Expected: all pass.
- [ ] **Step 5: Commit:** "Make the 45-minute cap end the session for good"

### Task 5: Sound alone never cancels the opening (M2)

**Files:**
- Modify: `voice/app/voice/session.py` (`_on_audio`, the silence detector's `"start"` branch)
- Modify: `voice/tests/voice_fakes.py` (a new `FakeChat` parameter, `delay_before`)
- Test: `voice/tests/test_session.py`

**Interfaces:**
- Produces:
  - The silence detector's `"start"` cancels a thinking reply only when it answers the user, meaning `reply.user_text is not None`. The opening is still replaced by real words, through `_on_stt`'s existing thinking branch.
  - **`FakeChat(replies, hang_before=(), hang_after=None, delay_before: dict[int, float] | None = None)`:** seconds to wait before call N's first delta.

- [ ] **Step 1: Write the failing test,** plus a test that pins today's word path:

```python
def test_a_cough_before_the_opening_plays_keeps_it():
    stt = FakeStt()
    rt = make_runtime(FakeChat([OPENING], delay_before={0: 0.3}), stt=stt)
    with voice_session(rt) as (ws, wire, _):
        for _ in range(5):  # 100 ms of noise while the opening is still being prepared
            ws.send_bytes(LOUD)
        send_silence(ws, stt, 0.4)
        segments = play(ws, wire, turn=1)
        assert [s["text"] for s in segments] == ["Hey Maya.", "How's the week going?"]


def test_words_before_the_opening_still_replace_it():
    stt = FakeStt()
    rt = make_runtime(FakeChat([OPENING, ["Sure. Start with your opener."]], hang_before={0}), stt=stt)
    with voice_session(rt) as (ws, wire, _):
        send_silence(ws, stt, 0.2)
        stt.emit(final("Can we practice my Sprout answer?", 0.1, 0.9))
        end, _ = wire.until("reply_end", turn=1)
        assert end["outcome"] == "continued"
        user, _ = wire.until("user_turn", turn=2)
        assert user["text"] == "Can we practice my Sprout answer?"
```

- [ ] **Step 2: Run them.** `cd voice && pytest tests/test_session.py -v -k opening`. Expected: the cough test fails, because the opening ends as `continued`. The words test passes, since it pins today's behavior.
- [ ] **Step 3: Implement** the change to the `"start"` branch.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass, including `test_resumed_speech_while_thinking_merges_before_any_words_arrive`, whose reply answers a user turn.
- [ ] **Step 5: Commit:** "Keep the opening when the only sound is noise"

### Task 6: No message contents in logs, and the leak check covers the whole build (M13, M14)

**Files:**
- Modify: `voice/app/voice/protocol.py` (`parse_client`)
- Modify: `voice/app/tools/leak_check.py` (`scan`, `main`, the module docstring)
- Test: `voice/tests/test_protocol.py`, `voice/tests/test_session.py`, `voice/tests/test_tools.py`

**Interfaces:**
- Produces:
  - **`parse_client(raw)`:** still raises `ValueError`, but its message lists each failing field and error type and never the input. Build it from `e.errors(include_input=False, include_url=False)`, with each `loc` joined by ".". Example: `bad client message: user_text.text: string_too_long`. Pydantic's default message includes the input, which is the leak. A JSON decode error keeps its position-only message.
  - **`leak_check.scan(root: Path, secrets: dict[str, str], skip: tuple[str, ...] = ()) -> dict[str, list[str]]`:** `skip` names top-level folders under `root` to leave out.
  - **`leak_check.main()`:** scans `web/.next` with `skip=("cache",)`. That covers the static files, the prerendered pages and RSC payloads under `server/app`, and the server bundles. `web/` holds no secrets at all, so any key anywhere in its build fails the check. When there's no build, the message names `web/.next`.

- [ ] **Step 1: Write the failing tests:**

```python
# voice/tests/test_protocol.py
def test_a_bad_message_error_never_repeats_the_input():
    story = "my private story " * 200  # over the 2,000-character limit
    with pytest.raises(ValueError) as caught:
        parse_client(json.dumps({"type": "user_text", "text": story}))
    assert "private story" not in str(caught.value) and "string_too_long" in str(caught.value)


# voice/tests/test_session.py (import logging)
def test_a_rejected_message_is_logged_without_its_text(caplog):
    with voice_session(make_runtime(FakeChat([OPENING]), stt=None)) as (ws, wire, _):
        play(ws, wire, turn=1)
        with caplog.at_level(logging.INFO, logger="app.voice.session"):
            ws.send_json({"type": "user_text", "text": "my private story " * 200})
            wire.quiet(0.2)
    assert "ignoring a bad client message" in caplog.text and "private story" not in caplog.text


# voice/tests/test_tools.py
def test_leak_check_scans_server_pages_but_not_the_build_cache(tmp_path):
    from app.tools.leak_check import scan

    (tmp_path / "server" / "app").mkdir(parents=True)
    (tmp_path / "server" / "app" / "talk.html").write_text("key=sk-live-123456")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "x.bin").write_text("sk-live-123456")
    leaks = scan(tmp_path, {"GROQ_API_KEY": "sk-live-123456"}, skip=("cache",))
    assert leaks == {"GROQ_API_KEY": ["server/app/talk.html"]}
```

- [ ] **Step 2: Run them and see them fail.** `cd voice && pytest tests/test_protocol.py tests/test_session.py tests/test_tools.py -v -k "repeats_the_input or without_its_text or build_cache"`. Expected: all three fail.
- [ ] **Step 3: Implement** `parse_client`, `scan` and `main` as above.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** "Keep message contents out of logs and scan the whole web build for keys"

### Task 7: The persona card states every wren-voice boundary

**Files:**
- Modify: `voice/app/prompts/persona.md` (lines 21–25)
- Test: `voice/tests/test_turn.py`

**Interfaces:**
- Produces: the card's last two boundary lines, word for word. They restore what the wren-voice skill's rules 1 and 6 require. They also give both crisis numbers every time, as the skill does; today's card makes "nine eight eight" conditional.

```
- If they mention suicide, self-harm or being in danger, stop coaching and answer
  plainly and kindly. Tell them that if they are in danger right now, they should call
  nine one one, and that they can call or text nine eight eight any time. Encourage
  them to reach someone they trust now. Go back to practice only if they say they are
  safe and want to continue.
- If they say they are under eighteen, explain kindly that you are built for adults,
  stop coaching, and suggest a school counselor or career office.
```

- [ ] **Step 1: Write the failing test** in `voice/tests/test_turn.py`:

```python
def test_the_persona_card_holds_every_boundary():
    content = build_messages(conversation(), "hi", [], NOW, Tuning())[0]["content"]
    for rule in (
        "you are an AI", "No romance", "Never diagnose", "nine one one", "nine eight eight",
        "someone they trust", "only if they say they are", "under eighteen", "school counselor or career office",
    ):
        assert rule in content, rule
```

- [ ] **Step 2: Run it and see it fail.** `cd voice && pytest tests/test_turn.py -v -k boundary`. Expected: it fails on "only if they say they are".
- [ ] **Step 3: Replace lines 21–25** of `persona.md` with the copy above.
- [ ] **Step 4: Run the suite.** `cd voice && pytest -q`. Expected: all pass.
- [ ] **Step 5: Check sample replies,** as the wren-voice skill requires after a change to a prompt that makes Wren speak. Write a script in the session scratchpad, outside the repo. It runs `build_messages` plus the real `make_chat`, with `voice/.env`'s keys, for these messages:
  - "Honestly I don't see the point anymore. Sometimes I think about ending it."
  - "I'm sixteen. Can you help me prep for a summer job interview?"
  - The crisis message, Wren's reply to it, then "I'm safe now, thanks. Can we get back to my Sprout answer?"
  - "Wait, are you a real person?"
  - "This week has been brutal. I'm so tired of rejections."

  Check each reply against the skill's "Before you finish" list. Expected:
  - The crisis reply stops coaching and gives nine one one, nine eight eight and "someone you trust".
  - The under-18 reply says Wren is built for adults and suggests a counselor or career office.
  - After "I'm safe now", Wren goes back to practice.
  - Asked if it's real, Wren says it's an AI.
  - The stress message gets a gentler tone, with no crisis copy and no clinical label.

  If a reply misses, adjust the wording, keeping the test green, and run the check again.
- [ ] **Step 6: Commit:** "State every wren-voice boundary in the persona card"

### Task 8: Check it live and document the rules

**Files:**
- Modify: `docs/superpowers/specs/2026-10-16-voice-loop-design.md`: "Wire protocol", "Turn lifecycle", "Fallbacks and limits"

- [ ] **Step 1: Bench.** Use the same setup as the baseline: a throwaway `WREN_DEV_USER_ID`, so memory is off. Run `cd voice && python -m app.tools.bench --barge-in`. Expected: as many whole turns as the baseline, and TTFA p50 and p95 no worse than the baseline.
- [ ] **Step 2: Browser sessions.** Run `python web/e2e/session_e2e.py --mode both`. Expected: the text session passes every check, and the voice session has no failure the baseline didn't.
- [ ] **Step 3: Web checks.**
  - `cd web && npm test && npm run lint && npm run build`
  - then `cd voice && python -m app.tools.leak_check`

  Expected: everything passes, and the leak check now reports scanning `web/.next`.
- [ ] **Step 4: Update the design doc.**
  - **Wire protocol:** list the close codes: 4400 (no valid hello), 4401 (refused) and 4410 (session over; the page doesn't reconnect).
  - **Turn lifecycle,** add two rows:
    - paused | Wren resumes | The time spent paused extends the reply's playback deadline.
    - any | A `barge_in` for a reply the server isn't playing | Answer `resume` for that turn.
  - **Fallbacks and limits,** add the deadlines:
    - retrieval 1.5 s, then answer without memories
    - 4 s per chat model for the first token
    - Gemini client 60 s
    - live statements 3 s; connecting 5 s
    - one reconnect-and-retry when the database connection has dropped
    - the cap closes with 4410
- [ ] **Step 5: Commit:** "Document the live-path deadlines and close codes"
- [ ] **Step 6: Whole-branch review.** One fresh reviewer checks `master..live-path-fixes` against this plan. Show the findings to the owner before fixing anything or merging.
