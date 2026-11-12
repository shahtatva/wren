import assert from "node:assert/strict";
import { test } from "node:test";

import { birdView } from "../lib/bird-state.ts";
import type { VoiceStatus } from "../lib/voice/client.ts";

const view = (status: VoiceStatus, { micOn = true, hearing = true, mock = false } = {}) => birdView({ status, micOn, hearing, mock });

test("before the first tap Wren perches still and offers to start", () => {
  assert.deepEqual(view("idle", { hearing: false }), { pose: "perched", status: "Not started", label: "Start talking", hint: "Tap Wren to start" });
});

test("reconnecting looks around and offers to reconnect, even with the mic off", () => {
  assert.deepEqual(view("reconnecting", { micOn: false }), { pose: "reconnecting", status: "Muted", label: "Reconnect", hint: "Tap Wren to reconnect" });
});

test("connecting looks around, and offers the mic until the server hears", () => {
  assert.deepEqual(view("connecting", { hearing: false }), { pose: "looking", status: "Connecting", label: "Turn the mic on", hint: "Tap Wren to turn the mic on" });
});

test("speaking follows the voice, even with the mic off", () => {
  assert.deepEqual(view("speaking"), { pose: "speaking", status: "Wren is talking", label: "Mute", hint: "Tap Wren to mute" });
  assert.deepEqual(view("speaking", { micOn: false }), { pose: "speaking", status: "Muted", label: "Turn the mic on", hint: "Tap Wren to turn the mic on" });
});

test("thinking hops, even with the mic off", () => {
  assert.equal(view("thinking").status, "Thinking");
  assert.equal(view("thinking").pose, "thinking");
  assert.equal(view("thinking", { micOn: false }).pose, "thinking");
});

test("when Wren stops for you, it leans in", () => {
  assert.deepEqual(view("paused"), { pose: "leaning", status: "Listening to you", label: "Mute", hint: "Tap Wren to mute" });
});

test("a mic that's off, or a server that can't hear, tucks Wren's head", () => {
  assert.deepEqual(view("listening", { micOn: false }), { pose: "tucked", status: "Muted", label: "Turn the mic on", hint: "Tap Wren to turn the mic on" });
  assert.deepEqual(view("listening", { hearing: false }), { pose: "tucked", status: "Listening", label: "Turn the mic on", hint: "Tap Wren to turn the mic on" });
});

test("listening cocks its head", () => {
  assert.deepEqual(view("listening"), { pose: "listening", status: "Listening", label: "Mute", hint: "Tap Wren to mute" });
});

test("in a mock interview the hint names the mic, because the mic is the button", () => {
  assert.equal(view("listening", { mock: true }).hint, "Tap the mic to mute");
  assert.equal(view("listening", { micOn: false, mock: true }).hint, "Tap the mic to turn it on");
  assert.equal(view("reconnecting", { mock: true }).hint, "Tap the mic to reconnect");
});
