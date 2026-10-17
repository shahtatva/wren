import assert from "node:assert/strict";
import { test } from "node:test";

import { PlaybackQueue } from "../public/voice/playback-worklet.mjs";

const RATE = 24000;

function samples(n, value = 8192) {
  return new Int16Array(n).fill(value);
}

function renderAll(queue, blocks) {
  const events = [];
  const out = [];
  for (let i = 0; i < blocks; i++) {
    const block = new Float32Array(128);
    events.push(...queue.render(block));
    out.push(...block);
  }
  return { events, out };
}

test("queue emits start/end per segment and reports played ms", () => {
  const queue = new PlaybackQueue(RATE);
  queue.push(1, 0, samples(480)); // 20 ms
  queue.complete(1, 0);
  queue.push(1, 1, samples(240));
  queue.complete(1, 1);

  const first = new Float32Array(128);
  assert.deepEqual(queue.render(first), [{ type: "segment-start", turn: 1, seg: 0 }]);
  assert.ok(Math.abs(first[0] - 0.25) < 1e-6);
  assert.deepEqual(queue.position(), { turn: 1, seg: 0, playedMs: (128 / RATE) * 1000 });

  const { events } = renderAll(queue, 6);
  assert.deepEqual(events, [
    { type: "segment-end", turn: 1, seg: 0 },
    { type: "segment-start", turn: 1, seg: 1 },
    { type: "segment-end", turn: 1, seg: 1 },
    { type: "drained" },
  ]);
  assert.equal(queue.position(), null);
  assert.equal(queue.active, false);
});

test("an incomplete segment waits for more audio instead of ending", () => {
  const queue = new PlaybackQueue(RATE);
  queue.push(2, 0, samples(100));
  const { events, out } = renderAll(queue, 2);
  assert.deepEqual(events, [{ type: "segment-start", turn: 2, seg: 0 }]);
  assert.equal(out.slice(100).every((v) => v === 0), true); // an underrun plays silence
  queue.complete(2, 0);
  assert.deepEqual(renderAll(queue, 1).events, [{ type: "segment-end", turn: 2, seg: 0 }, { type: "drained" }]);
});

test("flush drops everything and pause outputs silence", () => {
  const queue = new PlaybackQueue(RATE);
  queue.push(3, 0, samples(24000)); // 1 s
  renderAll(queue, 4);
  queue.pause();
  const fading = renderAll(queue, 2); // the 10 ms fade (240 samples) runs out within two blocks
  assert.ok(Math.abs(fading.out[0]) > 0 && fading.out.slice(240).every((v) => v === 0));
  const held = queue.position().playedMs;
  const paused = renderAll(queue, 10);
  assert.equal(paused.out.every((v) => v === 0), true);
  assert.equal(queue.position().playedMs, held); // a pause holds the position
  queue.resume();
  assert.ok(renderAll(queue, 3).out.slice(240).every((v) => Math.abs(v - 0.25) < 1e-6));
  queue.flush();
  const after = renderAll(queue, 2);
  assert.equal(after.out.every((v) => v === 0), true);
  assert.equal(queue.position(), null);
});

test("audio for a turn the browser has flushed is ignored", () => {
  const queue = new PlaybackQueue(RATE);
  queue.push(4, 0, samples(480));
  queue.flush(4);
  queue.push(4, 1, samples(480)); // late audio from the cancelled reply
  queue.push(5, 0, samples(480));
  assert.deepEqual(renderAll(queue, 1).events, [{ type: "segment-start", turn: 5, seg: 0 }]);
});

test("reset forgets dropped turns, for a new connection that numbers turns from 1 again", () => {
  const queue = new PlaybackQueue(RATE);
  queue.push(2, 0, samples(480));
  queue.flush(2);
  queue.reset();
  queue.push(2, 0, samples(480));
  assert.deepEqual(renderAll(queue, 1).events, [{ type: "segment-start", turn: 2, seg: 0 }]);
});
