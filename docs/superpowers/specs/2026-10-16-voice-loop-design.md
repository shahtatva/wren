# Voice loop design

Draft, 2026-10-16 · For [spec.md](../../spec.md) feature 1 ("Voice with barge-in") and goal 2 ("Feels live") · Rules: [CLAUDE.md](../../../CLAUDE.md)

## Build notes (2026-10-17)

The loop is built. Implementation plan: [plans/2026-10-16-voice-loop.md](../plans/2026-10-16-voice-loop.md). Live measurements changed six things in this design, and the sections below are updated to match:

1. **Aura-2 runs over REST**, one streamed request per segment, not its WebSocket.
   - The WebSocket allows 20 `Flush` messages a minute, and Wren flushes once per sentence.
   - Warm REST first byte measured 94 ms, against 105 ms for the WebSocket. `warm()` runs at each commit to avoid a cold TLS handshake (about 180 ms).
2. **The end of speech comes from a silence detector in `voice/`, which then sends `Finalize`.**
   - Nova-3's own endpointing fired about 1.5 s after the last word at `endpointing=10/100`, and not at all within 3 s at `300/500`.
   - After `Finalize`, finals arrive in about 100 ms. `speech_final` and `UtteranceEnd` still count if they come first.
3. **Resumed speech while Wren is still thinking cancels and merges the reply** before any words arrive, because Nova-3's words lag the audio by about a second. A pause holds while the user is still talking.
4. **The chat model is `qwen/qwen3.8-27b`**: median first token 186 ms, with the best persona fit of the three Groq models tried.
   - When it's rate-limited, `openai/gpt-oss-120b` takes over, then Gemini Flash.
   - Gemini's free tier allows only 20 requests a day per model.
5. **A `metrics` message carries each turn's timings to a debug panel.**
   - It shows TTFA, the end-of-speech, first-token and first-audio timestamps, and the stage breakdown.
   - The server also logs them, and appends them to `voice/logs/turns.jsonl`.
6. **The browser pauses Wren on 100 ms of voice within 160 ms.** Requiring 120 ms unbroken took 299 ms after a short first syllable.

Measured with memory off, because the Supabase schema isn't pushed yet. Retrieval will add to these numbers.

| Measurement | Result |
| --- | --- |
| Bench, real Deepgram and Groq, 6 voice turns | TTFA p50 975 ms, p95 1,265 ms |
| Browser (Chrome, fake mic) | TTFA 1,025–1,627 ms (Groq first token 304–868 ms) |
| Browser, from the interruption's first sound to paused | 99 ms (target 300 ms) |
| Server, confirming a barge-in from words | about 950 ms (inaudible: playback is already paused) |

## Why

Wren is voice-first, and the spec's second goal is that it feels live: time to first audio (TTFA) p50 ≤ 1.0 s and p95 ≤ 1.5 s, logged every turn. The user must be able to cut in at any moment, as in a real mock interview. The pipeline is a cascade on purpose, so `/turn` can rebuild the context every turn. Every provider call happens in `voice/`, so the browser never holds an API key.

## Where things stand

| Piece | State on 2026-10-16 |
| --- | --- |
| `retrieve()`: rewrite → embed → `hybrid_search()`, with latency logging | Built (sync) |
| `hybrid_search()` | Built. `security invoker`, so RLS applies when it's called as `authenticated` |
| Extraction, schema with RLS, settings | Built |
| `DEEPGRAM_API_KEY` and `ALLOWED_ORIGINS` in `voice/.env.example` | Present, unused |
| **`/turn`**: context assembly, conversation LLM, reply streaming | **Not built.** `main.py` has only `/health` |
| Persona card in code | Not built. The template is in the wren-voice skill |
| Deepgram code, WebSocket, live sessions, auth, web UI | Not built |

The week-1 sprint keeps voice and `/turn` out of scope, and the spec builds voice after the memory gates. This design is for that later sprint.

## Goals

