// Messages from the voice server. They mirror voice/app/voice/protocol.py and the design doc's
// "Wire protocol" table. Audio frames carry a 4-byte header: turn and segment, big-endian uint16,
// then 24 kHz mono linear16 samples.

export type Outcome = "complete" | "interrupted" | "continued" | "failed";

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
};

export type ServerMessage =
  | { type: "ready"; session_id: string; voice: boolean; memory: string; model: string; resumed: boolean }
  | { type: "stt"; text: string; final: boolean }
  | { type: "user_turn"; turn: number; text: string }
  | { type: "segment"; turn: number; seg: number; text: string }
  | { type: "segment_end"; turn: number; seg: number; samples: number }
  | { type: "reply_end"; turn: number; outcome: Outcome }
  | { type: "interrupt"; turn: number }
  | { type: "resume"; turn: number }
  | { type: "voice"; available: boolean; reason?: string }
  | ({ type: "metrics" } & TurnMetrics)
  | { type: "error"; code: string; message: string };

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
