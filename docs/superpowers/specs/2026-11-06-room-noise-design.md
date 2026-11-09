# Talking through a noisy room

Design, 2026-11-06 · For [spec.md](../../spec.md) feature 1 ("Voice with barge-in") · Builds on [2026-10-16-voice-loop-design.md](2026-10-16-voice-loop-design.md) · Rules: [CLAUDE.md](../../../CLAUDE.md)

## Why

The owner used Wren on their phone in a room where other people were talking. Wren paused, and sometimes never started a reply, even though speech-to-text couldn't make out a word anyone in the room said. In the owner's words: "It stops to try and listen."

Asked which failure bothers them more, the owner said Wren pausing for the room is worse than Wren sometimes talking over them.

## What the owner wants

Wren keeps talking through background chatter and only stops when the owner really means to interrupt. The bar is "loud enough and long enough that it's clearly you, not the room." When in doubt, Wren keeps talking. The owner confirmed this wording, and left the rest to the builder ("whatever you see fit").

## What goes wrong today

Four paths let the room stop or block Wren:

1. **The page pauses Wren for any loud-enough sound.** The page's detector (`web/lib/voice/vad.ts`) pauses playback after 100 ms of voice within 160 ms. Its noise floor only moves on frames it judges quiet. In a room where people are already talking above its bar, it never learns that the room is loud, so their voices keep counting as the owner's.
2. **The server holds the pause while it hears sound.** A pause resumes only once the server's detector (`voice/app/voice/vad.py`, `EnergyVad`) goes quiet. That detector has the same floor bug, so steady chatter keeps the pause going.
3. **The server cancels a reply before it starts.** Voice heard while Wren is preparing a reply (`heard == "start"` in `_on_audio`) cancels the reply, so it can merge in what the owner says next. Chatter does this too. That's the "Wren didn't even start" case.
4. **Words from anyone interrupt.** While Wren speaks, any transcript that passes `is_interruption` cuts the reply, with or without a pause from the page. While Wren prepares a reply, `real_words` cancels it, and words heard before the opening replace it. This didn't happen to the owner this time, but it will once the background talk is clear enough to transcribe.

## Goals

1. Chatter alone never pauses Wren, never keeps it paused, and never cancels a reply before it starts.
2. The owner's own voice at the phone still pauses Wren within the spec's 300 ms on a clear start, and their words still interrupt.
3. The bar adapts. A quiet room behaves as it does today, and a room that gets loud raises the bar within a few seconds.

## Non-goals

- Telling the owner's voice from another person's by who is speaking. That would need speaker identification and a new service.
- Background talk mixed into the owner's own turn while Wren listens. The owner hasn't reported it. Revisit if it shows up.
- Chrome's audio processing settings. They wait on the M0 echo spike.
- A tap-to-stop button. **Builder's call:** not in this change. The owner can ask for it once they've tried this.

## Anti-goals

- Ending the owner's turns early, or cutting them off sooner. The server's detector also decides when the owner has stopped talking.
- A slower Wren in a quiet room.

## Design

### One rule for "it's you": louder than the room's talkers, and long enough

The cue that separates the owner from the room is distance. The owner talks a few inches from the phone. Other people are a metre or more away, so their voices reach the mic much quieter. **Assumed, not measured:** the owner's voice at the phone is about 10 to 20 dB louder than people a metre or two away. The tests use that, and the owner's live check confirms it.

A low percentile of all frames, the room's quiet baseline, doesn't work for this. It handles a crowd's steady babble. But with one or two people taking turns, the baseline sits in the gaps between their words, and their voices clear any "several times louder than the room" bar.

So the yardstick is **how loud other people get while Wren talks**, because that's when the owner is normally quiet:

- **The room's talkers:** a high percentile (around the 80th, so short overlaps by the owner never become the yardstick) of the mic level over the last several seconds of frames heard while Wren is speaking, and while Wren prepares its opening. That level also takes in whatever of Wren's own voice leaks back.
- **Clearly you:** a frame some margin louder than the room's talkers, and above today's absolute floors, so a quiet room behaves as it does today.

Both detectors use this rule:

- **The page** decides when to pause Wren. It needs about 200 ms of clearly-you voice within a slightly longer window, so a laugh or a clatter doesn't pause Wren, and a clear start still pauses it within 300 ms.
- **The server** keeps the same yardstick. It uses it to judge the voice it hears: when Wren is preparing a reply, during a pause, and when words arrive.

