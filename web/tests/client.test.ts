import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";

import { Auth } from "../lib/auth.ts";
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

type Started = { getToken?: () => Promise<string | null>; fetch?: (url: string, init?: RequestInit) => Promise<Response> };

async function startedClient(options: Started = {}) {
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
  swap(globalThis, "fetch", async (url: string, init?: RequestInit) => {
    fetches.push(url);
    return options.fetch ? options.fetch(url, init) : new Response(null, { status: 500 });
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
  const client = new VoiceClient({ onEvent: (event) => events.push(event), startMic, getToken: options.getToken });
  await client.start();
  const socket = FakeSocket.made[0];
  socket.open();
  socket.receive({ type: "ready", session_id: "s1", voice: true, memory: "off: test", model: "m", resumed: false });
  const hear = (rms: number) => frames({ pcm: new ArrayBuffer(640), rms });
  const speakOver = async () => {
    for (let i = 0; i < 150; i++) hear(0.001); // 3 s of a quiet room while Wren talks, as on a real call
    for (let i = 0; i < 12; i++) hear(0.5); // then 240 ms of loud voice
    await settle();
  };
  return { client, socket, player, mic, events, fetches, speakOver, hear };
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

test("room talk before and during the opening doesn't pause Wren", async () => {
  let clock = 0;
  swap(performance, "now", () => clock);
  const { socket, player, hear, speakOver } = await startedClient();
  const talk = (frames: number) => {
    for (let i = 0; i < frames; i++) {
      clock += 20;
      hear(i % 25 < 15 ? 0.04 : 0.002); // people nearby taking turns: 0.3 s of voice, a 0.2 s gap
    }
  };
  player.audible = false; // the opening is still being prepared
  talk(100);
  player.audible = true;
  player.emit({ type: "segment-start", turn: 1, seg: 0 });
  talk(400);
  await settle();
  const bargeIns = () => socket.messages().filter((message) => message.type === "barge_in").length;
  assert.equal(bargeIns(), 0, "Wren talks through the room");
  await speakOver();
  assert.equal(bargeIns(), 1, "and still stops for the owner's voice");
});

test("cutting in again and again keeps working", async () => {
  let clock = 0;
  swap(performance, "now", () => clock);
  const { socket, player, hear } = await startedClient();
  const say = (rms: number, frames: number) => {
    for (let i = 0; i < frames; i++) {
      clock += 20;
      hear(rms);
    }
  };
  const bargeIns = () => socket.messages().filter((message) => message.type === "barge_in").length;
  for (let turn = 2; turn <= 5; turn++) {
    socket.receive({ type: "user_turn", turn, text: "Go on" });
    player.emit({ type: "segment-start", turn, seg: 0 });
    say(0.001, turn === 2 ? 150 : 50); // Wren talks in a quiet room
    say(0.3, 60); // the owner cuts in and keeps talking until the server confirms
    await settle();
    socket.receive({ type: "interrupt", turn });
    assert.equal(bargeIns(), turn - 1, `cut-in ${turn - 1} paused Wren`);
  }
});

test("a mic that starts after the opening doesn't take the owner's first turn for the room", async () => {
  let clock = 0;
  swap(performance, "now", () => clock);
  const { socket, player, hear } = await startedClient();
  const say = (rms: number, frames: number) => {
    for (let i = 0; i < frames; i++) {
      clock += 20;
      hear(rms);
    }
  };
  player.audible = false;
  socket.receive({ type: "segment", turn: 1, seg: 0, text: "Hi, I'm Wren, an AI." }); // played before the mic started
  say(0.3, 100); // the owner's first turn
  socket.receive({ type: "user_turn", turn: 2, text: "Sprout moved my screen" });
  player.audible = true;
  player.emit({ type: "segment-start", turn: 2, seg: 0 });
  say(0.001, 150);
  say(0.3, 15);
  await settle();
  assert.equal(socket.messages().filter((message) => message.type === "barge_in").length, 1);
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

// A real macrotask: mock.timers fakes setTimeout and Date, not setImmediate.
async function realTick(times = 5) {
  for (let i = 0; i < times; i++) await new Promise((resolve) => setImmediate(resolve));
}

function fakeTimers(apis: ("setTimeout" | "Date")[]) {
  mock.timers.enable({ apis, now: 0 });
  undo.push(() => mock.timers.reset());
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function hello(socket: FakeSocket) {
  return socket.messages().find((message) => message.type === "hello");
}

test("refreshes before reconnecting when the token is near expiry", async () => {
  fakeTimers(["setTimeout", "Date"]);
  const tokens = (access: string, refresh: string) => ({ access_token: access, refresh_token: refresh, expires_in: 3600 });
  const voiceServer = async (url: string) =>
    url.endsWith("/auth/signin") ? json(200, { ...tokens("access-1", "refresh-1"), age_confirmed: true })
    : url.endsWith("/auth/refresh") ? json(200, tokens("access-2", "refresh-2"))
    : json(404, {});
  const storage = new Map<string, string>();
  const auth = new Auth({
    base: "http://voice.test",
    fetch: voiceServer as typeof globalThis.fetch,
    storage: () => ({ getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => void storage.set(k, v), removeItem: (k: string) => void storage.delete(k) }),
  });
  await auth.signIn("maya@example.com", "correct horse battery staple");
  const { socket } = await startedClient({ getToken: () => auth.getAccessToken() });
  await realTick();
  assert.equal(hello(socket)?.token, "access-1");
  mock.timers.tick(3600_000 - 30_000); // the token has 30 seconds left
  socket.drop(1006);
  mock.timers.tick(1000); // the first reconnect backoff
  const again = FakeSocket.made[1];
  again.open();
  await realTick();
  assert.equal(hello(again)?.token, "access-2");
});

test("a 4503 refusal keeps the server's message and retries", async () => {
  fakeTimers(["setTimeout"]);
  const { socket, events } = await startedClient();
  const message = "I can't check your account right now. I'll keep trying.";
  socket.receive({ type: "error", code: "age_check_failed", message });
  socket.drop(4503);
  assert.deepEqual(events.filter((event) => event.type === "notice").at(-1), { type: "notice", text: message });
  assert.ok(!events.some((event) => event.type === "ended" || event.type === "refused"));
  mock.timers.tick(1000);
  assert.equal(FakeSocket.made.length, 2, "it reconnects with the usual backoff");
});

test("a 4401 age refusal tells the page to show the age step", async () => {
  const { socket, events } = await startedClient();
  socket.receive({ type: "error", code: "age_unconfirmed", message: "I'm built for adults. Confirm you're 18 or older to keep going." });
  socket.drop(4401);
  assert.ok(events.some((event) => event.type === "refused" && event.code === "age_unconfirmed"));
  assert.ok(events.some((event) => event.type === "ended"));
});

// Render's proxy loses any close the server starts and cuts the connection about 20 s later,
// so the page has to act on the server's last message, not on its close code.

test("a refusal ends the session even when its close code never arrives", async () => {
  fakeTimers(["setTimeout"]);
  const { socket, mic, events } = await startedClient();
  socket.receive({ type: "error", code: "unauthorized", message: "You're signed out. Sign in to keep going." });
  assert.ok(events.some((event) => event.type === "refused" && event.code === "unauthorized"), "the page goes to sign-in");
  assert.ok(events.some((event) => event.type === "ended"));
  assert.ok(mic.stopped, "the mic is off behind the sign-in screen");
  assert.equal(socket.readyState, 3, "the page closes the socket itself");
  socket.drop(1006);
  mock.timers.tick(30_000);
  assert.equal(FakeSocket.made.length, 1, "and never reconnects");
});

test("the session cap ends the session even when its close arrives as a dropped connection", async () => {
  fakeTimers(["setTimeout"]);
  const { socket, events } = await startedClient();
  socket.receive({ type: "error", code: "session_over", message: "That's our time for this session." });
  socket.drop(1006);
  mock.timers.tick(30_000);
  assert.ok(events.some((event) => event.type === "ended"));
  assert.equal(FakeSocket.made.length, 1, "a capped session isn't restarted behind the user's back");
});

test("a refusal that will pass retries without waiting for its close", async () => {
  fakeTimers(["setTimeout"]);
  const { socket, events } = await startedClient();
  const message = "I can't check your account right now. I'll keep trying.";
  socket.receive({ type: "error", code: "age_check_failed", message });
  assert.equal(socket.readyState, 3, "the page lets go of the socket at once");
  mock.timers.tick(1000);
  assert.equal(FakeSocket.made.length, 2, "it reconnects on the usual first backoff, not 20 s later");
  assert.deepEqual(events.filter((event) => event.type === "notice").at(-1), { type: "notice", text: message });
  assert.ok(!events.some((event) => event.type === "ended" || event.type === "refused"));
});

test("a failed reply keeps the session going", async () => {
  fakeTimers(["setTimeout"]);
  const { socket, events } = await startedClient();
  socket.receive({ type: "error", code: "reply_failed", message: "I lost my train of thought. Could you say that again?" });
  mock.timers.tick(30_000);
  assert.equal(socket.readyState, FakeSocket.OPEN, "the server doesn't close after this one");
  assert.ok(!events.some((event) => event.type === "ended" || event.type === "refused"));
});

test("a POST /turn refusal shows the server's message", async () => {
  fakeTimers(["setTimeout"]); // the dropped socket's reconnect never fires
  const message = "I'm built for adults. Confirm you're 18 or older to keep going.";
  const { client, socket, events, fetches } = await startedClient({
    getToken: async () => "access-1",
    fetch: async () => json(403, { code: "age_unconfirmed", message }),
  });
  socket.drop(1006); // the socket is down, so typed text goes over POST /turn
  await client.sendText("hello");
  assert.ok(fetches.at(-1)?.endsWith("/turn"));
  assert.deepEqual(events.filter((event) => event.type === "notice").at(-1), { type: "notice", text: message });
  assert.ok(events.some((event) => event.type === "refused" && event.code === "age_unconfirmed"));
});

test("a refresh that fails at reconnect keeps the sign-in and retries", async () => {
  fakeTimers(["setTimeout"]);
  const answers: (() => string)[] = [
    () => "access-1",
    () => {
      throw new Error("no token right now"); // a sign-in, but the refresh failed for now
    },
    () => "access-2",
  ];
  const { socket, events } = await startedClient({ getToken: async () => answers.shift()!() });
  await realTick();
  assert.equal(hello(socket)?.token, "access-1");
  socket.drop(1006);
  mock.timers.tick(1000);
  const second = FakeSocket.made[1];
  second.open();
  await realTick();
  assert.equal(hello(second), undefined, "no hello without a token: it would be refused, or let in as the dev identity");
  assert.deepEqual(events.filter((event) => event.type === "notice").at(-1), {
    type: "notice",
    text: "I can't reach sign-in right now. I'll keep trying.",
  });
  assert.ok(!events.some((event) => event.type === "refused" || event.type === "ended"), "still signed in");
  mock.timers.tick(2000); // the second backoff
  const third = FakeSocket.made[2];
  third.open();
  await realTick();
  assert.equal(hello(third)?.token, "access-2");
});

test("typed text waits when there's a sign-in but no token right now", async () => {
  fakeTimers(["setTimeout"]);
  let calls = 0;
  const { client, socket, events, fetches } = await startedClient({
    getToken: async () => {
      if (calls++ === 0) return "access-1";
      throw new Error("no token right now");
    },
  });
  await realTick();
  socket.drop(1006); // typed text now goes over POST /turn
  await client.sendText("hello");
  assert.ok(!fetches.some((url) => url.endsWith("/turn")), "nothing is posted without a token");
  assert.deepEqual(events.filter((event) => event.type === "notice").at(-1), {
    type: "notice",
    text: "I can't reach sign-in right now. Try again in a minute.",
  });
});

test("a mock message reaches the page", async () => {
  const { socket, events } = await startedClient();
  socket.receive({ type: "mock", on: true, name: "Dana", title: "recruiter", company: "Sprout Social" });
  assert.deepEqual(events.filter((event) => event.type === "mock").at(-1), {
    type: "mock", on: true, name: "Dana", title: "recruiter", company: "Sprout Social",
  });
});

test("segments spoken in role carry their role", async () => {
  const { socket, events } = await startedClient();
  socket.receive({ type: "segment", turn: 2, seg: 0, text: "Okay, stepping into role." });
  socket.receive({ type: "segment", turn: 2, seg: 1, text: "I'm Dana.", role: "interviewer" });
  const wren = events.filter((event) => event.type === "wren");
  assert.deepEqual(wren.map((event) => event.role), ["wren", "interviewer"]);
});

test("the end of a reply reaches the page, with its turn and outcome", async () => {
  const { socket, events } = await startedClient();
  socket.receive({ type: "reply_end", turn: 2, outcome: "interrupted" });
  assert.deepEqual(events.filter((event) => event.type === "reply_end"), [
    { type: "reply_end", epoch: 1, turn: 2, outcome: "interrupted" },
  ]);
});

test("a reply over POST /turn ends too, failed or not", async () => {
  fakeTimers(["setTimeout"]);
  const ndjson = [
    { type: "session", session_id: "s1", memory: "off: test" },
    { type: "segment", text: "Sure." },
    { type: "end", metrics: { outcome: "complete" } },
  ].map((line) => JSON.stringify(line) + "\n").join(""); // the server ends every line, the last one too
  const answers = [new Response(ndjson, { status: 200 }), new Response(null, { status: 500 })];
  const { client, socket, events } = await startedClient({ fetch: async () => answers.shift()! });
  socket.drop(1006);
  await client.sendText("Step out of the interview.");
  await client.sendText("Hello?");
  const turns = events.filter((event) => event.type === "user").map((event) => event.turn);
  assert.deepEqual(events.filter((event) => event.type === "reply_end"), [
    { type: "reply_end", epoch: 1, turn: turns[0], outcome: "complete" },
    { type: "reply_end", epoch: 1, turn: turns[1], outcome: "failed" },
  ]);
});

test("POST /turn passes the mock and the role on too", async () => {
  fakeTimers(["setTimeout"]);
  const ndjson = [
    { type: "session", session_id: "s1", memory: "off: test" },
    { type: "mock", on: true, name: "Dana", title: "recruiter", company: "Sprout Social" },
    { type: "segment", text: "Okay, stepping into role." },
    { type: "segment", text: "I'm Dana.", role: "interviewer" },
    { type: "end", metrics: null },
  ].map((line) => JSON.stringify(line)).join("\n");
  const { client, socket, events } = await startedClient({ fetch: async () => new Response(ndjson, { status: 200 }) });
  socket.drop(1006);
  await client.sendText("Let's practice.");
  assert.ok(events.some((event) => event.type === "mock" && event.on));
  assert.deepEqual(events.filter((event) => event.type === "wren").map((event) => event.role), ["wren", "interviewer"]);
});
