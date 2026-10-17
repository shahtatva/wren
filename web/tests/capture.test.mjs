import assert from "node:assert/strict";
import { test } from "node:test";

import { Framer, Resampler } from "../public/voice/capture-worklet.mjs";

test("resampler 48k→16k keeps duration", () => {
  const resampler = new Resampler(48000, 16000);
  let total = 0;
  for (let i = 0; i < 375; i++) {
    // 375 blocks of 128 = 48000 samples = 1 s of a 440 Hz tone
    const block = Float32Array.from({ length: 128 }, (_, j) => Math.sin((2 * Math.PI * 440 * (i * 128 + j)) / 48000));
    total += resampler.process(block).length;
  }
  assert.ok(Math.abs(total - 16000) <= 2, `got ${total} samples`);
});

test("resampler 44.1k→16k keeps duration", () => {
  const resampler = new Resampler(44100, 16000);
  let total = 0;
  for (let i = 0; i < 345; i++) total += resampler.process(new Float32Array(128)).length;
  assert.ok(Math.abs(total - (345 * 128 * 16000) / 44100) <= 2);
});

test("framer cuts 20 ms frames of 16-bit PCM and measures their level", () => {
  const framer = new Framer(320);
  assert.deepEqual(framer.push(new Float32Array(300).fill(0.5)), []);
  const frames = framer.push(new Float32Array(400).fill(0.5));
  assert.equal(frames.length, 2);
  for (const frame of frames) {
    assert.equal(frame.pcm.length, 320);
    assert.equal(frame.pcm[0], 16384);
    assert.ok(Math.abs(frame.rms - 0.5) < 1e-6);
  }
  assert.equal(framer.push(new Float32Array(0)).length, 0); // 60 samples wait for the next block
});

test("framer clips instead of wrapping", () => {
  const [frame] = new Framer(320).push(new Float32Array(320).fill(1.5));
  assert.equal(frame.pcm[0], 32767);
});
