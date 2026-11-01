import assert from "node:assert/strict";
import { test } from "node:test";

import { STEP_OUT_TEXT, TAPPED, stepOutAfter, type PendingStepOut } from "../lib/step-out.ts";
import type { ClientEvent } from "../lib/voice/client.ts";

function replay(events: ClientEvent[], from: PendingStepOut = TAPPED): PendingStepOut {
  return events.reduce(stepOutAfter, from);
}

const user = (epoch: number, turn: number, text: string): ClientEvent => ({ type: "user", epoch, turn, text });
const end = (epoch: number, turn: number, outcome: "complete" | "interrupted" | "continued" | "failed" = "complete"): ClientEvent => ({
  type: "reply_end", epoch, turn, outcome,
});
const mock = (on: boolean): ClientEvent => ({ type: "mock", on, name: "Dana", title: "recruiter", company: "Sprout Social" });

test("the button sends words that name the interview", () => {
  // They step out at any length, even merged onto an answer: voice/tests/test_mock.py pins how the server reads them.
  assert.equal(STEP_OUT_TEXT, "Step out of the interview.");
});

test("a tap is pending until the mock ends", () => {
  assert.notEqual(TAPPED, null);
  const pending = replay([user(1, 4, STEP_OUT_TEXT)]);
  assert.notEqual(pending, null);
  assert.equal(replay([mock(false)], pending), null);
  assert.equal(replay([mock(true)], pending), pending, "an undone step-out puts the interviewer back, and the tap waits on");
});

test("a tap is pending until the reply to its words ends", () => {
  assert.equal(replay([user(1, 4, STEP_OUT_TEXT), end(1, 4)]), null);
  assert.equal(replay([user(1, 4, STEP_OUT_TEXT), end(1, 4, "failed")]), null, "a failed reply ends it too");
  assert.equal(replay([user(1, 4, STEP_OUT_TEXT), end(1, 4, "interrupted")]), null);
  // POST /turn numbers its turns below zero.
  assert.equal(replay([user(1, -1, STEP_OUT_TEXT), end(1, -1, "failed")]), null);
});

test("the end of a reply the tap cut in on doesn't end it", () => {
  // Tapped while Wren spoke: the server ends that reply first, then takes the tap's words as a new turn.
  const pending = replay([end(1, 3, "interrupted"), user(1, 4, STEP_OUT_TEXT)]);
  assert.notEqual(pending, null);
  assert.equal(replay([end(1, 3)], pending), pending, "an older turn's end");
  assert.equal(replay([end(2, 4)], pending), pending, "another connection's turn 4");
});

test("words merged into the next turn are still waiting on a reply", () => {
  const merged = replay([user(1, 4, STEP_OUT_TEXT), end(1, 4, "continued"), user(1, 5, `So, yes. ${STEP_OUT_TEXT}`)]);
  assert.notEqual(merged, null);
  assert.equal(replay([end(1, 5)], merged), null);
});

test("a new connection or a finished client lets go of a tap", () => {
  assert.equal(replay([{ type: "ready", voice: true, memory: "off: test", model: "m" }]), null);
  assert.equal(replay([{ type: "ended" }]), null);
});

test("other events change nothing, and with no tap there's nothing to end", () => {
  const pending = replay([user(1, 4, STEP_OUT_TEXT)]);
  assert.equal(replay([user(1, 5, "Something else."), { type: "status", status: "speaking" }], pending), pending);
  assert.equal(replay([user(1, 4, STEP_OUT_TEXT), end(1, 4), mock(true)], null), null);
});