1. Hands-free conversation in desktop Chrome: the user talks, Wren answers out loud, and the user can interrupt at any time.
2. TTFA is logged for every voice turn with a per-stage breakdown, and reported as p50 and p95.
3. Barge-in: playback stops within 300 ms of the user starting to talk, the reply in flight is cancelled, and the history keeps only the words the user heard.
4. Text mode runs the same turn pipeline. It takes over when the mic or the speech providers fail, and over plain HTTP when the WebSocket can't connect.
5. No provider key reaches the browser, and `voice/` never becomes an open relay for those keys.

## Non-goals

- **The rest of the context builder:** the rolling summary and the context-inspection view. `/turn` v1 leaves a seam for them.
- **Extraction after live sessions.** Live transcripts are saved in the shape extraction already reads. Wiring that job is memory work.
- **Other features:** mock-interview mode logic, the proactive opener, pipeline entry by voice, and the "What Wren knows" page (spec features 2, 3, 5 and 6).
- **Boundary features:** the 18+ gate, crisis routing in code, and AI disclosure at session start (spec feature 7). `/turn` gets a guard step for crisis routing to slot into. All three must land before testers.
- **Deploying `voice/` and real user auth.** These belong to a separate plan, blocked on the spec's hosting question.
- **Also out:** phone and iOS polish, Flux, and speech-to-speech.
- **Any change to retrieval.** `/turn` calls `retrieve()` with the committed user text only, so no memory evals are triggered.

## Anti-goals

These would make the loop a failure even if it technically works.

- Wren stops itself because it hears its own voice, or stops for every "mm-hm" and cough.
- Wren cuts the user off mid-answer in a mock because they paused to think.
- The history records words the user never heard, so Wren refers back to things it never said aloud.
- Sync calls block the event loop, and audio stutters while retrieval runs.
- A deployed socket that anyone can open to spend the Deepgram credit or hear user zero's memories.

## Constraints

- **Free tiers, no new accounts.** Deepgram's $200 credit covers Nova-3 and Aura-2.
- **Groq limits.** Groq's limits page lists 8K tokens a minute and 200K a day per model for `gpt-oss` and Qwen, and the free tier may be lower. A turn's context will be about 2K tokens, so one model allows roughly 4 turns a minute and 100 a day. Check the org's limits page in M1.
- **Keys:** they live only in `voice/.env`. `web/` gets only `NEXT_PUBLIC_VOICE_SERVER_URL`.
- **One pipeline.** Text and voice share it, and tests depend on that.
- **Live path:** persistence and logging stay off it.
- **RLS stays on.** Live queries run as `authenticated`, following the access model in the schema migration.
- **Skills and docs:**
  - Prompts and copy follow the wren-voice skill.
  - UI follows frontend-design.
  - Check Next.js APIs in `web/node_modules/next/dist/docs/` before using them.
- **Dependencies:** no new Python package. Pin `websockets`, which google-genai already installs, because the code will import it. No new npm package.
- **Settings:** voice-only settings stay out of the eval harness's required set, so evals run without a Deepgram key.

## Architecture

```
 Browser (web/)                      voice/ (FastAPI)                         Providers
 mic ─▶ capture worklet ──PCM 16k──▶ /ws session ────────── audio ─────────▶ Nova-3 (v1/listen)
        (local VAD)                    │  ◀─────── Results, SpeechStarted, UtteranceEnd
 captions ◀──── stt, user_turn ────────┤
                                       ├─▶ end-of-turn policy
                                       ▼
                                   run_turn(text) ── retrieve() ──────────▶ Groq rewrite, Gemini embed, Postgres
                                       │          └─ chat stream ─────────▶ Groq chat model (Gemini fallback)
                                       ▼ sentences
 playback worklet ◀──PCM 24k──────── TTS adapter ── one request/segment ──▶ Aura-2 (REST v1/speak)
   │ played position
   └── barge_in, playback ──────────▶ barge-in controller: cancel turn, Clear TTS, trim history
 text box ── user_text, POST /turn ─▶ run_turn (same function)
```

