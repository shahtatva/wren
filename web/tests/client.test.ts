import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { VoiceClient, type ClientEvent } from "../lib/voice/client.ts";
import type { Mic, MicFrame } from "../lib/voice/mic.ts";
import { Player, type PlayerEvent } from "../lib/voice/player.ts";

// The browser's WebSocket, played by hand: the test opens it, feeds it and closes it.
class FakeSocket {
  static OPEN = 1;
  static made: FakeSocket[] = [];
  readyState = 0;
  binaryType = "blob";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeSocket.made.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }

  messages(): { type: string; [key: string]: unknown }[] {
    return this.sent.filter((data): data is string => typeof data === "string").map((data) => JSON.parse(data));
  }
}

// Wren's speaker without Web Audio. pause() reports where playback stopped, as the worklet does.
class FakePlayer {
  calls: string[] = [];
  audible = true;
  emit: (event: PlayerEvent) => void = () => {};

  push() {}
  complete() {}
  async pause() {
    this.calls.push("pause");
    return { turn: 2, seg: 0, playedMs: 120 };
  }
  async position() {
    return { turn: 2, seg: 0, playedMs: 120 };
  }
  resume() {
    this.calls.push("resume");
  }
  flush() {
    this.calls.push("flush");
  }
  reset() {
    this.calls.push("reset");
  }
  close() {
    this.calls.push("close");
  }
}

const undo: (() => void)[] = [];

function swap(target: object, key: string, value: unknown) {
  const host = target as Record<string, unknown>;
  const original = host[key];
  host[key] = value;
  undo.push(() => {
    host[key] = original;
  });
}

afterEach(() => {
  while (undo.length) undo.pop()!();
  FakeSocket.made = [];
});

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function startedClient() {
  const player = new FakePlayer();
  const fetches: string[] = [];
  let frames: (frame: MicFrame) => void = () => {};
  const mic = {
    stopped: false,
    setEnabled() {},
    stop() {
      this.stopped = true;
    },
  };
  swap(globalThis, "WebSocket", FakeSocket);
  swap(globalThis, "fetch", async (url: string) => {
    fetches.push(url);
    return new Response(null, { status: 500 });
  });
  swap(Player, "create", async (onEvent: (event: PlayerEvent) => void) => {
    player.emit = onEvent;
    return player;
  });
  const startMic = async (onFrame: (frame: MicFrame) => void): Promise<Mic> => {
    frames = onFrame;
    return mic;
  };
  const events: ClientEvent[] = [];
  const client = new VoiceClient({ onEvent: (event) => events.push(event), startMic });
  await client.start();
  const socket = FakeSocket.made[0];
  socket.open();
  socket.receive({ type: "ready", session_id: "s1", voice: true, memory: "off: test", model: "m", resumed: false });
  const speakOver = async () => {
    for (let i = 0; i < 6; i++) frames({ pcm: new ArrayBuffer(640), rms: 0.5 }); // 120 ms of loud voice
    await settle();
  };
  return { client, socket, player, mic, events, fetches, speakOver };
}

function lastStatus(events: ClientEvent[]) {
  return events.filter((event) => event.type === "status").at(-1);
}

test("after the session cap, the client lets go of the mic and the session for good", async () => {
  const { client, socket, player, mic, events, fetches } = await startedClient();
  socket.receive({ type: "error", code: "session_over", message: "That's our time for this session." });
  socket.drop(4410);
  assert.ok(events.some((event) => event.type === "ended"), "the page hears that this client is done");
  assert.ok(mic.stopped && player.calls.includes("close"), "the mic and Wren's voice are released");
  assert.deepEqual(events.filter((event) => event.type === "notice").at(-1), {
    type: "notice",
    text: "That's our time for this session.",
  });
  await client.sendText("one more thing");
  assert.deepEqual(fetches, [], "nothing reaches the capped session");
  assert.equal(FakeSocket.made.length, 1, "and nothing reconnects to it");
});

test("a resume for another turn doesn't unpause Wren", async () => {
  const { socket, player, events, speakOver } = await startedClient();
  socket.receive({ type: "user_turn", turn: 2, text: "Feedback please" });
  player.emit({ type: "segment-start", turn: 2, seg: 0 });
  await speakOver();
  assert.deepEqual(lastStatus(events), { type: "status", status: "paused" });
  assert.ok(socket.messages().some((m) => m.type === "barge_in" && m.turn === 2));
  socket.receive({ type: "resume", turn: 1 }); // a late answer about an older turn
  assert.ok(!player.calls.includes("resume"));
  assert.deepEqual(lastStatus(events), { type: "status", status: "paused" });
  socket.receive({ type: "resume", turn: 2 });
  assert.ok(player.calls.includes("resume"));
  assert.deepEqual(lastStatus(events), { type: "status", status: "speaking" });
});

test("a resume that arrives after its turn was interrupted is ignored", async () => {
  const { socket, player, events, speakOver } = await startedClient();
  socket.receive({ type: "user_turn", turn: 2, text: "Feedback please" });
  player.emit({ type: "segment-start", turn: 2, seg: 0 });
  await speakOver();
  socket.receive({ type: "interrupt", turn: 2 }); // the server confirmed from words first
  socket.receive({ type: "resume", turn: 2 }); // its answer to the page's barge_in, already stale
  assert.ok(!player.calls.includes("resume"));
  assert.deepEqual(lastStatus(events), { type: "status", status: "listening" });
});
