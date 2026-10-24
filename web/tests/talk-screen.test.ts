import assert from "node:assert/strict";
import { test } from "node:test";

import { screenAfter } from "../lib/talk-screen.ts";

test("an unauthorized refusal goes to sign-in, stops the client and forgets the stored sign-in", () => {
  assert.deepEqual(screenAfter({ type: "refused", code: "unauthorized" }), { screen: "sign-in", stopClient: true, forgetSignIn: true });
});

test("an age refusal goes to the age step and stops the client, keeping the sign-in", () => {
  assert.deepEqual(screenAfter({ type: "refused", code: "age_unconfirmed" }), { screen: "age", stopClient: true, forgetSignIn: false });
});

test("confirming the age goes to the talk screen, and the next tap starts the session", () => {
  // Starting it here would build Wren's audio after an awaited request, outside the tap: iOS can keep it silent.
  assert.deepEqual(screenAfter({ type: "age-confirmed" }), { screen: "talk", stopClient: false, forgetSignIn: false });
});

test("an age step that finds the user signed out goes to sign-in", () => {
  assert.deepEqual(screenAfter({ type: "age-signed-out" }), { screen: "sign-in", stopClient: true, forgetSignIn: true });
});

test("a fresh sign-in goes to the age step only when it's needed", () => {
  assert.equal(screenAfter({ type: "signed-in", needsAge: true }).screen, "age");
  assert.equal(screenAfter({ type: "signed-in", needsAge: false }).screen, "talk");
});

test("asking to sign in from the talk screen stops the session there", () => {
  assert.deepEqual(screenAfter({ type: "sign-in-asked" }), { screen: "sign-in", stopClient: true, forgetSignIn: false });
});
