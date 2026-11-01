// Messages from the voice server. They mirror voice/app/voice/protocol.py and the design doc's
// "Wire protocol" table. Audio frames carry a 4-byte header: turn and segment, big-endian uint16,
// then 24 kHz mono linear16 samples.

export type Outcome = "complete" | "interrupted" | "continued" | "failed";

/** Who spoke a line. On the wire only the interviewer's segments say so: Wren's carry no role,
 * including the switch lines "Okay, stepping into role." and "Stepping out. It's me again." */
export type Role = "wren" | "interviewer";

export function roleOf(segment: { role?: unknown }): Role {
  return segment.role === "interviewer" ? "interviewer" : "wren";
}

export type TurnMetrics = {
  turn: number;
  mode: string;
  input: "voice" | "text" | "opening";
  outcome: Outcome | null;
  ttfa_ms: number | null;
  endpoint_ms: number | null;
  retrieval_ms: number | null;
  llm_ttft_ms: number | null;
  segment_ms: number | null;
  tts_ttfb_ms: number | null;
  turn_to_audio_ms: number | null;
  speech_end_at: string | null;
  committed_at: string | null;
  first_token_at: string | null;
  first_audio_at: string | null;
  memories: number;
  retrieval_fallback: string | null;
  model: string | null;
  chat_fallback: string | null;
  context_tokens: number | null;
  barge_in_ms: number | null;
  reply_repair: "dropped_tail" | "dropped_stacked" | "dropped_claim" | "dropped_question" | "retried" | "gave_up" | null;
  finish_reason: string | null;
  completion_tokens: number | null;
  // A turn that changed who has the floor: "mock_start", "mock_resume" or "step_out:<why>". ":undone" is
  // added only when a reply on a live socket undid its own switch: it was cut before any of the switch
  // line was heard, the floor hadn't moved on and no other writer had saved a turn since. A crisis
  // step-out is never undone, and a closing socket undoes nothing.
  switch: string | null;
};

export type ServerMessage =
  | { type: "ready"; session_id: string; voice: boolean; memory: string; model: string; resumed: boolean }
  | { type: "stt"; text: string; final: boolean }
  | { type: "user_turn"; turn: number; text: string }
  | { type: "segment"; turn: number; seg: number; text: string; role?: "interviewer" }
  | { type: "segment_end"; turn: number; seg: number; samples: number }
  | { type: "reply_end"; turn: number; outcome: Outcome }
  | { type: "interrupt"; turn: number }
  | { type: "resume"; turn: number }
  | { type: "voice"; available: boolean; reason?: string }
  // `on` is true only while the interviewer has the floor. A stepped-out or paused mock still names
  // its interviewer, with `on: false`. With no mock at all, every field is null. It goes out before
  // each switch line, after an undo, and right after `ready` when a reconnect lands mid-mock.
  | { type: "mock"; on: boolean; name: string | null; title: string | null; company: string | null }
  | ({ type: "metrics" } & TurnMetrics)
  | { type: "error"; code: string; message: string };

// Close codes the voice server uses. After any other close, the page reconnects. 4503 is a
// refusal for now (the age or token check couldn't answer): the page keeps its message and retries.
export const CLOSE_CODES = { badHello: 4400, unauthorized: 4401, sessionOver: 4410, unavailable: 4503 } as const;

/** Whether the page should reconnect after the socket closed with this code. */
export function reconnects(code: number): boolean {
  return code !== CLOSE_CODES.unauthorized && code !== CLOSE_CODES.sessionOver;
}

export function parseServerMessage(text: string): ServerMessage | null {
  try {
    const data = JSON.parse(text);
    return data && typeof data === "object" && typeof data.type === "string" ? (data as ServerMessage) : null;
  } catch {
    return null;
  }
}

export function unpackAudio(buf: ArrayBuffer): { turn: number; seg: number; pcm: Int16Array } {
  if (buf.byteLength < 4) throw new Error("audio frame shorter than its header");
  const view = new DataView(buf);
  return { turn: view.getUint16(0), seg: view.getUint16(2), pcm: new Int16Array(buf, 4, (buf.byteLength - 4) >> 1) };
}
