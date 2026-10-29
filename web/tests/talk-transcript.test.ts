import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EMPTY_TRANSCRIPT,
  lineKey,
  speakerLabel,
  transcriptAfter,
  type Transcript,
  type TranscriptAction,
} from "../lib/talk-transcript.ts";

function replay(actions: TranscriptAction[], from: Transcript = EMPTY_TRANSCRIPT): Transcript {
  return actions.reduce(transcriptAfter, from);
}

const user = (epoch: number, turn: number, text: string): TranscriptAction => ({ type: "user", epoch, turn, text });
const wren = (epoch: number, turn: number, text: string): TranscriptAction => ({ type: "wren", epoch, turn, text, role: "wren" });
const interviewer = (epoch: number, turn: number, text: string): TranscriptAction => ({
  type: "wren", epoch, turn, text, role: "interviewer",
});
const mock = (on: boolean, name: string | null): TranscriptAction => ({
  type: "mock", on, name, title: name && "recruiter", company: name && "Sprout Social",
});
const ready: TranscriptAction = { type: "ready", voice: true, memory: "off: test", model: "m" };

const keys = (t: Transcript) => t.lines.map((line) => line.key);

test("a reply that switches speaker becomes two lines, and each keeps its own words", () => {
  const t = replay([
    user(1, 2, "Let's practice."),
    mock(true, "Dana"),
    wren(1, 2, "Okay, stepping into role."),
    interviewer(1, 2, "I'm Dana, the recruiter at Sprout Social."),
    interviewer(1, 2, "Tell me about yourself."),
  ]);
  assert.deepEqual(t.lines.map((line) => [line.key, line.speaker, line.label, line.text]), [
    ["user:1:2", "user", "You", "Let's practice."],
    ["wren:1:2", "wren", "Wren", "Okay, stepping into role."],
    ["interviewer:1:2", "interviewer", "Dana", "I'm Dana, the recruiter at Sprout Social. Tell me about yourself."],
  ]);
});

test("line keys name the speaker, the connection and the turn", () => {
  assert.equal(lineKey("interviewer", 3, 7), "interviewer:3:7");
  const t = replay([wren(1, 2, "First."), wren(2, 2, "Same turn number, new connection.")]);
  assert.equal(t.lines.length, 2, "a new connection numbers its turns again");
});

test("a role line is labeled with the name from the latest mock message", () => {
  const t = replay([mock(true, "Priya"), interviewer(1, 2, "Hi.")]);
  assert.equal(t.lines[0].label, "Priya");
});

test("a line keeps the name it was spoken under when a later mock names someone else", () => {
  const t = replay([
    mock(true, "Dana"),
    interviewer(1, 2, "First mock."),
    mock(false, "Dana"),
    wren(1, 3, "Stepping out."),
    mock(true, "Priya"),
    interviewer(1, 4, "Second mock."),
  ]);
  assert.deepEqual(t.lines.filter((line) => line.speaker === "interviewer").map((line) => line.label), ["Dana", "Priya"]);
});

test("with no name known, a role line says Interviewer", () => {
  const noName = replay([interviewer(1, 2, "Hi.")]);
  assert.equal(noName.lines[0].label, "Inter­viewer"); // a soft hyphen: it may break after "Inter" in the 3rem column
  assert.equal(speakerLabel("interviewer", null).replace("­", ""), "Interviewer");
  assert.equal(speakerLabel("interviewer", ""), speakerLabel("interviewer", null));
  assert.equal(speakerLabel("interviewer", "  "), speakerLabel("interviewer", null));
  assert.equal(speakerLabel("interviewer", " Dana "), "Dana");
  assert.equal(speakerLabel("user", "Dana"), "You");
  assert.equal(speakerLabel("wren", "Dana"), "Wren");
});

test("a mock message sets who has the floor, and a stepped-out mock still names its interviewer", () => {
  assert.deepEqual(replay([mock(true, "Dana")]).mock, { on: true, name: "Dana" });
  assert.deepEqual(replay([mock(true, "Dana"), mock(false, "Dana")]).mock, { on: false, name: "Dana" });
  assert.deepEqual(replay([mock(true, "Dana"), mock(false, null)]).mock, { on: false, name: null });
});

test("a ready means no mock until a mock message says there is one", () => {
  const midMock = replay([mock(true, "Dana"), interviewer(1, 2, "Question one.")]);
  const reconnected = replay([ready], midMock);
  assert.deepEqual(reconnected.mock, { on: false, name: null });
  assert.deepEqual(keys(reconnected), keys(midMock), "the conversation stays");
  assert.deepEqual(replay([mock(true, "Dana")], reconnected).mock, { on: true, name: "Dana" });
});

test("a ready forgets a stepped-out mock too, and old role lines keep their name", () => {
  const t = replay([mock(true, "Dana"), interviewer(1, 2, "Question one."), mock(false, "Dana"), ready]);
  assert.deepEqual(t.mock, { on: false, name: null });
  assert.equal(t.lines[0].label, "Dana");
});

test("a finished client has no mock", () => {
  assert.deepEqual(replay([mock(true, "Dana"), { type: "ended" }]).mock, { on: false, name: null });
});

test("a turn that merged into the next one drops all of its lines", () => {
  const t = replay([
    user(1, 2, "Let's practice."),
    wren(1, 2, "Okay, stepping into role."),
    interviewer(1, 2, "I'm Dana."),
    user(1, 3, "Yes. For Sprout."),
    wren(1, 3, "Stepping out."),
    user(2, 2, "A new connection's turn 2."),
  ]);
  const dropped = replay([{ type: "drop", epoch: 1, turn: 2 }], t);
  assert.deepEqual(keys(dropped), ["user:1:3", "wren:1:3", "user:2:2"]);
});

test("a cut-in marks both of the turn's reply lines, and nothing else", () => {
  const t = replay([
    user(1, 2, "Let's practice."),
    wren(1, 2, "Okay, stepping into role."),
    interviewer(1, 2, "I'm Dana."),
    wren(1, 3, "Another turn."),
  ]);
  const cut = replay([{ type: "cut", epoch: 1, turn: 2 }], t);
  assert.deepEqual(cut.lines.map((line) => [line.key, line.cut]), [
    ["user:1:2", false],
    ["wren:1:2", true],
    ["interviewer:1:2", true],
    ["wren:1:3", false],
  ]);
});

test("clearing empties the conversation and the mock", () => {
  const t = replay([user(1, 2, "Hi."), mock(true, "Dana"), { type: "clear" }]);
  assert.deepEqual(t, EMPTY_TRANSCRIPT);
});

test("events that change nothing hand back the same transcript, so the page doesn't re-render", () => {
  assert.equal(transcriptAfter(EMPTY_TRANSCRIPT, ready), EMPTY_TRANSCRIPT);
  assert.equal(transcriptAfter(EMPTY_TRANSCRIPT, { type: "ended" }), EMPTY_TRANSCRIPT);
});
