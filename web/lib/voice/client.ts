// The browser side of the voice loop: one WebSocket to the voice server, the mic, Wren's voice,
// and barge-in. Design: docs/superpowers/specs/2026-10-16-voice-loop-design.md.
//
// No key ever reaches this file. The browser talks only to the voice server, which holds the
// Deepgram, Groq and Gemini keys. Typed turns go over the socket, or over POST /turn while the
// socket is down, and both run the same pipeline on the server.

import { startMic, type Mic, type MicFrame } from "./mic";
import { Player, type PlayerEvent, type Route } from "./player";
import { parseServerMessage, unpackAudio, type ServerMessage, type TurnMetrics } from "./protocol";
import { Vad } from "./vad";

export const VOICE_SERVER_URL = process.env.NEXT_PUBLIC_VOICE_SERVER_URL ?? "http://localhost:8000";

export type VoiceStatus = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "paused" | "reconnecting";

/** `epoch` counts connections: the server numbers turns from 1 on each one. */
export type ClientEvent =
  | { type: "status"; status: VoiceStatus }
  | { type: "ready"; voice: boolean; memory: string; model: string }
  | { type: "hearing"; available: boolean }
  | { type: "caption"; text: string }
  | { type: "user"; epoch: number; turn: number; text: string }
  | { type: "wren"; epoch: number; turn: number; text: string }
  | { type: "drop"; epoch: number; turn: number }
  | { type: "cut"; epoch: number; turn: number }
  | { type: "metrics"; epoch: number; metrics: TurnMetrics }
  | { type: "notice"; text: string | null };

// What Wren says when something breaks: what happened, and what to do next (wren-voice skill).
export const NOTICES = {
  micDenied: "I can't hear you. Allow the microphone in your browser, or type instead.",
  noVoice: "This browser can't do voice with me. You can still type.",
  lost: "I lost the connection. Tap to reconnect, or keep typing.",
  idle: "I paused listening. Tap the mic when you want to keep talking.",
  deaf: "I can't hear right now. Type instead, and I'll keep answering.",
  unreachable: "I can't reach the voice server right now. Try again in a moment.",
};

const RECONNECT_MS = [1000, 2000, 5000, 10000, 20000];

type Options = {
  mode?: "checkin" | "mock";
  voiceOut?: boolean;
  route?: Route;
  onEvent: (event: ClientEvent) => void;
  onLevel?: (rms: number) => void;
};

export class VoiceClient {
  private ws: WebSocket | null = null;
  private mic: Mic | null = null;
  private player: Player | null = null;
  private readonly vad = new Vad();
  private sessionId: string | null = null;
  private status: VoiceStatus = "idle";
  private epoch = 0;
  private attempts = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private micOn = true;
  private micPending = false; // the mic is still starting; "hearing" waits for it
  private serverHears: boolean | null = null; // speech-to-text on the server, known from "ready"
  private pausing = false;
  private audioTurns = new Set<number>();
  private pendingText: string[] = [];
  private localTurn = 0; // POST /turn replies get negative turn numbers, never a server's
  private readonly tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  // With ?debug=1, the last minute of mic levels, for tuning the barge-in detector.
  private readonly levels: [number, number, boolean, string][] | null =
    typeof window !== "undefined" && new URLSearchParams(window.location.search).has("debug") ? [] : null;

  constructor(private readonly options: Options) {}

  /** Call from a tap: browsers only start audio from a user gesture. */
  async start(): Promise<void> {
    this.stopped = false;
    this.setStatus("connecting");
    if (this.options.voiceOut !== false) {
      try {
        this.player = await Player.create((event) => this.onPlayer(event), this.options.route);
      } catch {
        this.player = null; // Wren still answers, in text
      }
    }
    // Open the socket while the mic starts: the permission prompt and the capture worklet
    // take about a second, and the server can get ready in that time. The player comes
    // first, because the hello says whether Wren's voice can play.
    this.micPending = true;
    this.connect();
    try {
      const mic = await startMic((frame) => this.onMicFrame(frame));
      if (this.stopped) {
        mic.stop();
      } else {
        this.mic = mic;
        if (!this.micOn) mic.setEnabled(false);
        if (this.serverHears) this.notice(null);
      }
    } catch (error) {
      this.mic = null;
      this.notice(error instanceof DOMException && error.name === "NotAllowedError" ? NOTICES.micDenied : NOTICES.noVoice);
    } finally {
      this.micPending = false;
    }
    if (!this.stopped) this.updateHearing();
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.send({ type: "end" });
    this.ws?.close();
    this.ws = null;
    this.mic?.stop();
    this.mic = null;
    this.player?.close();
    this.player = null;
    this.setStatus("idle");
  }