The server's own end-of-speech detection, which decides when to ask Deepgram for final words, stays as it is. It can only get slower in a noisy room, never earlier.

The margin, the percentile and the windows are the builder's to set, from the tests below. They live as named constants.

### Words count only with your voice

- **While Wren speaks,** words interrupt only during a pause the page started for clearly-you voice. **Changed during the build:** a second route also let words in when the server alone had heard clearly-you voice in the last couple of seconds. In the browser run with a nearby talker, that route let the talker's words cut Wren's opening off: the talk started after a quiet first second, and the server's yardstick lagged it. The owner ranked pausing for the room as the worse failure, so the route is gone.
- **Background words are ignored,** meaning words that arrive with no clearly-you voice behind them. They also stay out of the owner's next turn: no caption, and no commit.
- **While Wren prepares a reply,** renewed voice or words cancel it only if the server heard clearly-you voice in the last couple of seconds. Deepgram's words lag the audio by about a second. The same rule covers words heard before the opening.
- **Warm-up:** the page and the server judge voice against the yardstick only after hearing 3 s of the room, while Wren speaks or prepares its opening. Before that, nothing in those moments counts as the owner's voice.

### Found by the final review, and fixed

- **Pauses aren't room.** Frames heard while the page holds Wren paused are the owner's, so they're never learned. Before this, two cut-ins taught the page the owner's voice.
- **The opening phase ends at Wren's first line.** If the mic starts after the opening has played, the owner's first turn isn't learned as the room.
- **A faster rise.** The yardstick is the higher of the 80th percentile over 8 s and over the last 2.4 s, so new talk raises it within about a second. The short window is 2.4 s, not 2, so two quick cut-ins stay under the fifth the percentile ignores.
- **It survives reconnects.** The server keeps the yardstick with the conversation.
- **Crisis words always get through.** While Wren speaks without a page pause, a crisis mention still interrupts. CLAUDE.md says a crisis mention always routes to real resources. Other ignored words are logged with a reason, never their text.

### A pause always ends

If a pause gets no confirming words within a cap, Wren resumes, even if the room is still loud. The cap is about 2.5 s: a confirmation window plus Deepgram's lag. Before the cap, a pause holds while any frame of the owner's voice continues, counting the gaps between words.

### What doesn't change

- the socket protocol;
- the spec's 300 ms pause target;
- echo handling (`is_echo`, the echo tail) and backchannels;
- mock interviews: the same rules hold with the interviewer speaking.

## Verification

- **Unit, both detectors:** synthetic level tracks, including:
  - a crowd's steady babble;
  - one or two people taking turns, with gaps;
  - talk that starts mid-session;
  - the owner's voice about 12 to 15 dB over the talkers;
  - a quiet room.

  Today's detector tests keep passing, or change only where the longer page onset requires it.
- **Session:**
  - background voice while Wren prepares a reply doesn't cancel it;
  - a pause with no words resumes within the cap, despite the noise;
  - background words while Wren speaks neither interrupt it nor reach the next turn;
  - the owner's words still interrupt, both after a page pause and with only the server's check;
  - the end-of-turn tests still pass.
- **Browser:** `web/e2e/session_e2e.py` gains `--chatter DB`.
  - It mixes talk built from `evals/audio/sample.wav` under the whole fake-mic track, DB below the sample.
  - The talk is time-reversed copies of the sample. That sounds like speech but has no words, like the room the owner was in.
  - Two styles: a crowd (overlapping copies), and one nearby talker (a single copy with its natural gaps).
  - Expected: the page sends no `barge_in` while Wren speaks; the opening and the reply play to the end; the sample still becomes one answered turn.
  - The plain run stays green.
- **Live:** the owner on the phone, in a room with people talking.

## Calls the builder made

The owner delegated these on 2026-11-06:

- Voice stays the way to interrupt. No tap-to-stop button in this change.
- The yardstick is how loud other people get while Wren talks, not the room's quiet baseline, so one or two people nearby are covered, not only a crowd.
- The page and the server use the same rule for "it's you".
- Words interrupt a speaking Wren only after a pause from the page. The server's own route was tried and dropped after the browser run, above.
- Neither side counts voice as the owner's until it has heard 3 s of the room.
- Background words that don't interrupt also stay out of the next turn.
- The server's end-of-speech detection is unchanged.
- The pause cap is about 2.5 s.
- The test talk is built from the reversed sample: free, repeatable, and without words.
- This goes before the 2026-10-19 end-of-turn plan. When that plan is revised, its "continued" merge while Wren speaks gets the same clearly-you check.
