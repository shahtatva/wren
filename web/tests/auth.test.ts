import assert from "node:assert/strict";
import { test } from "node:test";

import { Auth } from "../lib/auth.ts";
import { START_FORM, signInForm } from "../lib/sign-in-form.ts";

const BASE = "http://voice.test";
const EMAIL = "maya@example.com";
const PASSWORD = "correct horse battery staple";

// localStorage, kept in a Map.
class FakeStorage {
  items = new Map<string, string>();
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, String(value));
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
}

type Answer = { status: number; body?: unknown };
type Call = { url: string; body: unknown; authorization: string | null };

// The voice server's auth routes: each path answers from a function of the request body.
function voiceServer(routes: Record<string, (body: unknown) => Answer>) {
  const calls: Call[] = [];
  const fetch = async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, body, authorization: new Headers(init.headers).get("authorization") });
    const answer = routes[new URL(url).pathname]?.(body) ?? { status: 404, body: { detail: "Not Found" } };
    const text = answer.body === undefined ? null : JSON.stringify(answer.body);
    return new Response(text, { status: answer.status, headers: { "content-type": "application/json" } });
  };
  return { calls, fetch: fetch as typeof globalThis.fetch };
}

function tokens(access: string, refresh: string, expiresIn = 3600) {
  return { access_token: access, refresh_token: refresh, expires_in: expiresIn };
}

function signedInServer(ageConfirmed: boolean | null = true, extra: Record<string, (body: unknown) => Answer> = {}) {
  return voiceServer({ "/auth/signin": () => ({ status: 200, body: { ...tokens("access-1", "refresh-1"), age_confirmed: ageConfirmed } }), ...extra });
}

function makeAuth(server: { fetch: typeof globalThis.fetch }, storage = new FakeStorage(), now = () => 0) {
  return { auth: new Auth({ base: BASE, fetch: server.fetch, storage: () => storage, now }), storage };
}

test("stores the refresh token but keeps the access token in memory, and never stores the password", async () => {
  const server = signedInServer();
  const { auth, storage } = makeAuth(server);
  assert.deepEqual(await auth.signIn(EMAIL, PASSWORD), { ok: true, needsAge: false });
  assert.deepEqual([...storage.items], [["wren.refresh", "refresh-1"]]);
  assert.equal(await auth.getAccessToken(), "access-1");
  assert.deepEqual(server.calls.map((call) => call.url), [`${BASE}/auth/signin`]); // no refresh while it's fresh
  assert.deepEqual(server.calls[0].body, { email: EMAIL, password: PASSWORD });
});

test("a sign-in that reports age_confirmed: false leads to the age step", async () => {
  const notYet = makeAuth(signedInServer(false)).auth;
  assert.deepEqual(await notYet.signIn(EMAIL, PASSWORD), { ok: true, needsAge: true });
  const unknown = makeAuth(signedInServer(null)).auth; // the database couldn't say: the hello decides
  assert.deepEqual(await unknown.signIn(EMAIL, PASSWORD), { ok: true, needsAge: false });
});

test("an age_unconfirmed refusal shows the age step, and confirming retries: confirmAge posts {tz} with the bearer token", async () => {
  const server = signedInServer(false, { "/auth/age": () => ({ status: 204 }) });
  const { auth } = makeAuth(server);
  await auth.signIn(EMAIL, PASSWORD);
  assert.deepEqual(await auth.confirmAge("America/Chicago"), { ok: true });
  const age = server.calls.at(-1)!;
  assert.deepEqual([age.url, age.body, age.authorization], [`${BASE}/auth/age`, { tz: "America/Chicago" }, "Bearer access-1"]);
});

test("a 429 shows the throttle copy and keeps the email", async () => {
  const throttled = "Too many tries. Wait a few minutes, then try again.";
  const server = voiceServer({ "/auth/signin": () => ({ status: 429, body: { code: "throttled", message: throttled } }) });
  const { auth, storage } = makeAuth(server);
  let form = signInForm(START_FORM, { type: "email", value: EMAIL });
  form = signInForm(form, { type: "password", value: "not it" });
  form = signInForm(form, { type: "submit" });
  assert.equal(form.busy, true);
  const result = await auth.signIn(form.email, form.password);
  assert.deepEqual(result, { ok: false, code: "throttled", message: throttled });
  form = signInForm(form, { type: "refused", message: result.ok ? "" : result.message });
  assert.deepEqual(form, { email: EMAIL, password: "", busy: false, error: throttled });
  assert.equal(storage.items.size, 0);
});

test("a sign-in that can't reach the voice server says so", async () => {
  const unreachable = async () => {
    throw new TypeError("fetch failed");
  };
  const { auth } = makeAuth({ fetch: unreachable as typeof globalThis.fetch });
  const result = await auth.signIn(EMAIL, PASSWORD);
  assert.equal(result.ok, false);
  assert.match(result.ok ? "" : result.message, /can't reach sign-in/);
});

test("signing out calls /auth/signout and clears storage", async () => {
  const server = signedInServer(true, { "/auth/signout": () => ({ status: 204 }) });
  const { auth, storage } = makeAuth(server);
  await auth.signIn(EMAIL, PASSWORD);
  await auth.signOut();
  const out = server.calls.at(-1)!;
  assert.deepEqual([out.url, out.authorization], [`${BASE}/auth/signout`, "Bearer access-1"]);
  assert.equal(storage.items.size, 0);
  assert.equal(auth.hasSession(), false);
  assert.equal(await auth.getAccessToken(), null);
  assert.equal(server.calls.length, 2); // with nothing stored, there's nothing to refresh
});

test("a token near expiry is refreshed once, even when two callers ask at the same moment", async () => {
  let now = 0;
  const server = signedInServer(true, {
    "/auth/refresh": (body) =>
      JSON.stringify(body) === JSON.stringify({ refresh_token: "refresh-1" })
        ? { status: 200, body: tokens("access-2", "refresh-2") }
        : { status: 401, body: { code: "unauthorized", message: "You've been signed out. Sign in again to keep going." } },
  });
  const { auth, storage } = makeAuth(server, new FakeStorage(), () => now);
  await auth.signIn(EMAIL, PASSWORD);
  now = 3600_000 - 30_000; // 30 seconds left
  assert.deepEqual(await Promise.all([auth.getAccessToken(), auth.getAccessToken()]), ["access-2", "access-2"]);
  assert.equal(server.calls.filter((call) => call.url === `${BASE}/auth/refresh`).length, 1);
  assert.equal(storage.getItem("wren.refresh"), "refresh-2");
});

test("a reload stays signed in through the stored refresh token", async () => {
  const storage = new FakeStorage();
  storage.setItem("wren.refresh", "refresh-1");
  const server = voiceServer({ "/auth/refresh": () => ({ status: 200, body: tokens("access-2", "refresh-2") }) });
  const { auth } = makeAuth(server, storage);
  assert.equal(auth.hasSession(), true);
  assert.equal(await auth.getAccessToken(), "access-2");
});

test("a refresh token the server refuses signs the page out", async () => {
  const storage = new FakeStorage();
  storage.setItem("wren.refresh", "refresh-0");
  const server = voiceServer({ "/auth/refresh": () => ({ status: 401, body: { code: "unauthorized", message: "You've been signed out. Sign in again to keep going." } }) });
  const { auth } = makeAuth(server, storage);
  assert.equal(await auth.getAccessToken(), null);
  assert.equal(auth.hasSession(), false);
  assert.equal(storage.items.size, 0);
});
