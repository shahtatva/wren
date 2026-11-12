import assert from "node:assert/strict";
import { test } from "node:test";

import { CLOSED, formAfter, type FormAction } from "../lib/knows-form.ts";

const replay = (actions: FormAction[], from = CLOSED) => actions.reduce(formAfter, from);
const NOT_SAVED = "That didn't save. Try again in a minute.";

test("one item opens at a time, and opening one clears the last flash", () => {
  const state = replay([{ type: "edit", id: "a" }, { type: "submit" }, { type: "done", section: "stories", text: "Saved." }, { type: "edit", id: "b" }]);
  assert.deepEqual(state, { open: { id: "b", mode: "edit" }, busy: false, error: null, flash: null });
});

test("while a change is on its way, every other action waits", () => {
  const busy = replay([{ type: "edit", id: "a" }, { type: "submit" }]);
  for (const action of [{ type: "submit" }, { type: "edit", id: "b" }, { type: "cancel" }, { type: "ask-delete" }] as FormAction[]) {
    assert.equal(formAfter(busy, action), busy);
  }
});

test("Delete asks first, and Cancel closes the item", () => {
  const asking = replay([{ type: "edit", id: "a" }, { type: "ask-delete" }]);
  assert.deepEqual(asking.open, { id: "a", mode: "confirm" });
  assert.deepEqual(formAfter(asking, { type: "cancel" }), CLOSED);
});

test("a failed change keeps the item open with the error", () => {
  const state = replay([{ type: "edit", id: "a" }, { type: "submit" }, { type: "failed", message: NOT_SAVED }]);
  assert.deepEqual(state, { open: { id: "a", mode: "edit" }, busy: false, error: NOT_SAVED, flash: null });
});

test("a finished change closes the item and says what happened in its section", () => {
  const state = replay([
    { type: "edit", id: "a" }, { type: "ask-delete" }, { type: "submit" },
    { type: "done", section: "feedback", text: "Deleted. Wren won't use it again." },
  ]);
  assert.deepEqual(state, { open: null, busy: false, error: null, flash: { section: "feedback", text: "Deleted. Wren won't use it again." } });
});

test("with nothing open there's nothing to submit or delete", () => {
  assert.equal(formAfter(CLOSED, { type: "submit" }), CLOSED);
  assert.equal(formAfter(CLOSED, { type: "ask-delete" }), CLOSED);
});

test("leaving the page resets it", () => {
  assert.deepEqual(formAfter(replay([{ type: "edit", id: "a" }, { type: "submit" }]), { type: "reset" }), CLOSED);
});
