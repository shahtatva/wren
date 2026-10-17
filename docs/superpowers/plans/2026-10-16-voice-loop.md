# Voice Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hands-free voice conversation with Wren in desktop Chrome. Mic audio goes over a WebSocket to `voice/`, through Nova-3 speech-to-text, `/turn` and Aura-2 text-to-speech, and plays back in the browser. The user can barge in. Three timestamps are logged on every voice turn: the end of the user's speech, the first LLM token and the first audio byte. Time to first audio shows in a debug panel.

**Architecture:**
- `run_turn` is one async generator of speakable sentences. Voice, typed text, `POST /turn` and evals all share it.
- A per-socket `VoiceSession` orchestrates a turn state machine. The Deepgram adapters and the reply task sit behind small protocols, so tests swap in fakes.
- The browser captures 16 kHz PCM in an AudioWorklet and runs a small voice-activity detector (VAD) on the main thread. It plays tagged 24 kHz PCM through a second worklet that can pause, flush and report the played position.

**Tech stack:**
- Python 3.14: FastAPI, `websockets` (already installed), `groq` `AsyncGroq`, google-genai (async), psycopg 3.
- Next.js 16 with React 19 and Tailwind 4. No new npm packages. Web tests use `node --test`, which strips TypeScript types on Node 24.

**Spec:** [docs/superpowers/specs/2026-10-16-voice-loop-design.md](../specs/2026-10-16-voice-loop-design.md) · [docs/spec.md](../../spec.md) feature 1 · [CLAUDE.md](../../../CLAUDE.md)

**Status at plan time (2026-10-16):**
- The Supabase project is empty: no `public` tables, no `hybrid_search()`, no `auth.users` rows. The live path must therefore run with memory off and say so.
- Pushing the migrations and creating user zero change the user's project, so they wait for the user.
- Every key in `voice/.env` is set.

## Global Constraints

- **Keys:** live only in `voice/.env`. The browser talks only to `voice/`. `web/` env holds `NEXT_PUBLIC_VOICE_SERVER_URL` only.
- **Free tiers only.** No new accounts. New Python packages: none, but pin `websockets==16.1.1`. New npm packages: none.
- **One pipeline.** Text mode uses the same `run_turn` as voice mode.
- **Retrieval:** unchanged. `/turn` calls `retrieve()` only with the committed user text.
- **TTFA:** logged on every voice turn, measured from the end of speech to the first audio byte sent. The log line carries ISO timestamps for `speech_end_at`, `first_token_at` and `first_audio_at`. It logs at WARNING above 1,500 ms.
- **Live path:**
  - Persistence and logging run as background tasks.
  - Sync memory code runs in `asyncio.to_thread`.
- **RLS:** live DB access runs as `authenticated` through `store.as_user()`.
- **Model IDs:** pinned in env vars:
  - `GROQ_CHAT_MODEL=qwen/qwen3.8-27b` (measured 2026-10-16: median time to first token 186 ms, best persona fit)
  - `DEEPGRAM_STT_MODEL=nova-3`
  - `DEEPGRAM_TTS_MODEL=aura-2-vesta-en`, a placeholder until the user picks a voice in M0
- **Copy:** prompts and user-facing copy follow the wren-voice skill. UI follows frontend-design.
- **Audio formats:**
  - Up: `linear16`, 16 kHz, mono, 20 ms frames.
  - Down: `linear16`, 24 kHz, mono, with a 4-byte header (turn, seg as big-endian uint16).
- **Tuning defaults** (`Tuning` dataclass):

  | Setting | Default |
  | --- | --- |
  | `endpointing_ms` | 300 |
  | `utterance_end_ms` | 1000 |
  | `hold_checkin_s` | 0.0 |
  | `hold_mock_s` | 0.7 |
  | `confirm_window_s` | 1.2 |
  | `idle_s` | 120 |
  | `session_cap_s` | 2700 |
  | `hello_timeout_s` | 5 |
  | `history_turns` | 6 |
  | `memories_k` | 8 |
  | `pipeline_days` | 7 |
  | `max_reply_tokens` | 400 |

