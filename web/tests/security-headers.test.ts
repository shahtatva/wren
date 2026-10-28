import assert from "node:assert/strict";
import { test } from "node:test";

import { securityHeaders } from "../next.config.ts";

const RENDER = "https://wren-voice.onrender.com";
const LOCAL = "http://localhost:8000";

function header(headers: { key: string; value: string }[], key: string): string {
  const found = headers.find((h) => h.key === key);
  assert.ok(found, `missing ${key}`);
  return found.value;
}

// "default-src 'self'; frame-ancestors 'none'" -> {"default-src": "'self'", "frame-ancestors": "'none'"}
function policy(voiceServer: string, dev: boolean): Map<string, string> {
  const csp = header(securityHeaders(voiceServer, dev), "Content-Security-Policy");
  return new Map(
    csp
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const [name, ...sources] = part.split(/\s+/);
        return [name, sources.join(" ")];
      }),
  );
}

test("production lets the page reach only itself and the voice server", () => {
  const csp = policy(RENDER, false);
  assert.equal(csp.get("connect-src"), `'self' ${RENDER} wss://wren-voice.onrender.com`);
  assert.ok(csp.has("upgrade-insecure-requests"));
  assert.ok(![...csp.values()].some((sources) => sources.includes("'unsafe-eval'")));
});

test("development allows the local servers and hot reload", () => {
  const csp = policy(LOCAL, true);
  const connect = csp.get("connect-src")?.split(" ") ?? [];
  for (const source of [LOCAL, "ws://localhost:8000", "ws://localhost:3000"]) {
    assert.ok(connect.includes(source), source);
  }
  assert.ok(csp.get("script-src")?.split(" ").includes("'unsafe-eval'"));
  assert.ok(!csp.has("upgrade-insecure-requests"));
});

test("the page can't be framed, and the mic is for this site only", () => {
  assert.equal(policy(RENDER, false).get("frame-ancestors"), "'none'");
  assert.match(header(securityHeaders(RENDER, false), "Permissions-Policy"), /^microphone=\(self\)/);
});

test("the browser doesn't sniff types or send full URLs to other sites", () => {
  const headers = securityHeaders(RENDER, false);
  assert.equal(header(headers, "X-Content-Type-Options"), "nosniff");
  assert.equal(header(headers, "Referrer-Policy"), "strict-origin-when-cross-origin");
});

test("a voice server URL with a path or trailing slash still names its origin", () => {
  const csp = policy(`${RENDER}/`, false);
  assert.equal(csp.get("connect-src"), `'self' ${RENDER} wss://wren-voice.onrender.com`);
});