One browser tab holds one WebSocket to `voice/`. Per session, `voice/` holds one Nova-3 connection, opened only after the hello passes. Aura-2 runs over a shared, warm HTTP connection, with one streamed request per segment (see Build notes). Sessions live in one process's memory, so `voice/` runs a single worker. Each session runs its readers in one task group, so a disconnect or failure cancels them all.

### Server (`voice/app/`)

| Module | Job |
| --- | --- |
| `turn.py` | `run_turn`, the turn pipeline. Voice, typed text, `POST /turn` and evals all share it |
| `prompts/persona.md` | The runtime persona card, from the wren-voice template |
| `voice/session.py` | One socket's orchestrator: the state machine, the turn task, history and persistence hooks |
| `voice/stt.py` | Nova-3 adapter: takes audio in and emits normalized events |
| `voice/vad.py` | Silence detector on the forwarded mic frames: its "end" triggers Nova-3's `Finalize` |
| `voice/endpoint.py` | End-of-turn policy. Pure logic with an injected clock |
| `voice/bargein.py` | Barge-in confirmation, with echo and backchannel guards. Pure logic |
| `voice/segment.py` | Splits the LLM stream into speakable segments |
| `voice/tts.py` | Aura-2 adapter: one streamed REST request per segment, on a warm connection |
| `voice/protocol.py` | Message models and the audio frame header |
| `voice/metrics.py` | Per-turn timings, the log line and the transcript record |
| `memory/store.py` (additions) | Session rows and turn appends, run as `authenticated` |

**`run_turn`** is an async generator that yields speakable segments: `run_turn(conversation, user_text, stages) -> AsyncIterator[str]`. With `user_text=None` it produces the opening line. Its stages come in as callables, like `retrieve()`'s, so the server, evals and tests each wire their own. Steps:

1. **Guard.** A no-op hook where crisis routing lands later.
2. **Retrieve.** The existing `retrieve()` runs in `asyncio.to_thread`, with the live stages: Groq rewrite, Gemini query embedding, and `hybrid_search_many` inside an `authenticated` transaction.
3. **Build messages.**
   - The persona card.
   - Live signals: mode, local time of day, and days since the last session.
   - Profile and the next 7 days of pipeline. These and the last-session date load once per session.
   - The top 8 memories.
   - The last 6 turns.
   - The user text.
4. **Stream the reply** from Groq (`AsyncGroq`, `stream=True`). On a 429 or an error before the first token, fall back to streaming Gemini Flash.
5. **Segment.** Yield each sentence as it completes. To start audio sooner, the first segment may break at a clause boundary after 5 or more words. As a safety net, strip markdown, emoji and links.

It records retrieval time, time to first token, first-segment time and context tokens for the metrics.

**Sync code in an async server.** The memory code is sync: it uses the sync Groq, Gemini and psycopg clients. `to_thread` keeps the event loop free, so audio keeps flowing. A cancelled turn can't stop a running thread, but its result is discarded, and the rewrite already has a 1 s stall guard.

**Conversation model.** Pick it by measurement in M1, from Groq's free models such as `openai/gpt-oss-120b` or `qwen/qwen3.8-27b`.
- Measure time to first token.
- Check 5 sample replies against the wren-voice list.
- Use a different model from the rewrite's `gpt-oss-20b`, so the two calls don't share one model's token limit.
- Store the choice in a new setting, `GROQ_CHAT_MODEL`.

### Client (`web/`)

| Piece | Job |
| --- | --- |
| Talk page (client component) | Shows state, captions and Wren's text. Has a mic toggle and a text box that's always available |
| `lib/voice/connection.ts` | WebSocket and protocol, reconnect with session resume, `POST /turn` fallback |
| Capture worklet | Converts the mic from the device rate to 16 kHz mono `linear16` in 20 ms frames, and runs a local voice-activity detector (VAD) |
| Playback worklet | A ring buffer for 24 kHz PCM. It pauses, resumes and flushes at once, and reports the played position per segment |

- Call `getUserMedia` with `echoCancellation`, `noiseSuppression` and `autoGainControl` on.
- Audio never goes through React state. Only coarse state changes do.
- The AudioContext starts or resumes on the user's tap.
- Copy follows the wren-voice skill, for example: "I lost the connection. Tap to reconnect, or keep typing."