## Review Focus

1. **Echo becoming a user turn.** A final transcript of Wren's own words heard during playback must not commit a user turn. Test: `test_session.py::test_echo_during_playback_is_never_committed`.
2. **Disconnect mid-reply.** The reply task is cancelled, both Deepgram sockets close, and the heard part is saved. Test: `test_session.py::test_disconnect_mid_reply_cancels_and_saves_heard_text`.
3. **Groq 429 mid-session.** Gemini answers and the fallback is logged. Test: `test_chat.py::test_falls_back_to_gemini_before_first_token`.
4. **A two-minute mock answer** with pauses. Finals keep accumulating and nothing is dropped. Test: `test_endpoint.py::test_long_answer_with_pauses_commits_everything_once`.
5. **Typing while Wren talks.** Typing interrupts the reply and commits the typed text. Test: `test_session.py::test_typed_text_interrupts_and_commits`.

---

### Task 1: Settings, tuning, clock, audio timeline

**Files:** Modify `voice/app/config.py`, `voice/.env.example`, `voice/requirements.txt`. Create `voice/app/voice/__init__.py`, `voice/app/voice/tuning.py`, `voice/app/voice/clock.py`, `voice/app/voice/timeline.py`. Tests in `voice/tests/test_voice_config.py` and `voice/tests/test_timeline.py`.

**Interfaces:**
- **Produces:**
  - `VoiceSettings(deepgram_api_key, deepgram_stt_model, deepgram_tts_model, groq_chat_model, allowed_origins: tuple[str, ...], dev_user_id: UUID | None)` and `load_voice_settings()`. These are separate from `Settings`, so evals don't need Deepgram.
  - `Tuning`, a frozen dataclass with the defaults above, and `tuning.hold_s(mode) -> float`.
  - `clock.now() -> float` (monotonic) and `clock.iso(mono: float | None) -> str | None` (UTC, ms, `Z`).
  - `AudioTimeline(bytes_per_second=32000)` with `.add(nbytes, arrived)`, `.arrival_of(audio_s) -> float | None` and `.reset()`. It keeps the last 120 s.

