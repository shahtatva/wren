import assert from "node:assert/strict";
import { test } from "node:test";

import { TokenUnavailable } from "../lib/auth.ts";
import { KnowsApi } from "../lib/knows-api.ts";

const BASE = "http://voice.test";
const KNOWS = { timezone: "UTC", profile: null, pipeline: [], memories: [] };
type Call = { method: string; url: string; body: unknown; authorization: string | null };

// The voice server's /knows routes: every request answers from `answer`.
function server(answer: (call: Call) => { status: number; body?: unknown }) {
  const calls: Call[] = [];
  const fetch = async (url: string | URL | Request, init: RequestInit = {}) => {
    const call = {
      method: init.method ?? "GET", url: String(url),
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      authorization: new Headers(init.headers).get("authorization"),
    };
    calls.push(call);
    const { status, body } = answer(call);
    return new Response(body === undefined ? null : JSON.stringify(body), { status });
  };
  return { calls, fetch: fetch as typeof globalThis.fetch };
}

const api = (s: ReturnType<typeof server>, token: () => Promise<string | null> = async () => "tok") =>
  new KnowsApi({ base: BASE, fetch: s.fetch, token });

test("load asks with the sign-in token and returns what the server knows", async () => {
  const s = server(() => ({ status: 200, body: KNOWS }));
  assert.deepEqual(await api(s).load(), { ok: true, value: KNOWS });
  assert.deepEqual(s.calls, [{ method: "GET", url: `${BASE}/knows`, body: undefined, authorization: "Bearer tok" }]);
});

test("without a token it still asks, so the dev identity works on this machine", async () => {
  const s = server(() => ({ status: 200, body: KNOWS }));
  await api(s, async () => null).load();
  assert.equal(s.calls[0].authorization, null);
});

test("a 401 is a sign-out and a 403 a refusal, each in the server's words", async () => {
  const out = server(() => ({ status: 401, body: { code: "unauthorized", message: "You're signed out. Sign in to keep going." } }));
  assert.deepEqual(await api(out).load(), { ok: false, reason: "signed-out", message: "You're signed out. Sign in to keep going." });
  const age = server(() => ({ status: 403, body: { code: "age_unconfirmed", message: "I'm built for adults. Confirm you're 18 or older to keep going." } }));
  assert.deepEqual(await api(age).load(), { ok: false, reason: "refused", message: "I'm built for adults. Confirm you're 18 or older to keep going." });
});

test("a token that can't be had right now is a failure, not a sign-out", async () => {
  const s = server(() => ({ status: 200, body: KNOWS }));
  const result = await api(s, async () => {
    throw new TokenUnavailable();
  }).load();
  assert.deepEqual(result, { ok: false, reason: "failed" });
  assert.equal(s.calls.length, 0);
});

test("an unreachable server or a 503 is a failure", async () => {
  const down = new KnowsApi({ base: BASE, token: async () => "tok", fetch: (async () => {
    throw new TypeError("fetch failed");
  }) as typeof globalThis.fetch });
  assert.deepEqual(await down.load(), { ok: false, reason: "failed" });
  const busy = server(() => ({ status: 503, body: { code: "unavailable", message: "I can't reach Wren's memory right now." } }));
  assert.deepEqual(await api(busy).load(), { ok: false, reason: "failed" });
});

test("editMemory patches that memory and returns the saved one", async () => {
  const saved = { id: "m 1", kind: "story", text: "New.", source: { mode: null, at: "2026-11-10T18:00:00+00:00" } };
  const s = server(() => ({ status: 200, body: saved }));
  assert.deepEqual(await api(s).editMemory("m 1", "New."), { ok: true, value: saved });
  assert.deepEqual(s.calls[0], { method: "PATCH", url: `${BASE}/knows/memories/m%201`, body: { text: "New." }, authorization: "Bearer tok" });
});

test("a 404 means it's gone, and a delete's 204 is a success with nothing in it", async () => {
  const gone = server(() => ({ status: 404, body: { code: "not_found", message: "That isn't there anymore." } }));
  assert.deepEqual(await api(gone).deleteMemory("m"), { ok: false, reason: "gone" });
  const s = server(() => ({ status: 204 }));
  assert.deepEqual(await api(s).deleteInterview("i"), { ok: true, value: null });
  assert.deepEqual([s.calls[0].method, s.calls[0].url], ["DELETE", `${BASE}/knows/pipeline/i`]);
});

test("editProfile sends only the fields it's given", async () => {
  const s = server(() => ({ status: 200, body: { display_name: null, summary: "", target_roles: [] } }));
  await api(s).editProfile({ summary: "" });
  assert.deepEqual(s.calls[0].body, { summary: "" });
});