## Wire protocol

Arrows: `→` is browser to `voice/`, and `←` is `voice/` to browser.

- **Upstream binary frames** are raw mic PCM.
- **Downstream binary frames** are TTS audio with a 4-byte header: the turn and segment numbers, each a big-endian uint16. The header lets the client drop audio from a cancelled turn.
- **Everything else** is JSON with a `type`.

| Dir | Message | When |
| --- | --- | --- |
| → | `hello {mode, tz, session_id?, voice_out}` | First message. If it doesn't arrive within 5 s, the socket closes. Carries the auth token once real auth exists |
| ← | `ready {session_id, voice, memory, model, resumed}` | The hello passed. `voice: false` if speech-to-text didn't connect. `memory` is "on" or "off: <why>" |
| → | binary PCM | Sent continuously while the mic is on, including while Wren talks |
| → | `user_text {text}` | A typed turn. Interrupts any reply in flight |
| → | `mic {on}` | Mute or unmute. While muted, the server sends Deepgram KeepAlives |
| ← | `stt {text, final}` | Live captions |
| ← | `user_turn {turn, text}` | The committed user turn |
| ← | `segment {turn, seg, text}` | Each reply sentence, sent before its audio |
| ← | binary audio | Tagged with (turn, seg) |
| ← | `segment_end {turn, seg, samples}` | TTS finished that segment |
| ← | `reply_end {turn, outcome}` | `complete`, `interrupted` or `failed` |
| → | `playback {turn, seg, event}` | `start` and `end` of each segment, as actually heard |
| → | `barge_in {turn, seg, played_ms, onset_ms}` | The local VAD heard the user and paused playback |
| ← | `interrupt {turn}` / `resume {turn}` | The barge-in was confirmed / was a false alarm |
| ← | `voice {available, reason}` | Speech-to-text dropped (`stt_closed`), was closed after 2 idle minutes (`idle`), or came back. The UI switches to text and back |
| ← | `metrics {turn, ttfa_ms, ..., speech_end_at, first_token_at, first_audio_at}` | The turn's timings: once with the first audio byte, and again with its outcome. Feeds the debug panel |
| → | `end {}` | The user ends the session |
| ← | `error {code, message}` | `message` is user-facing copy |

## Turn lifecycle

Each session is in one of four states:

- `listening`
- `thinking`: the turn task is running and no audio has been sent
- `speaking`: audio has been sent and hasn't finished
- `paused`: playback is paused by a suspected barge-in

| State | Event | Result |
| --- | --- | --- |
| listening | End of turn | Start the turn task → thinking |
| thinking | The user says new words | Cancel the task. Keep the committed text and merge it with what the user says next → listening |
| thinking | First audio sent | → speaking |
| speaking | `barge_in` from the client | → paused, and start the confirmation window |
| speaking | The server hears words the client missed | Confirm as below. This is the backup path, slower than 300 ms |
| paused | Confirmed: 2 or more words that aren't echo or a backchannel | Cancel the task, send `Clear` to Aura-2, send `interrupt`, trim Wren's turn to what played → listening |
| paused | Not confirmed within the window (start at 1.2 s) | Send `resume` → speaking |
| speaking | `playback end` for the last segment | Save Wren's full turn → listening |
| any | `user_text` | Interrupt as above, then commit the typed text |
| any | Socket closes | Cancel everything, save what was heard and end the session |

**End of turn with Nova-3.** Connect with these parameters:

- `model=nova-3`, `encoding=linear16`, `sample_rate=16000`, `channels=1`
- `interim_results=true`
- `endpointing` set explicitly, because Deepgram's default is 10 ms
- `utterance_end_ms=1000`, `vad_events=true`, `smart_format=true`

How the policy decides:

