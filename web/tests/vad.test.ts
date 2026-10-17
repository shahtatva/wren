import assert from "node:assert/strict";
import { test } from "node:test";

import { Vad } from "../lib/voice/vad.ts";

const FRAME_MS = 20;

function feed(vad: Vad, levels: number[], wrenSpeaking = false, startMs = 0): number[] {
  const onsets: number[] = [];
  levels.forEach((level, i) => {
    const now = startMs + i * FRAME_MS;
    if (vad.push(level, now, wrenSpeaking) === "onset") onsets.push(now);
  });
  return onsets;
}

function quietRoom(): Vad {
  const vad = new Vad();
  feed(vad, Array(20).fill(0.001));
  return vad;
}

test("vad onset needs 100ms of voice above the adaptive floor", () => {
  const vad = new Vad();
  assert.deepEqual(feed(vad, Array(20).fill(0.001)), []);
  // Voice starts at 400 ms. Four frames (80 ms) aren't enough; the fifth makes 100 ms.
  assert.deepEqual(feed(vad, Array(10).fill(0.1), false, 400), [480]);
  assert.equal(vad.onsetAt, 400);
});

test("a short dip between syllables doesn't restart the count", () => {
  // Measured in Chrome: a 100 ms first syllable, a 60 ms dip, then the next one.
  const vad = quietRoom();
  const levels = [0.04, 0.05, 0.06, 0.001, 0.001, 0.05, 0.4, 0.4];
  assert.deepEqual(feed(vad, levels, false, 400), [520]); // the fifth voiced frame within 160 ms
  assert.equal(vad.onsetAt, 400);
});

test("vad fires once per stretch of speech", () => {
  const vad = quietRoom();
  assert.deepEqual(feed(vad, Array(30).fill(0.1), false, 400), [480]);
  feed(vad, Array(15).fill(0.001), false, 1000);
  assert.deepEqual(feed(vad, Array(10).fill(0.1), false, 1300), [1380]);
});

test("vad needs more energy while Wren is speaking", () => {
  assert.deepEqual(feed(quietRoom(), Array(10).fill(0.01), false, 400), [480]);
  assert.deepEqual(feed(quietRoom(), Array(10).fill(0.01), true, 400), []); // could be Wren's own echo
  assert.deepEqual(feed(quietRoom(), Array(10).fill(0.1), true, 400), [480]);
});

test("a short click is not an onset", () => {
  assert.deepEqual(feed(quietRoom(), [0.2, 0.2, 0.001, 0.001, 0.001, 0.001], false, 400), []);
});