  /** "Tap to reconnect": try now instead of waiting for the next retry. */
  reconnect(): void {
    if (this.ws || this.stopped) return;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.attempts = 0;
    this.connect();
  }

  setMic(on: boolean): void {
    this.micOn = on;
    this.mic?.setEnabled(on);
    this.send({ type: "mic", on });
  }

  async sendText(text: string): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN && this.sessionId) {
      this.send({ type: "user_text", text });
    } else if (this.ws) {
      this.pendingText.push(text); // connecting: send once the session is ready
    } else {
      await this.postTurn(text);
    }
  }

  // --- the socket ---

  private connect(): void {
    const ws = new WebSocket(VOICE_SERVER_URL.replace(/^http/, "ws") + "/ws");
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.epoch++;
    this.audioTurns.clear();
    this.player?.reset();
    ws.onopen = () => {
      this.send({
        type: "hello",
        mode: this.options.mode ?? "checkin",
        tz: this.tz,
        session_id: this.sessionId,
        voice_out: this.player !== null,
      });
    };
    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        this.onAudio(event.data);
      } else {
        const message = parseServerMessage(event.data);
        if (message) this.onMessage(message);
      }
    };
    ws.onclose = (event) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.player?.flush();
      if (this.stopped) return;
      if (event.code === 4401) {
        this.setStatus("idle"); // the server already said why; retrying won't change it
        return;
      }
      this.setStatus("reconnecting");
      this.notice(this.sessionId ? NOTICES.lost : NOTICES.unreachable);
      const delay = RECONNECT_MS[Math.min(this.attempts++, RECONNECT_MS.length - 1)];
      this.retryTimer = setTimeout(() => this.connect(), delay);
    };
  }

  private send(message: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  private onMessage(m: ServerMessage): void {
    const epoch = this.epoch;
    switch (m.type) {
      case "ready":
        this.sessionId = m.session_id;
        this.attempts = 0;
        this.emit({ type: "ready", voice: m.voice, memory: m.memory, model: m.model });
        this.serverHears = m.voice;
        this.updateHearing();
        if (this.mic) this.notice(null);
        this.setStatus("listening");
        for (const text of this.pendingText.splice(0)) this.send({ type: "user_text", text });
        break;
      case "stt":
        this.emit({ type: "caption", text: m.text });
        break;
      case "user_turn":
        this.emit({ type: "caption", text: "" });
        this.emit({ type: "user", epoch, turn: m.turn, text: m.text });
        this.setStatus("thinking");
        break;
      case "segment":
        this.emit({ type: "wren", epoch, turn: m.turn, text: m.text });
        break;
      case "segment_end":
        if (m.samples === 0 || !this.player) {
          // Nothing to play, so it's already "heard" as text.
          this.send({ type: "playback", turn: m.turn, seg: m.seg, event: "start" });
          this.send({ type: "playback", turn: m.turn, seg: m.seg, event: "end" });
        } else {
          this.player.complete(m.turn, m.seg);
        }
        break;
      case "reply_end":
        if (m.outcome === "continued") this.emit({ type: "drop", epoch, turn: m.turn });
        if (!this.audioTurns.has(m.turn) && this.status !== "speaking") this.setStatus("listening");
        break;
      case "interrupt":
        this.player?.flush(m.turn);
        this.pausing = false;
        this.emit({ type: "cut", epoch, turn: m.turn });
        this.setStatus("listening");
        break;
      case "resume":
        this.player?.resume();
        this.pausing = false;
        this.setStatus("speaking");
        break;
      case "voice":
        this.serverHears = m.available;
        this.updateHearing();
        this.notice(m.available ? null : m.reason === "idle" ? NOTICES.idle : NOTICES.deaf);
        break;
      case "metrics":
        this.emit({ type: "metrics", epoch, metrics: m });
        break;
      case "error":
        this.notice(m.message);
        break;
    }
  }

  private onAudio(buf: ArrayBuffer): void {
    if (!this.player) return;
    const { turn, seg, pcm } = unpackAudio(buf);
    this.audioTurns.add(turn);
    this.player.push(turn, seg, pcm);
  }

  // --- Wren's voice ---

  private onPlayer(event: PlayerEvent): void {
    if (event.type === "level") {
      this.options.onLevel?.(event.rms);
    } else if (event.type === "segment-start") {
      this.send({ type: "playback", turn: event.turn, seg: event.seg, event: "start" });
      if (this.status !== "paused") this.setStatus("speaking");
    } else if (event.type === "segment-end") {
      this.send({ type: "playback", turn: event.turn, seg: event.seg, event: "end" });
    } else if (event.type === "drained" && this.status === "speaking") {
      this.setStatus("listening");
    }
  }

  // --- the mic and barge-in ---

  private onMicFrame(frame: MicFrame): void {
    if (this.micOn && this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame.pcm);
    const now = performance.now();
    const audible = this.player?.audible ?? false;
    if (this.levels) {
      this.levels.push([now, frame.rms, audible, this.status]);
      if (this.levels.length > 3000) this.levels.splice(0, 1000);
      (window as unknown as { __wrenLevels?: unknown }).__wrenLevels = this.levels;
    }
    const onset = this.vad.push(frame.rms, now, audible) === "onset";
    if (onset && this.micOn && this.status === "speaking" && !this.pausing && this.player) void this.bargeIn(now);
  }

  /** The user started talking over Wren: stop at once, and let the server confirm with words. */
  private async bargeIn(now: number): Promise<void> {
    this.pausing = true;
    this.setStatus("paused");
    const onsetMs = now - (this.vad.onsetAt ?? now);
    const position = await this.player!.pause();
    if (position) {
      this.send({ type: "barge_in", turn: position.turn, seg: position.seg, played_ms: position.playedMs, onset_ms: onsetMs });
    } else {
      this.pausing = false; // nothing was playing after all
      this.player!.resume();
      this.setStatus("listening");
    }
  }

  // --- text, when the socket is down ---

  private async postTurn(text: string): Promise<void> {
    const turn = --this.localTurn;
    const epoch = this.epoch;
    this.emit({ type: "user", epoch, turn, text });
    this.setStatus("thinking");
    try {
      const response = await fetch(`${VOICE_SERVER_URL}/turn`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, session_id: this.sessionId, mode: this.options.mode ?? "checkin", tz: this.tz }),
      });
      if (!response.ok || !response.body) throw new Error(`POST /turn returned ${response.status}`);
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffered = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffered += value;
        let newline: number;
        while ((newline = buffered.indexOf("\n")) >= 0) {
          const line = buffered.slice(0, newline).trim();
          buffered = buffered.slice(newline + 1);
          if (!line) continue;
          const data = JSON.parse(line);
          if (data.type === "session") this.sessionId = data.session_id;
          else if (data.type === "segment") this.emit({ type: "wren", epoch, turn, text: data.text });
          else if (data.type === "error") this.notice(data.message);
        }
      }
    } catch {
      this.notice(NOTICES.unreachable);
    } finally {
      if (this.status === "thinking") this.setStatus(this.ws ? "listening" : "reconnecting");
    }
  }

  // --- helpers ---

  /** Whether the user can be heard needs both answers: the server's and the mic's. */
  private updateHearing(): void {
    if (this.micPending || this.serverHears === null) return;
    this.emit({ type: "hearing", available: this.serverHears && this.mic !== null });
  }

  private emit(event: ClientEvent): void {
    this.options.onEvent(event);
  }

  private notice(text: string | null): void {
    this.emit({ type: "notice", text });
  }

  private setStatus(status: VoiceStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.emit({ type: "status", status });
  }
}
