// What the talk page shows of the conversation, apart from the screen, so `node --test` can check it.
// A turn can hold a line for each speaker: the user, Wren, and a mock interviewer. A reply that
// switches speaker becomes two lines.

import type { ClientEvent } from "./voice/client.ts";
import type { Role } from "./voice/protocol.ts";

export type Speaker = "user" | Role;

/** `label` is what the speaker column shows. A line keeps the label it was spoken under. */
export type Line = { key: string; speaker: Speaker; label: string; text: string; cut: boolean };

/** Whether an interviewer has the floor, and who it is: from the latest "mock" message. */
export type MockView = { on: boolean; name: string | null };

export type Transcript = { lines: Line[]; mock: MockView };

const NO_MOCK: MockView = { on: false, name: null };

export const EMPTY_TRANSCRIPT: Transcript = { lines: [], mock: NO_MOCK };

// The speaker column is 3rem wide and holds six letters, which fits every interviewer name the server
// picks: voice/tests/test_mock.py pins them at six letters or fewer, in
// test_the_spoken_lines_and_names_are_the_agreed_copy. "Interviewer" doesn't fit, so it may break after
// "Inter", at the soft hyphen, U+00AD.
const UNNAMED_INTERVIEWER = "Inter\u00ADviewer";

export function speakerLabel(speaker: Speaker, interviewerName: string | null): string {
  if (speaker === "user") return "You";
  if (speaker === "wren") return "Wren";
  return interviewerName?.trim() || UNNAMED_INTERVIEWER;
}

export function lineKey(speaker: Speaker, epoch: number, turn: number): string {
  return `${speaker}:${epoch}:${turn}`;
}

function newLine(speaker: Speaker, epoch: number, turn: number, text: string, interviewerName: string | null): Line {
  return { key: lineKey(speaker, epoch, turn), speaker, label: speakerLabel(speaker, interviewerName), text, cut: false };
}

/** The client events the transcript follows, and "clear" for a page that starts over (sign-out). */
export type TranscriptAction =
  | Extract<ClientEvent, { type: "user" | "wren" | "drop" | "cut" | "mock" | "ready" | "ended" }>
  | { type: "clear" };

export function transcriptAfter(t: Transcript, action: TranscriptAction): Transcript {
  switch (action.type) {
    case "user":
      return { ...t, lines: [...t.lines, newLine("user", action.epoch, action.turn, action.text, null)] };
    case "wren": {
      // Segments of one speaker's part of a reply run together as one line.
      const key = lineKey(action.role, action.epoch, action.turn);
      const at = t.lines.findIndex((line) => line.key === key);
      if (at === -1) {
        return { ...t, lines: [...t.lines, newLine(action.role, action.epoch, action.turn, action.text, t.mock.name)] };
      }
      const lines = t.lines.slice();
      lines[at] = { ...lines[at], text: `${lines[at].text} ${action.text}` };
      return { ...t, lines };
    }
    case "drop": {
      // That turn merged into the next one: the user hadn't finished.
      const gone = (["user", "wren", "interviewer"] as const).map((speaker) => lineKey(speaker, action.epoch, action.turn));
      return { ...t, lines: t.lines.filter((line) => !gone.includes(line.key)) };
    }
    case "cut": {
      // The user cut in on the reply, whichever of its speakers was talking.
      const cut = (["wren", "interviewer"] as const).map((speaker) => lineKey(speaker, action.epoch, action.turn));
      return { ...t, lines: t.lines.map((line) => (cut.includes(line.key) ? { ...line, cut: true } : line)) };
    }
    case "mock":
      return { ...t, mock: { on: action.on, name: action.name } };
    case "ready":
    case "ended":
      // A new connection starts with no mock: the server says so only if it lands mid-mock, and a
      // switch it undid while the socket was closing never reached the page. A finished client has none.
      return t.mock.on || t.mock.name !== null ? { ...t, mock: NO_MOCK } : t;
    case "clear":
      return EMPTY_TRANSCRIPT;
  }
}
