import assert from "node:assert/strict";
import { test } from "node:test";

import { parseServerMessage, reconnects, unpackAudio } from "../lib/voice/protocol.ts";

test("unpackAudio reads the header", () => {
  const buf = new ArrayBuffer(8);
  const view = new DataView(buf);
  view.setUint16(0, 258); // turn, big-endian
  view.setUint16(2, 3); // segment
  view.setInt16(4, -2, true); // samples, little-endian like linear16
  view.setInt16(6, 1000, true);
  const { turn, seg, pcm } = unpackAudio(buf);
  assert.equal(turn, 258);
  assert.equal(seg, 3);
  assert.deepEqual(Array.from(pcm), [-2, 1000]);
});

test("unpackAudio rejects a frame shorter than its header", () => {
  assert.throws(() => unpackAudio(new ArrayBuffer(3)));
});

test("parseServerMessage keeps known messages and drops the rest", () => {
  assert.deepEqual(parseServerMessage('{"type":"interrupt","turn":4}'), { type: "interrupt", turn: 4 });
  assert.equal(parseServerMessage("not json"), null);
  assert.equal(parseServerMessage('{"no":"type"}'), null);
});

test("only a refusal or the session cap stops the page from reconnecting", () => {
  assert.equal(reconnects(4401), false); // refused: retrying won't change it
  assert.equal(reconnects(4410), false); // the session is over
  assert.equal(reconnects(1006), true); // the connection dropped
  assert.equal(reconnects(1000), true);
});