0. **Detect silence.** The energy detector in `voice/vad.py` watches the forwarded frames. After 300 ms of quiet it sends `Finalize`, and a final that comes back with `from_finalize` is a candidate end, like `speech_final`.
1. **Collect.** Append each `is_final` result to a buffer, and treat `speech_final` as a candidate end of turn, as Deepgram recommends.
2. **Hold.** Wait a mode-dependent extra time before committing: short in check-ins, long in mocks. Start at 0 ms and 700 ms, and tune in M2. New words during the hold cancel it.
3. **Backstop.** `UtteranceEnd` commits the turn when noise keeps `speech_final` from firing. Ignore it when `last_word_end` is -1.

The adapter emits the same events a Flux adapter would, so trying Flux later is an adapter swap.

**Barge-in: stop fast, then confirm.**

- **Pause locally.** The page runs an energy detector on the mic frames, with an adaptive noise floor, and raises its threshold while Wren is audible. After 100 ms of voice within 160 ms, it pauses playback on the spot (a 10 ms fade, to avoid a click) and sends `barge_in`. That keeps onset to silence under 300 ms without a server round trip.
- **Confirm with words.** The server confirms using words from Nova-3. A transcript that mostly repeats what Wren just said counts as echo. One made only of backchannels ("mm-hm", "yeah", "okay", "right") doesn't count either. Both guards apply to every transcript heard while Wren talks, so echo can never become a user turn.
- **Keep generating while paused.** Generation keeps running, so a false alarm resumes cleanly.
- **Cancel on confirm.**
  1. Cancel the turn task, which closes the Groq stream.
  2. Send `Clear` to Aura-2 and drop its audio until `Cleared` arrives.
  3. The client drops anything tagged with the old turn.

**What was heard.** Aura-2 returns no word timings, so the cut is an estimate.
- Wren's turn keeps every fully played segment.
- From the cut segment, it keeps the first `played_ms / segment_ms` share of the words, rounded down.
- The trimmed turn is saved with `interrupted: true`. The conversation prompt shows such lines as cut off, so Wren knows the user didn't hear the rest.
- If nothing played, Wren's turn isn't saved.

**Who speaks first.** Wren does. On `ready`, the server runs `run_turn` with no user text, which gives a short greeting through the same pipeline. The proactive opener and the session-start AI disclosure later replace its content.

## Latency budget

**TTFA** runs from the end of the user's speech to the first audio byte sent. The end of speech is Nova-3's last word end, mapped to server time through the audio frames' arrival times. The commit-based number, from turn committed to first audio byte, is logged too.

| Stage | Target p50 | Lever if it's slow |
| --- | --- | --- |
| End of speech → turn committed | about 300 ms in check-ins | `endpointing` and the hold time |
| Retrieval | under 300 ms | Already logged against its budget. Changes go through evals, and E2 covers rewrite cost |
| Context assembly | under 20 ms | Session-level data loads once |
| LLM time to first token | about 200 ms | Model choice, measured in M1 |
| First segment complete | about 50 ms | Early clause break |
| Aura-2 time to first byte | about 200 ms | A warm connection, opened at session start |
| **Total** | **about 1,050 ms** | |

The total is right at the 1.0 s p50 target, so the per-stage log matters more than any one optimization. Mock turns hold longer on purpose and are reported separately. The spec already says targets get revised once, after the first measurements.

## Fallbacks and limits

- **The mic fails** (permission denied, no device, track ended): the UI switches to text on the same socket.
- **Deepgram fails or the credit runs out:** the server sends `voice {available: false}`, keeps serving text, and retries in the background with backoff.
- **The WebSocket drops:**
  - The client reconnects with backoff and sends `session_id` in its hello.
  - The server rebuilds the conversation from the saved transcript.
  - While the socket is down, typed turns go to `POST /turn`, which streams text and appends to the same session.
  - A per-session lock keeps the socket and `POST /turn` from running turns at once.
- **Groq returns a 429 or an error:** Gemini Flash streams the reply. `retrieve()` already falls back to the raw message when the rewrite fails.
- **Credit guard:**
  - After 2 minutes with no speech, close the Nova-3 connection and show a paused state.
  - Cap sessions at 45 minutes.
  - While muted, send a KeepAlive every 3 to 5 s instead of audio, since Deepgram closes a connection after 10 s with neither.