- [ ] Tests:
  - `test_voice_settings_parse_origins_and_optional_dev_user`
  - `test_missing_deepgram_key_names_the_variable`
  - `test_arrival_of_maps_audio_time_to_the_frame_that_carried_it` (frames of 640 bytes = 20 ms; `arrival_of(0.05)` is the third frame's arrival minus 0.01)
  - `test_arrival_of_before_first_frame_is_none`
  - `test_reset_starts_a_new_stream_at_zero`
- [ ] Implement.
- [ ] Run until green.

### Task 2: Wire protocol and turn metrics

**Files:** Create `voice/app/voice/protocol.py`, `voice/app/voice/metrics.py`. Tests in `voice/tests/test_protocol.py` and `voice/tests/test_metrics.py`.

**Interfaces:**
- **Client messages:** pydantic models `Hello{mode="checkin"|"mock", tz="UTC", session_id=None, voice_out=True, token=None}`, `UserText{text 1..2000}`, `Mic{on}`, `Playback{turn, seg, event: "start"|"end"}`, `BargeIn{turn, seg, played_ms, onset_ms}` and `End`.
- **Parsing:** `parse_client(raw: str) -> ClientMessage` raises `ValueError`.
- **Audio frames:** `pack_audio(turn, seg, pcm) -> bytes` and `unpack_audio(frame) -> (turn, seg, pcm)`. Turn numbers wrap at 65536.
- **Timing:** `TurnTiming` is a mutable dataclass. Its fields are monotonic floats or `None`:
  - `speech_end`, `committed`, `retrieval_start`, `retrieval_end`, `llm_start`, `llm_first_token`, `first_segment`, `tts_first_byte`, `first_audio_sent`

  plus:
  - `turn`, `mode`, `input` ("voice"|"text"|"opening"), `memories`, `retrieval_fallback`, `model`, `chat_fallback`, `context_tokens`, `outcome`, `barge_in_ms`
- **Derived and output:**
  - `.ttfa_ms()` and `.record() -> dict`. The record holds ms durations, including `ttfa_ms`, `endpoint_ms`, `retrieval_ms`, `llm_ttft_ms`, `tts_ttfb_ms` and `turn_to_audio_ms`, plus `*_at` ISO stamps. It's sent to the client as `{"type":"metrics", "turn", ...record}` and stored in the transcript.
  - `log_turn(session_id, timing)` writes one line.

- [ ] Tests:
  - `test_hello_defaults`
  - `test_unknown_type_is_rejected`
  - `test_audio_header_round_trips_and_wraps_turn_numbers`
  - `test_ttfa_is_first_audio_minus_speech_end`
  - `test_record_has_iso_stamps_for_speech_end_first_token_first_audio`
  - `test_log_line_warns_over_1500ms` (caplog: `ttfa_ms=`, `speech_end_at=`, `first_token_at=`, `first_audio_at=`)
- [ ] Implement.
- [ ] Run until green.

### Task 3: Speakable segments

**Files:** Create `voice/app/voice/segment.py`. Tests in `voice/tests/test_segment.py`.

**Interfaces:**
- `clean(text) -> str` strips markdown marks, emoji, URLs and list markers, maps unicode hyphens and dashes to plain ones, turns spaced dashes into commas, and collapses whitespace.
- `Segmenter(first_min_words=5, max_words=30)` has `.feed(delta) -> list[str]` and `.flush() -> list[str]`.
- `async segments(deltas) -> AsyncIterator[str]`.

**Split rule:**
- Split after `.`, `!` or `?` (plus closing quotes) followed by whitespace, unless the word before is a known abbreviation (Mr, Mrs, Ms, Dr, St, vs, e.g, i.e) or the dot sits between digits.
- The first segment may also split at `, ; :` once it has 5 or more words.
- Any segment splits at the last space after `max_words`.

- [ ] Tests:
  - `test_splits_sentences_as_they_complete`
  - `test_first_segment_breaks_early_at_a_clause`
  - `test_does_not_split_abbreviations_or_decimals`
  - `test_flush_returns_the_tail`
  - `test_clean_strips_markdown_emoji_and_links`
  - `test_clean_maps_unicode_hyphens`
- [ ] Implement.
- [ ] Run until green.

### Task 4: End-of-turn policy

**Files:** Create `voice/app/voice/events.py` and `voice/app/voice/endpoint.py`. Tests in `voice/tests/test_endpoint.py`.

**Interfaces:**
- **Events:**
  - `SpeechStarted(at)`
  - `Transcript(text, is_final, speech_final, start, end)`
  - `UtteranceEnd(last_word_end)`
  - `TtsAudio(turn, seg, pcm)` and `TtsDone(turn, seg)`
- **Commit:** `Commit(text, speech_end)`, where `speech_end` is the audio seconds of the last word end.
- **Policy:** `EndOfTurn(hold_s)` with these methods:
  - `.on_transcript(t, now) -> Commit | None`
  - `.on_utterance_end(e, now) -> Commit | None`
  - `.poll(now) -> Commit | None`
  - `.caption() -> str`
  - `.has_words() -> bool`
  - `.clear()`
  - a settable `.hold_s`

- [ ] Tests:
  - `test_commits_on_speech_final_with_no_hold`
  - `test_hold_waits_and_new_words_cancel_it`
  - `test_utterance_end_commits_when_speech_final_never_came`
  - `test_utterance_end_with_minus_one_is_ignored`
  - `test_interim_results_never_commit`
  - `test_long_answer_with_pauses_commits_everything_once`
- [ ] Implement.
- [ ] Run until green.

### Task 5: Barge-in rules

**Files:** Create `voice/app/voice/bargein.py`. Tests in `voice/tests/test_bargein.py`.

**Interfaces:**
- `words(text) -> list[str]` returns lowercase words with apostrophes kept.
- `is_interruption(text, wren_recent) -> bool`. Drop words found in `wren_recent`, which is echo. Then return true if 2 or more non-backchannel words remain, or 1 that is in `STOP_WORDS`.
- `BACKCHANNELS`: mm, mhm, mmhmm, hmm, uh, um, huh, uhhuh, yeah, yep, yes, okay, ok, right, sure, oh, ah, cool, nice, totally, gotcha.
- `STOP_WORDS`: wait, stop, hold, hang, sorry, actually, no, but.

- [ ] Tests:
  - `test_echo_of_wrens_words_is_not_an_interruption`
  - `test_backchannels_are_not_interruptions`
  - `test_two_real_words_interrupt`
  - `test_a_single_stop_word_interrupts`
  - `test_one_random_word_waits`
- [ ] Implement.
- [ ] Run until green.

### Task 6: Conversation types, prompts, `run_turn`

**Files:** Create:
- `voice/app/conversation.py`
- `voice/app/prompts/persona.md` (from the wren-voice template)
- `voice/app/prompts/conversation.md`
- `voice/app/turn.py`

Modify `voice/app/memory/types.py` to add `Profile(display_name, summary, target_roles, timezone)` and `PipelineEntry(company, role, stage, next_date, next_time, notes)`. Tests in `voice/tests/test_turn.py`.

**Interfaces:**
- **Conversation types:**
  - `Background(profile, pipeline: tuple[PipelineEntry, ...], last_session_at)`
  - `HistoryTurn(id, speaker, text, interrupted=False)`
  - `Conversation(session_id, user_id, mode, tz, background, turns, lock)` with `.next_turn_id()` (`<session_id>:tNN`) and `.add(turn)`
  - `Retrieved(hits, ms, fallback)`
  - `ConversationRegistry` with `.get/.put/.evict_idle(max_idle_s=1800)`
- **Stage types:**
  - `Retrieve = Callable[[str], Awaitable[Retrieved]]`
  - `Chat = Callable[[list[dict], TurnTiming], AsyncIterator[str]]`
- **Functions:**
  - `build_messages(convo, user_text | None, hits, now_utc) -> list[dict]`
  - `async run_turn(convo, user_text, *, retrieve, chat, timing) -> AsyncIterator[str]`. It sets the `retrieval_*`, `memories`, `llm_start`, `first_segment` and `context_tokens` fields (≈ chars/4).
  - If retrieval raises, the turn continues with no memories and `timing.retrieval_fallback="error:<Type>"`.
  - `user_text=None` is the opening turn: no retrieval, and the messages end with the opening instruction.
- **Prompt rendering:**
  - An interrupted Wren line renders as its text plus ` [cut off here. They didn't hear the rest]`.
  - Live signals use the conversation's tz: weekday, part of day, local time, and days since the last session. "First session together" when there's no last session.

- [ ] Tests:
  - `test_messages_hold_persona_signals_memories_and_last_six_turns`
  - `test_interrupted_wren_line_is_marked`
  - `test_opening_turn_skips_retrieval_and_asks_for_a_greeting`
  - `test_run_turn_yields_clean_segments_and_records_timing`
  - `test_retrieval_failure_keeps_the_turn_going`
- [ ] Write the persona card and the conversation prompt (wren-voice skill). After implementing, generate 5 sample replies with the live chat model and check them against the skill's list.
- [ ] Implement.
- [ ] Run until green.

### Task 7: Live chat with Gemini fallback

**Files:** Create `voice/app/chat.py`. Tests in `voice/tests/test_chat.py`.

**Interfaces:**
- `make_chat(settings, voice, *, groq=None, gemini=None) -> Chat`.
- Groq: `AsyncGroq(max_retries=0, timeout=10)`, with `stream=True`, `max_completion_tokens=400` and `reasoning_effort="none"` for Qwen (`"low"` plus `include_reasoning=False` for gpt-oss).
- It sets `timing.llm_first_token` on the first non-empty delta and `timing.model`.
- **Fallback:** any exception before the first token switches to Gemini Flash (`client.aio.models.generate_content_stream`). It sets `timing.chat_fallback="<Type>"`. After the first token, errors propagate.
- **Cancellation** closes the stream.

- [ ] Tests (fake clients):
  - `test_streams_groq_deltas_and_stamps_first_token`
  - `test_falls_back_to_gemini_before_first_token`
  - `test_error_after_first_token_propagates`
  - `test_cancel_closes_the_groq_stream`
- [ ] Implement.
- [ ] Run until green.

### Task 8: Memory for the live path

**Files:** Modify `voice/app/memory/store.py` and add `voice/app/memory/live.py`. Tests in `voice/tests/test_memory_live.py` (fakes) and `voice/tests/test_store_db.py` (skipped unless `WREN_DB_TESTS=1`).

**Interfaces:**
- **New store functions:**
  - `as_user(conn, user_id)` is a context manager: transaction, `set local role authenticated`, and `request.jwt.claims`.
  - `schema_ready(conn) -> bool` and `user_exists(conn, user_id) -> bool`
  - `start_session(conn, user_id, mode, started_at) -> UUID`
  - `append_turns(conn, session_id, turns: list[dict])`, using jsonb `||`
  - `end_session(conn, session_id, ended_at)`
  - `load_transcript(conn, session_id) -> (mode, list[dict]) | None`
  - `last_session_started(conn, user_id, before) -> datetime | None`
  - `load_profile(conn, user_id) -> Profile | None`
  - `upcoming_pipeline(conn, user_id, start, end) -> list[PipelineEntry]`
- **Memory protocol** (`Memory`):
  - `status: str`
  - `async open_session(user_id, mode, resume_id, now) -> (session_id, Background, list[HistoryTurn])`
  - `async retrieve(user_id, text) -> Retrieved`
  - `async append(session_id, user_id, turns: list[dict])`
  - `async close_session(session_id, user_id, now)`
- **Implementations:**
  - `NoMemory(reason)` is in-memory only.
  - `PostgresMemory(settings)` keeps one connection per process, reconnects once on `OperationalError`, and runs everything in `to_thread`.
  - `async connect_memory(settings, user_id) -> Memory` returns `NoMemory("no schema")` or `NoMemory("no such user")` when the DB isn't ready.

- [ ] Tests:
  - `test_no_memory_retrieves_nothing_and_keeps_session_ids_unique`
  - `test_connect_memory_reports_missing_schema` (fake check)
  - DB tests: `test_session_round_trip_as_user`, `test_rls_hides_other_users_sessions`, `test_live_search_matches_owner_search`
- [ ] Implement.
- [ ] Run until green. DB tests are skipped.

### Task 9: Deepgram adapters

**Files:** Create `voice/app/voice/stt.py` and `voice/app/voice/tts.py`. Tests in `voice/tests/test_deepgram.py`.

**Interfaces:**
- **STT:**
  - `nova_url(model, tuning) -> str`, with model, encoding, sample_rate, channels, interim_results, endpointing, utterance_end_ms, vad_events, smart_format and language=en.
  - `parse_stt(raw) -> SttEvent | None`. A transcript's `end` is its last word's `end`, falling back to start+duration.
- **TTS:**
  - `aura_url(model) -> str`, with linear16 at 24000.
  - `TtsRouter` with `.spoke(turn, seg)`, `.audio(pcm) -> TtsAudio | None`, `.flushed() -> TtsDone | None`, `.cleared()`, and `.clear()` to drop audio until Cleared.
- **Protocols:**
  - `Stt` with `.send(pcm)`, `.keep_alive()`, `.events()` and `.close()`
  - `Tts` with `.speak(turn, seg, text)`, `.clear()`, `.events()` and `.close()`
- **Connectors:** `connect_nova(key, model, tuning)` and `connect_aura(key, model)` over `websockets` with an `Authorization: Token` header.

- [ ] Tests:
  - `test_nova_url_has_endpointing_and_vad_events`
  - `test_parse_results_uses_last_word_end`
  - `test_parse_speech_started_and_utterance_end`
  - `test_router_tags_audio_by_flush_order`
  - `test_router_drops_audio_between_clear_and_cleared`
- [ ] Implement.
- [ ] Run until green. Live check comes in Task 11.

### Task 10: Voice session, routes and identity

**Files:**
- Create `voice/app/voice/identity.py`, `voice/app/voice/session.py`, `voice/app/runtime.py` and `voice/app/routes.py`.
- Modify `voice/app/main.py` (CORS for `ALLOWED_ORIGINS`, include routes).
- Tests in `voice/tests/test_identity.py`, `voice/tests/test_session.py` and `voice/tests/test_turn_http.py`.

**Interfaces:**
- **Identity:** `resolve_identity(token, client_host, dev_user_id) -> UUID | None` accepts dev identity only from 127.0.0.1, ::1 or the testclient.
- **Runtime and session:**
  - `Runtime(voice, settings, tuning, chat, registry, connect_stt, connect_tts, memory_for)`
  - `get_runtime(app) -> Runtime` is lazy. Tests set `app.state.runtime`.
  - `VoiceSession(ws, runtime, user_id, hello).run()`
- **WebSocket `/ws`:**
  - Origin check, then accept. A hello must arrive within 5 s, then identity is resolved.
  - Close codes: 4401 (no identity) and 1008 (bad origin).
- **`POST /turn`:**
  - Body `{text, session_id?, mode?, tz?}`.
  - Response NDJSON: `{"type":"session",...}`, `{"type":"segment","text"}`, `{"type":"end"}`.
- **Server messages:** `ready{session_id, voice, memory, model}`, `stt`, `user_turn`, `segment`, binary audio, `segment_end`, `reply_end`, `interrupt`, `resume`, `voice{available, reason}`, `metrics`, `error`.

- [ ] Tests (fake STT, TTS, chat and memory; real time with short tuning):
  - `test_bad_origin_and_missing_hello_are_rejected`
  - `test_opening_then_voice_turn_streams_segments_audio_and_metrics`
  - `test_metrics_carry_speech_end_first_token_first_audio`
  - `test_barge_in_confirmed_cancels_and_trims_history`
  - `test_backchannel_during_pause_resumes`
  - `test_echo_during_playback_is_never_committed`
  - `test_speech_while_thinking_merges_into_one_user_turn`
  - `test_typed_text_interrupts_and_commits`
  - `test_stt_failure_switches_to_text_and_text_still_works`
  - `test_disconnect_mid_reply_cancels_and_saves_heard_text`
  - `test_post_turn_streams_ndjson_through_run_turn`
  - `test_reconnect_with_session_id_resumes_history`
- [ ] Implement.
- [ ] Run until green.

### Task 11: Tools and live verification

**Files:** Create:
- `voice/app/tools/__init__.py`
- `voice/app/tools/bench.py`, which synthesizes prompts with Aura-2 REST at 16 kHz, streams them into `/ws` in real time with live-mic silence, and reports TTFA p50/p95 and barge-in time
- `voice/app/tools/ttfa_report.py`
- `voice/app/tools/voices.py`, which writes audition WAVs
- `voice/app/tools/echo_spike.py`, which makes the clip and counts leaked words in the recordings

- [ ] Run the server locally with memory off and `WREN_DEV_USER_ID` set in the process env.
- [ ] Run `python -m app.tools.bench`. Expected: replies with TTFA logged, `outcome=complete`, and a barge-in run that ends `interrupted`.

### Task 12: Web audio core

**Files:** Create:
- `web/lib/voice/protocol.ts`
- `web/lib/voice/vad.ts`
- `web/public/voice/capture-worklet.js`, which exports `Resampler` and `Framer`
- `web/public/voice/playback-worklet.js`, which exports `PlaybackQueue`
- tests `web/tests/*.test.ts|mjs`

Modify `web/package.json` (`"test": "node --test tests/"`) and `web/tsconfig.json` (exclude `tests`).

**Interfaces:**
- **Protocol (`protocol.ts`):** `unpackAudio(buf) -> {turn, seg, pcm: Int16Array}` and `ServerMessage` types.
- **VAD (`vad.ts`):** `Vad({startMs=120, speakingBoost=2.5})` with `.push(rms, nowMs, wrenSpeaking) -> "onset" | null` and `.onsetAt`. It keeps an adaptive noise floor.
- **`PlaybackQueue(sampleRate)`:**
  - `.push(turn, seg, samples)`, `.complete(turn, seg)`, `.render(out) -> events[]`, `.pause()`, `.resume()`, `.flush()` and `.position() -> {turn, seg, playedMs}`
  - Events: `segment-start`, `segment-end` and `drained`.
  - Pause and resume ramp over 10 ms.

- [ ] Tests:
  - `vad onset needs 120ms of voice above the adaptive floor`
  - `vad needs more energy while Wren is speaking`
  - `queue emits start/end per segment and reports played ms`
  - `flush drops everything and pause outputs silence`
  - `resampler 48k→16k keeps duration`
  - `unpackAudio reads the header`
- [ ] Implement.
- [ ] Run `npm test`, `npm run lint`.

### Task 13: Browser client, talk page, debug panel

**Files:**
- Create `web/lib/voice/mic.ts`, `web/lib/voice/player.ts` and `web/lib/voice/client.ts`. The client handles the WebSocket, hello, reconnect with `session_id`, the `POST /turn` fallback and barge-in wiring.
- Create `web/app/talk/page.tsx` (metadata), `web/app/talk/talk.tsx` (client) and `web/app/talk/debug-panel.tsx`.
- Modify `web/app/page.tsx` to add a link to `/talk`.

**Behavior:**
- The first tap creates the AudioContexts, asks for the mic and connects.
- Audio never goes through React state.
- **Debug panel:**
  - Shown in development or with `?debug=1`.
  - Shows the last turn's TTFA in large type and the session median.
  - Shows a breakdown: endpoint, retrieval, LLM time to first token, TTS time to first byte.
  - Shows the three timestamps, plus the memory status and model.

- [ ] `npm run lint` and `npm run build` pass.
- [ ] Key-leak scan: no value from `voice/.env` appears in `web/.next/static`.

### Task 14: Browser end-to-end

Use the webapp-testing skill with Playwright on the system Chrome. Feed a fake mic from a WAV made by the bench synthesizer, using `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture`.

- [ ] Script: `web/e2e/voice_e2e.py`. It checks that:
  - the talk page connects
  - a user turn is committed
  - the debug panel shows a TTFA number
  - audio frames arrive
  - a barge-in utterance during playback produces `barge_in` with `onset_ms` under 300 and an `interrupted` reply
- [ ] Run it. Record the results.

### Task 15: Docs

- [ ] Update `voice/.env.example` with the new settings.
- [ ] Update the design doc:
  - add the `metrics` message and the debug panel
  - record the chat model choice
  - note the memory-off mode
- [ ] Add the run commands to `CLAUDE.md` (bench, report, web tests).
