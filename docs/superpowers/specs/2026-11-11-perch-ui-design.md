# Perch: how Wren looks

Design, 2026-11-11 · For [spec.md](../../spec.md) features 1 (voice), 2 (mock interviews) and 5 ("What Wren knows") · Direction boards: [Wren design directions](https://claude.ai/artifact/1GQHZqjFSmX9z2dNvciGwP), row A · Rules: [CLAUDE.md](../../../CLAUDE.md)

## Build notes (2026-11-12)

Built from [plans/2026-11-11-perch-ui.md](../plans/2026-11-11-perch-ui.md) in eight tasks on branch `perch-ui`, with a fresh review after Task 4 and again after Task 8. The ledger of every call made along the way is kept locally in `logs/perch-ui/`, which git ignores.

1. **What shipped.**
   - Perch's colors and type on every screen: apricot day and plum night, with Wren's words in Recursive's casual style. The bird is drawn once, in `web/lib/bird.tsx`, for the home page, `/talk`, `/knows` and the app icon.
   - `/talk`: the bird on its branch is the talk button, and `birdView()` picks its pose, status line, label and hint. The newest line that isn't yours leads, large. A mock interview gets the mock bar, with Step out, and a round mic button.
   - `/knows`: six sections with their counts; Edit, Delete and Clear on every item; "Moved from" on rescheduled interviews; and the not-saving-yet line while nothing is saved. Signed-out, refused and failed loads each say what to do next.
   - `voice/`: six routes in `voice/app/knows_routes.py` and their store functions. A delete removes the item's older versions in the same statement, and a memory edit embeds the new text before it saves.
2. **Checks.**
   - voice `pytest`: 788 passed and 24 skipped (the database tests, which run on their own). `WREN_DB_TESTS=1 pytest tests/test_store_db.py`: 24 passed.
   - evals `pytest evals/tests`: 56 passed.
   - web `npm test`: 130 of 130. Lint and build are clean.
   - Leak check: 760 files in `web/.next` (leaving out `cache/`), and none of the five keys.
   - e2e, with memory off (a throwaway dev user): `session_e2e.py --mode both` passed 48 of 49 checks, including the new icon check and both come-back checks. `voice_e2e.py` passed 6 of 7. Both misses are in item 4.
   - Look, at 390 × 844 and 375 × 667 in light and dark: every screen fits with no sideways scroll, including a 1,000-character memory, a 60-letter company name and a long interviewer name. The input stays in view on the short phone, and the bird's feet sit on the branch. `/knows` ran on the production build with fixture answers, so nothing real was written. `/dev/perch` and a `/talk` reply ran on the dev servers. With reduced motion the listening head stops, and every pose still looks different.
   - Keyboard: on `/talk`, Tab goes wordmark, What Wren knows, Sign out, Wren, message, Send, each with a visible ring, and Enter or Space on Wren starts a session. On `/knows`, Tab goes Back to talk, the six jump links, then each Edit. Save returns focus to that Edit. Delete asks with focus on the question, then lands on the section's heading.
   - Live and read-only: the owner's `/knows`, with memory on through the dev identity, shows About you with the time zone (America/Chicago), every other section empty, and the not-saving-yet line. It sent no writes.
3. **Where the build differs from this doc.**
   - **In a mock, the hint names the mic**, since the bird isn't the button there: "Tap the mic to start", "Tap the mic to turn it on", and so on.
   - **The talk page starts over when you come back.** Next keeps a page you leave hidden instead of unmounting it, so `/talk` came back with the old conversation and a stopped connection, and a tap did nothing. Hiding it now stops the connection and clears the conversation, and the next tap opens a new session. The e2e checks Back from home, and the look run checks What Wren knows, then Back to talk.
   - **The page's limits match the server's.** Each field stops at the server's length, and Save stays off past 10 roles or a role over 100 characters. Roles show "Separate roles with commas. Up to 10."
   - **An interview edit sends every field.** Role, date, time and notes can be empty but not left out, so an edit can't wipe a field by omission. Every `/knows` answer says `Cache-Control: no-store`, so the browser doesn't keep what Wren knows on disk.
   - **The not-saving-yet line** works as builder's call 8 says: it shows only while there are no memories and no interviews.
4. **Found in the last checks.**
   - **The app icon didn't render outside the build.** Satori, which draws it, takes only svg elements inside an `<svg>`, and the bird nested a component there. The build still prerendered both PNGs, so it showed only in dev, where every page logged a 500. The bird's parts are now a plain function, and the e2e checks that the icons render.
   - **`voice_e2e.py`'s barge-in check fails here and on master.** While Wren answered, the page heard the test's interruption at about 0.1 on its level meter, against 0.3 to 0.7 for the question, and never paused. The test predates the room-noise fix, which changed when the page counts a voice as yours. That's the likely cause, not yet checked. This branch changes no barge-in code. One run here passed, because the question itself cut into Wren's opening line.
   - **`session_e2e.py`'s "heard the sample when expected" fails here and on master.** The page's level trace first shows sustained voice 17.3 to 17.6 s after the click, outside the check's window of 14.7 ± 2 s, with or without Chrome's audio processing. Master reads 17.5 s. The server commits the turn on time in every run, and this branch changes no audio code.
5. **Known limits.**
   - **`/knows` shares the live path's database connection.** `PostgresMemory` runs one operation at a time on one connection, so a `/knows` read waits while a turn on another device reads memory. Leaving `/talk` ends its session, so on one device the two don't overlap.
   - The minors the reviews deferred are listed in the ledger.
6. **Left for later.** Spec feature 5's boxes stay open until the owner's live check on the phone: `/talk` in each state, and `/knows` with an About you round trip.

## Why

The owner asked for three designs, mobile-first: the conversation screen, the "What Wren knows" page (memories grouped by kind, each editable and deletable, showing which session it came from), and Wren's states (listening, thinking, speaking). The brief: "Warm and a little playful, never a SaaS dashboard."

Two directions went on a canvas. The owner chose **A, Perch**: Wren is a drawn bird on a branch, the bird is the talk button, and its posture shows what Wren is doing.

Today's look (a cream background, brown accent, Geist, and a breathing ring around a mic) is the most common generated look and says nothing about Wren. The "What Wren knows" page doesn't exist yet.

## Goals

1. `/talk` matches the Perch boards. The bird is the control, and its posture shows every voice status, mute, and a mock interview.
2. A "What Wren knows" page at `/knows` shows the profile, the pipeline and every current memory, grouped by kind, each with the session it came from and its date. Each one can be edited or deleted. Memory edits count from the next turn, and deleted ones never come back (spec feature 5).
3. The whole web app uses Perch's colors and type, including sign-in, the age step, the home page and the app icon.
4. The live path is no slower. Wren's voice level moves the beak the same way it moves the ring today, without re-rendering.

## Non-goals

- **Running extraction on live sessions.** Nothing extracts memories from live sessions yet: `extract()` only runs in the evals. The owner chose to build the page now and do live extraction as its own work, with its own design and eval run (see "Decided by the owner").
- Direction B, and any 3D or animated-character work beyond the drawn bird (concept.md: a simple illustrated avatar is enough).
- Pages for sessions (tapping a memory's source to read that session). Later.
- The `stories` table. Nothing writes it; story memories live in `memories` with kind `story`.
- Vibe decay. The page doesn't promise that "Lately" fades, because nothing makes it fade yet.

## Anti-goals

- A trust page that shows something other than what Wren uses. No rewording on the way to the screen.
- Charm that blurs a boundary. "Wren is an AI, not a person." stays on every screen, and an interviewer gets no bird flavor.
- Motion when nothing is happening, beyond the listening head-tilt. Reduced motion turns every loop off.

## Design

### Colors and type

Light is "apricot day", dark is "plum night". The tokens replace today's in `web/app/globals.css`, keeping the Tailwind names where one exists (`background`, `foreground`, `wren`, `feather`, `brick`) so the change stays small.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| background | #f9c59b | #2b1e2e | Every page |
| toffee (new) | #edb283 | #3a2838 | Alternate bands on `/knows` |
| field (new) | #fde3cc | #3a2a3d | Inputs |
| foreground | #3a1f2b | #f7e3cc | Text |
| ink-soft (new) | #5b3a3c | #d9bfae | Secondary text. Replaces `text-foreground/70`, which fails contrast on apricot |
| wren | #7b3a1d | #e09a6a | Wordmark, headings, Send, song lines |
| feather | #c98a5e | #5e4658 | Rules only, never text |
| brick | #842a1f | #f08b78 | Errors and Delete |

Every text pair passes 4.5:1, brick on toffee included (4.8:1). The bird, branch and leaf colors are on the boards and go in the same file.

**Type: Recursive, one family**, loaded with `next/font/google` and `axes: ["CASL"]`. It replaces Geist and Geist Mono.

- **Wren's words:** casual (`font-variation-settings: "CASL" 1`).
- **Your words and the interface:** linear (CASL 0).
- **An interviewer's words:** linear at weight 800. It's formal, with no bird flavor.

### The bird

One component, `web/lib/bird.tsx`, draws the bird as inline SVG with its parts classed (`head`, `tail`, `beak-top`, `beak-bot`, `eye`, `eye-shut`, `song`, `hear`). The home page, `/talk`, `/knows` and the app icon all use it. It takes a `pose`, and the poses are CSS on those parts, in `globals.css` beside today's keyframes.

**Voice level.** The talk page already gets Wren's voice level about 23 times a second (`onLevel`) and writes it to a `--level` custom property on the ring. It writes it to the bird instead. The top of the beak rotates by `calc(var(--level) * -18deg)` and the song lines fade in with the level, inner line first. The boards fake this with a loop; the app uses the real level.

**Poses.** A pure function in `web/lib/bird-state.ts` maps what the page knows (status, started, mic on, hearing) to a pose, a status line, a hint and the button's label. The first row that matches wins:

| When | Pose |
| --- | --- |
| Not started | Perched and still |
| Reconnecting | Looks around, and the branch goes dotted |
| Connecting | Looks around |
| Speaking, even with the mic off, as when Wren answers a typed message | Beak and song lines follow the level |
| Thinking | Looks up and hops along the branch |
| Listening to you (`paused`: Wren stopped for you) | Leans in, and sound arcs come toward it |
| Mic off, or not hearing (paused for quiet, or speech-to-text down) | Eyes shut, head tucked |
| Listening | Head cocked, tail up, and it tilts its head now and then |

- **The status line and the button's label** are today's: `muted ? "Muted" : STATUS[status]`, and the label expression in `talk.tsx` ("Start talking", "Reconnect", "Turn the mic on", "Mute"). The e2e tests and screen readers keep working.
- **The hint** is "Tap Wren to" plus the label's action: "Tap Wren to start", "Tap Wren to reconnect", "Tap Wren to turn the mic on", "Tap Wren to mute".

Reduced motion stops the tilt, hop and look-around. The poses are still and still differ, so every state reads without motion. The song lines still fade with the level, without moving.

### The conversation screen

```
┌──────────────────────────────────┐
│ Wren        What Wren knows  Sign out │  header: wordmark links home
│ Mock interview with Dana   🐦 Step out │  only during a mock
│                                  │
│ earlier lines, small and soft    │  section "Conversation", ol > li
│                    your line, right │
│ WREN'S LATEST LINE, LARGE        │
│                                  │
│         ~~~~ (bird) ~~~~         │  the bird on its branch, the talk button
│            Listening             │  status, aria-live
│         Tap Wren to mute         │  hint
│ notice, in brick                 │
│ [ Or type here          ] [Send] │
│   Wren is an AI, not a person.   │
└──────────────────────────────────┘
```

- **Transcript.** The newest line that isn't yours is large (27 px casual for Wren, 24 px heavy for an interviewer). The lines before it are 17 px in ink-soft. Your lines are right-aligned. Each `li` keeps a `span` label first and the text `p` first: "You" and "Wren" labels are visually hidden, but an interviewer's name shows, because two voices that aren't yours need telling apart. Cut-off lines and the live caption work as they do today.
- **The stage.** The branch runs edge to edge. The bird is a `<button>` with today's labels. Its size follows the screen height, so a short phone keeps the input in view.
- **Mock interview.** The bird shrinks into the mock bar beside Step out. A plain round mic button with the same labels takes its spot, under the interviewer's name. Step out brings the bird back.
- **Empty screen:** "Tap Wren and say hi. I'll listen, and you can cut in whenever you like."
- **Copy changes in `web/lib/voice/client.ts`:** `idle` becomes "I paused listening. Tap Wren when you want to keep talking." `lost` becomes "I lost the connection. Tap Wren to reconnect, or keep typing."
- **Wide screens** keep today's centered 40 rem column.
- **The home page** shows the bird on its branch, "Wren", the description line and "Talk to Wren".
- **The browser bar** (`themeColor`, and the manifest's `theme_color` and `background_color`) takes the background color, so it blends with the page.

### What Wren knows

A page at `/knows`, linked from the talk header. Going there ends the live session, as leaving `/talk` does today.

**Sections, in order**, alternating background and toffee bands. Each has a one-line note:

| Section | From | Note |
| --- | --- | --- |
| About you | `profiles`: name, summary, target roles, time zone | Wren keeps these in mind every time you talk. |
| Interviews | `pipeline`, current rows | Companies, stages and dates. Wren brings up anything in the next seven days. |
| Stories | `memories`, kind `story` | The examples you tell in interviews. |
| Feedback | kind `feedback` | Patterns in how you answer, from mocks and debriefs. |
| Moments | kind `episode` | Things that happened along the way. |
| Lately | kind `vibe` | How you've said you feel. |

- **Top of the page:** a back link, "What Wren knows" with the small bird, and the intro "Everything Wren remembers about you. Edit or delete anything. Changes count from your next message."
- **Until live extraction lands:** while there are no memories and no interviews, a plain line under the intro says "Wren doesn't save memories from your talks yet, so most of this page stays empty for now. You can fill in About you today." The extraction work deletes it.
- **Jump links:** one per section, with its count. A section with nothing in it says "Nothing here yet."
- **Each memory:** its text exactly as stored, then its source: "From a check-in, Nov 10" or "From a mock interview, Nov 9", from the session's mode and start date in the owner's time zone. When the session is gone it says "Saved Nov 10" instead. Memories are newest first within a section.
- **Each interview:** company, stage, role if there is one, date and time, and notes. When it was rescheduled, "Moved from Thu, Nov 13" comes from its superseded rows. Upcoming interviews come first, soonest at the top, then undated ones, then past ones, most recent first.
- **Edit** opens the item in place:
  - **A memory:** one text box.
  - **An interview:** company, role, stage, date (`type=date`), time (`type=time`) and notes.
  - **A profile field:** one field. The fields are Name, Background (the summary, such as "MS in Information Management, targeting BDR roles") and Roles you're aiming for (comma-separated).
  - **Buttons:** Save, Cancel, and the Delete button. The time zone isn't editable: it's set from the device at the age step.
- **Delete** asks first: "Delete this memory? Wren won't use it again." For an interview: "Delete this interview? Wren won't bring it up again." A profile field says Clear instead of Delete.
- **After a change**, a status line in that section says "Saved. Wren uses your version from your next message." or "Deleted. Wren won't use it again."
- **Errors:** "Can't reach Wren's memory right now. Try again in a minute." and "That didn't save. Try again in a minute."
- **Signed out:** a link to sign in on `/talk`.

### The voice server's side

The browser talks only to `voice/`, so the page reads and writes through new routes in `voice/app/knows_routes.py`.

| Route | Does |
| --- | --- |
| `GET /knows` | Profile, current pipeline rows with their history, current memories with their sources, and the time zone |
| `PATCH /knows/memories/{id}` `{text}` | New text (1 to 1,000 characters) and a new embedding |
| `DELETE /knows/memories/{id}` | Deletes it, and its older versions |
| `PATCH /knows/pipeline/{id}` | Corrects company, role, stage, date, time and notes in place |
| `DELETE /knows/pipeline/{id}` | Deletes it, and its history |
| `PATCH /knows/profile` | Name, summary, target roles (upserts the row) |

- **Who may call:** the same `admit()` as `/turn`, so a signed-in adult, or the loopback dev identity. Refusals are JSON `{code, message}`, as on the auth routes.
- **Origin check:** as on the auth routes. CORS adds GET, PATCH and DELETE.
- **Row scope:** all work runs inside `store.as_user`, so RLS scopes every row. A row that isn't yours, or isn't current, is a 404.
- **When memory is off** (no schema, unknown user), the routes return 503.
- **Store functions** go in `voice/app/memory/store.py`, taking `schema` like the others. They're exposed through `PostgresMemory`, and `NoMemory` refuses.
- **Deleting never brings an older fact back.** `superseded_by` is `on delete set null`, so deleting the newest row on its own would make the row it replaced current again: delete Friday and Thursday comes back. So a delete also removes every row that leads to it through `superseded_by`, in one statement with a recursive CTE.
- **Editing a memory re-embeds it** with `embed_texts([text], "document", ...)`, the call the evals use for memories. `tsv` is a generated column, so it follows. Kind, entities and importance stay as they are. If the embedding call fails, nothing changes and the page shows its error. This is a new write path, not a change to extraction, retrieval, ranking or the embedding model, so it doesn't need an eval run.
- **When changes count.** Retrieval reads the table every turn, so memory edits count from the next turn. Profile and pipeline load when a session opens, and returning from `/knows` opens one, so they count from the next session.

### Testing

- **voice:**
  - Route tests with a fake memory: who's admitted, each refusal, 404 for another user's row, input limits, and 503 with memory off.
  - Database tests (`WREN_DB_TESTS=1`) for each store function: only current rows are listed, a delete takes its older versions with it, and RLS hides another user's rows.
- **web:** `node --test` for `bird-state.ts` (every row of the table, including precedence), the page's view model (grouping, sorting, source labels, dates in the owner's time zone) and its form reducers.
- **e2e:** update `session_e2e.py` for the new empty-screen text and where the status line lives. The other locators keep working by design. Run `voice_e2e.py` and `session_e2e.py --mode both`.
- **Look:** screenshots of `/talk` in every pose and of `/knows` at 390 × 844 and 375 × 667, light and dark, plus a keyboard-only pass.
- **Checks:** lint, build, `pytest` and the leak check. The live path on the server doesn't change, so the latency bench isn't needed. On the page, the level is still one style write per frame.
- **Local setup:** local `/knows` needs memory on. The `WREN_DEV_USER_ID` in `voice/.env` no longer exists in `auth.users`, so set it to the current account's id, or sign in. Local `ALLOWED_ORIGINS` lists only production, so pass `http://localhost:3000`, as the tests do.

### Builder's calls

1. **Memory text shows exactly as stored.** Extraction writes "The user …", so that's what the page shows, and an edit saves what the owner typed. Rewording on the way to the screen would show something Wren doesn't use.
2. **A delete removes the item's older versions too**, so nothing old comes back.
3. **Interview edits correct the row in place.** A correction isn't a reschedule, so it gets no new version.
4. **Labels and status words stay as they are** ("Mute", "Muted", "Wren is talking"), so tests and screen readers don't change. Only the pose is new.
5. **`/knows` is its own page, not a sheet over the conversation.** A sheet would leave the mic live while the owner reads.
6. **Superseded memories aren't listed.** Interviews show their history because reschedules are common. Memories have no supersede path yet, since compaction isn't built.
7. **Every screen gets the new tokens**, and the app icon becomes the bird.
8. **The not-saving-yet line shows only while there are no memories and no interviews**, so it's never wrong beside real entries, such as test data.

## Decided by the owner

**Live extraction is its own work** (option (a), 2026-11-11). This work builds Perch and the page now. The page says plainly that Wren isn't saving memories from talks yet, and About you works today. Live extraction follows, with its own design and an eval run before and after.