## Keys and access

- **Provider keys** are read from `voice/.env` and sent only in server-side `Authorization` headers. No protocol message carries one.
- **Origin check.** The socket and `POST /turn` check `Origin` against `ALLOWED_ORIGINS`. That blocks other sites, but it isn't authentication.
- **Local dev identity.** `WREN_DEV_USER_ID`, user zero's row in `auth.users`, is accepted without a token, but only from loopback client addresses (`127.0.0.1` or `::1`). It applies to the socket and to `POST /turn` alike. A proxy on the same host would also look like loopback, so the deploy plan must leave this setting unset.
- **Deepgram connections** open only after the hello passes.
- **Before any deploy** (a separate plan): real sign-in, routed through `voice/` because the browser never talks to Supabase. The likely shape is Supabase email codes, with the hello carrying a JWT checked against the project's signing keys. Live queries already run as `authenticated` with the user's id, so only the identity source changes.
- **Check.** After `npm run build`, scan `web/.next/static` for each key value in `voice/.env`. The scan prints pass or fail, never the keys.

## Persistence and metrics

- **Session row.** A `sessions` row is created on `ready`.
- **Turns.** Each finished turn is appended to `transcript` with:
  - `{id, speaker, text}`, the shape extraction reads
  - `interrupted`
  - `metrics`, on Wren's turns

  Turn ids are `<session id>:tNN`. There's no schema change.
- **Off the live path.** Appends run as background tasks after the turn, never ahead of audio. A failure is logged and retried once.
- **Log line.** One line per turn, in the style of `retrieve()`'s, logged at WARNING when TTFA passes 1,500 ms:

  ```
  turn session= turn= mode= input= outcome= ttfa_ms= commit_ms= retrieval_ms= llm_ttft_ms= first_segment_ms= tts_ttfb_ms= context_tokens= barge_in_ms=
  ```
- **Report.** A command reads saved sessions and prints TTFA p50 and p95 by mode.

## Testing

**Unit tests** need no network. They cover:
- the end-of-turn policy, with a fake clock
- barge-in confirmation: echo, backchannel and timeout
- the segmenter and the heard-text cut
- the frame header and the state transitions

**Socket tests** use `TestClient.websocket_connect` with fake STT, TTS and LLM adapters. They check that:
- a voice turn completes
- a typed turn produces the same `run_turn` call
- barge-in while speaking trims the history
- speech while thinking merges into one user turn
- a backchannel resumes playback
- echo is ignored
- a provider failure switches to text
- a late or missing hello, and a foreign origin, are rejected
- a reconnect resumes the session

**Retrieval parity:** under `authenticated`, the live search returns the same hits as the owner-role search for the same user. This backs the claim that retrieval is unchanged.

**Live bench** (manual, with real keys): a script plays WAV prompts into the local socket in real time and records TTFA and barge-in timing. It gives reproducible numbers for the write-up.

**Browser test** (webapp-testing skill): Chrome with a fake mic fed from a WAV. It checks that:
- a reply plays
- a second voice mid-reply pauses playback within 300 ms of its onset
- the saved turn is trimmed

## Milestones

Each milestone ends with something that runs.

| # | Milestone | Done when | Est. |
| --- | --- | --- | --- |
| M0 | **Echo spike** (throwaway) and **voice pick** | We know which playback route keeps Wren's voice out of the mic on the real setup, and Wren's Aura-2 voice is picked | 1.5 h |
| M1 | **`/turn` v1, text only** | `POST /turn` streams Wren's reply from real memory. Sessions persist. The chat model is picked by measurement. User zero's `auth.users` row exists | 5 h |
| M2 | **Ears** | Talking in the browser gives captions and committed turns. Replies arrive as text | 4.5 h |
| M3 | **Voice** | Wren answers out loud. TTFA is logged and saved every turn | 3 h |
| M4 | **Barge-in** | Interrupting stops playback, cancels the reply and trims the history. "Mm-hm" and echo don't interrupt | 3 h |
| M5 | **Fallbacks and limits** | Mic, provider and socket failures all leave a working text mode. The idle and session caps hold | 2 h |
| M6 | **Numbers** | The bench and the browser test pass. TTFA p50 and p95 are reported | 2.5 h |

