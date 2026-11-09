import assert from "node:assert/strict";
import { test } from "node:test";

import { Vad } from "../lib/voice/vad.ts";

const FRAME_MS = 20;

function feed(vad: Vad, levels: number[], wrenSpeaking = false, startMs = 0, roomFrame = wrenSpeaking): number[] {
  const onsets: number[] = [];
  levels.forEach((level, i) => {
    const now = startMs + i * FRAME_MS;
    if (vad.push(level, now, wrenSpeaking, FRAME_MS, roomFrame) === "onset") onsets.push(now);
  });
  return onsets;
}

function quietRoom(): Vad {
  const vad = new Vad();
  feed(vad, Array(20).fill(0.001));
  return vad;
}

// People talking near the phone, taking turns: 0.3 s of voice, then a 0.2 s gap.
function talk(seconds: number, loud = 0.04, quiet = 0.002): number[] {
  const frames = Math.round((seconds * 1000) / FRAME_MS);
  const levels: number[] = [];
  while (levels.length < frames) levels.push(...Array(15).fill(loud), ...Array(10).fill(quiet));
  return levels.slice(0, frames);
}

// A quiet room heard for 3 s while Wren speaks: long enough to trust what it learned.
function trustedQuietRoom(): Vad {
  const vad = new Vad();
  feed(vad, Array(150).fill(0.001), true, -3000);
  return vad;
}

// A room with people talking, heard for 2 s before Wren's opening plays.
function roomOfTalkers(): Vad {
  const vad = new Vad();
  feed(vad, talk(2), false, 0, true);
  return vad;
}

test("vad onset needs 200ms of voice above the adaptive floor", () => {
  const vad = new Vad();
  assert.deepEqual(feed(vad, Array(20).fill(0.001)), []);
  // Voice starts at 400 ms. Nine frames (180 ms) aren't enough; the tenth makes 200 ms.
  assert.deepEqual(feed(vad, Array(15).fill(0.1), false, 400), [580]);
  assert.equal(vad.onsetAt, 400);
});

test("a short dip between syllables doesn't restart the count", () => {
  // A 100 ms first syllable, a 60 ms dip, then the next one.
  const vad = quietRoom();
  const levels = [0.04, 0.05, 0.06, 0.05, 0.04, 0.001, 0.001, 0.001, 0.05, 0.4, 0.4, 0.4, 0.4, 0.4];
  assert.deepEqual(feed(vad, levels, false, 400), [640]); // the tenth voiced frame within 260 ms
  assert.equal(vad.onsetAt, 400);
});

test("vad fires once per stretch of speech", () => {
  const vad = quietRoom();
  assert.deepEqual(feed(vad, Array(30).fill(0.1), false, 400), [580]);
  feed(vad, Array(15).fill(0.001), false, 1000);
  assert.deepEqual(feed(vad, Array(15).fill(0.1), false, 1300), [1480]);
});

test("vad needs more energy while Wren is speaking", () => {
  assert.deepEqual(feed(quietRoom(), Array(10).fill(0.01), false, 400), [580]);
  assert.deepEqual(feed(trustedQuietRoom(), Array(10).fill(0.01), true, 400), []); // could be Wren's own echo
  assert.deepEqual(feed(trustedQuietRoom(), Array(10).fill(0.1), true, 400), [580]);
});

test("Wren isn't paused before the room has been heard for 3 s", () => {
  const vad = new Vad();
  feed(vad, Array(25).fill(0.001), false, 0, true); // half a second of a quiet room before the opening
  assert.deepEqual(feed(vad, talk(2), true, 500), [], "people who start talking as Wren does aren't the owner");
  assert.deepEqual(feed(vad, talk(1), true, 2500), [], "and once it trusts the room, they still aren't");
  assert.deepEqual(feed(vad, Array(15).fill(0.3), true, 3500), [3680], "while the owner still pauses Wren");
});

test("a short click is not an onset", () => {
  assert.deepEqual(feed(quietRoom(), [0.2, 0.2, 0.001, 0.001, 0.001, 0.001], false, 400), []);
});

test("the room's talkers don't pause Wren", () => {
  assert.deepEqual(feed(roomOfTalkers(), talk(8), true, 2000), []);
});

test("your voice over the room's talkers still pauses Wren", () => {
  const vad = roomOfTalkers();
  feed(vad, talk(8), true, 2000);
  assert.deepEqual(feed(vad, Array(20).fill(0.2), true, 10_000), [10_180]);
  assert.equal(vad.onsetAt, 10_000);
});

test("a voice as loud as the room's talkers doesn't count", () => {
  const vad = roomOfTalkers();
  feed(vad, talk(8), true, 2000);
  assert.deepEqual(feed(vad, Array(20).fill(0.06), true, 10_000), []);
});

test("the bar falls again once the room is quiet", () => {
  const vad = new Vad();
  feed(vad, talk(8), true, 0);
  feed(vad, Array(450).fill(0.001), true, 8_000); // 9 s: the talk has left the 8 s the room is judged on
  assert.deepEqual(feed(vad, Array(15).fill(0.02), true, 17_000), [17_180]);
});

test("echo that leaks back doesn't raise the bar past today's", () => {
  const vad = new Vad();
  assert.deepEqual(feed(vad, Array(400).fill(0.01), true, 0), []);
  assert.deepEqual(feed(vad, Array(15).fill(0.1), true, 8_000), [8_180]);
});

test("talk that starts mid-session pauses Wren at most once, as it begins", () => {
  const vad = trustedQuietRoom();
  feed(vad, Array(250).fill(0.001), true, 0); // 8 s of a quiet room in all
  const onsets = feed(vad, talk(4), true, 5000);
  assert.ok(onsets.length <= 1, `paused ${onsets.length} times`);
  assert.ok(onsets.every((at) => at < 5600), "and only as the talk began");
});

test("short overlaps don't teach it your voice", () => {
  const vad = roomOfTalkers();
  const onsets: number[] = [];
  for (let round = 0; round < 4; round++) {
    const at = 2000 + round * 2000;
    onsets.push(...feed(vad, talk(1.7), true, at));
    // The owner cuts in for 0.3 s. Once the page pauses Wren, 0.2 s in, the rest isn't the room's.
    onsets.push(...feed(vad, Array(10).fill(0.2), true, at + 1700));
    onsets.push(...feed(vad, Array(5).fill(0.2), true, at + 1900, false));
  }
  assert.equal(onsets.length, 4, "every cut-in still counts");
});
