// What the talk button shows for each voice state: Wren's pose, the status line, the button's label and
// the hint under it. Pure, so `node --test` can check every row (spec: "The bird", Poses).

// The import names its .ts file so `node --test` can load this module, as web/tests do.
import type { VoiceStatus } from "./voice/client.ts";

export type Pose = "perched" | "looking" | "reconnecting" | "speaking" | "thinking" | "leaning" | "tucked" | "listening";

export const STATUS: Record<VoiceStatus, string> = {
  idle: "Not started",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Wren is talking",
  paused: "Listening to you",
  reconnecting: "Reconnecting",
};

export type BirdInput = { status: VoiceStatus; micOn: boolean; hearing: boolean; mock: boolean };
export type BirdView = { pose: Pose; status: string; label: string; hint: string };

// What a tap does, by the button's label. In a mock interview the mic is the button, so the hint names it.
const ACTION: Record<string, { wren: string; mic: string }> = {
  "Start talking": { wren: "start", mic: "start" },
  Reconnect: { wren: "reconnect", mic: "reconnect" },
  "Turn the mic on": { wren: "turn the mic on", mic: "turn it on" },
  Mute: { wren: "mute", mic: "mute" },
};

// The first row that matches wins.
function poseFor({ status, micOn, hearing }: BirdInput): Pose {
  if (status === "idle") return "perched";
  if (status === "reconnecting") return "reconnecting";
  if (status === "connecting") return "looking";
  if (status === "speaking") return "speaking"; // even with the mic off, as when Wren answers a typed message
  if (status === "thinking") return "thinking";
  if (status === "paused") return "leaning"; // Wren stopped for you
  if (!micOn || !hearing) return "tucked";
  return "listening";
}

export function birdView(input: BirdInput): BirdView {
  const { status, micOn, hearing, mock } = input;
  const started = status !== "idle";
  const muted = started && !micOn;
  // The status line and the label are the talk page's from before Perch, so tests and screen readers keep working.
  const label = !started ? "Start talking" : status === "reconnecting" ? "Reconnect" : muted || !hearing ? "Turn the mic on" : "Mute";
  const action = ACTION[label];
  return {
    pose: poseFor(input),
    status: muted ? "Muted" : STATUS[status],
    label,
    hint: mock ? `Tap the mic to ${action.mic}` : `Tap Wren to ${action.wren}`,
  };
}