The total is about 21.5 h, roughly a sprint and a half at 15 h a week. M5 and M6 are where to trim if time runs short, but spec feature 1 needs both the text takeover and the logged numbers.

**The M0 echo spike** answers the riskiest unknown first. Browsers reliably cancel echo only for audio they can use as a reference. Whether Chrome counts Web Audio output is unsettled, and the answer decides whether barge-in works on laptop speakers.

1. A script makes a 20-second Aura-2 clip. The key stays server-side.
2. A throwaway page plays the clip three ways while recording the mic with echo cancellation on:
   - Web Audio directly
   - Web Audio routed into an `<audio>` element
   - a local WebRTC loopback into an `<audio>` element
3. A script transcribes each recording with Nova-3 and counts the words of Wren's that leaked through.
4. Pick the route with about zero leaked words. If none gets there, v1 is headphones-first, and the server's echo guard carries more weight.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Chrome's echo canceller ignores Web Audio playback | Wren interrupts itself on speakers | The M0 spike runs before playback is built. Fallbacks: the `<audio>` or loopback route, the server echo guard, headphones |
| Groq limits: about 8K tokens a minute and 200K a day per model | 429s mid-session, and about 100 turns a day | Separate chat and rewrite models. Gemini fallback. Cancel streams promptly. Log context tokens |
| TTFA p50 over 1.0 s from end of speech | Misses goal 2 | The per-stage log on every turn, and the levers in the budget table |
| Premature end of turn in mocks | Wren cuts the user off | A longer hold in mocks. Speech while thinking cancels and merges. A later Flux trial |
| An open mic spends the credit | The credit runs out | Idle close, session cap, KeepAlive while muted |
| A free host adds latency or cold starts | Slower once deployed | Measure locally first. The hosting decision is a separate plan |
| iOS audio quirks | Phone sessions break | Desktop Chrome first. The phone comes with the deploy plan |

## Later, on top of this loop

- **Flux trial.** Deepgram's Flux model has built-in turn detection (`StartOfTurn`, `EagerEndOfTurn`, `TurnResumed`, `EndOfTurn`). An A/B against Nova-3 on TTFA and cut-ins is a natural experiment, behind the same adapter events.
- **Nova-3 keyterms** from the pipeline's company names, so names like "Velloway" aren't misheard.
- **Starting the reply early** on an eager end of turn, using the same cancellation path.

## Open questions

1. **Does `/turn` v1 belong in this plan (M1)?** The rolling summary and live-session extraction would stay with the memory work. The alternative is to build the full context builder as its own plan first. *Default until answered: in this plan.*
2. **Where do the first real sessions happen: laptop with headphones, laptop speakers, or phone?**
   - Speakers make M0 critical.
   - A phone pulls auth and hosting into this plan, because phones need HTTPS and can't reach a local server.
   - *Default until answered: laptop with Chrome; phone later.*

## Calls made in this design

These are the designer's choices, not decisions from the user. Check them.

1. A hand-rolled asyncio loop, the concept's first pick. Deepgram is reached over raw `websockets`, not Pipecat or the Deepgram SDK.
2. Aura-2 over REST, one streamed request per segment. The WebSocket's 20-flushes-a-minute limit ruled it out (see Build notes).
3. Barge-in pauses playback on a local detector, and cancels only once Nova-3 hears real words. Echo and backchannels resume playback.
4. Mocks wait longer before Wren replies than check-ins do.
5. TTFA's headline number runs from the end of speech, not from the turn commit. Both are logged.
6. The history keeps only heard words, and cut lines are marked.
7. Wren speaks first in every session.
8. Local-only with a loopback dev identity until the auth and hosting plan.
9. The transcript is saved turn by turn, with metrics inside it. No schema change.
10. `/turn` v1 includes the profile and the 7-day pipeline (cheap reads) but not the rolling summary.
