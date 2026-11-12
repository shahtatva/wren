// The "What Wren knows" page's edits: one item open at a time, one change on its way at a time. Pure,
// so `node --test` can check it. An item's id is a memory's or interview's id, or "profile:<key>".

// The import names its .ts file so `node --test` can load this module, as web/tests do.
import type { SectionId } from "./knows-view.ts";

export type FormState = {
  open: { id: string; mode: "edit" | "confirm" } | null;
  busy: boolean; // a change is on its way to the server
  error: string | null; // shown in the open item
  flash: { section: SectionId; text: string } | null; // what the last change did, in its section
};

export const CLOSED: FormState = { open: null, busy: false, error: null, flash: null };

export type FormAction =
  | { type: "edit"; id: string }
  | { type: "ask-delete" }
  | { type: "cancel" }
  | { type: "submit" }
  | { type: "done"; section: SectionId; text: string }
  | { type: "failed"; message: string }
  | { type: "reset" }; // the page was left: start over

export function formAfter(state: FormState, action: FormAction): FormState {
  if (action.type === "reset") return CLOSED;
  // While a change is on its way, only its answer moves the form: a second tap can't send it twice.
  if (state.busy && action.type !== "done" && action.type !== "failed") return state;
  switch (action.type) {
    case "edit":
      return { open: { id: action.id, mode: "edit" }, busy: false, error: null, flash: null };
    case "ask-delete":
      return state.open ? { ...state, open: { ...state.open, mode: "confirm" }, error: null } : state;
    case "cancel":
      return CLOSED;
    case "submit":
      return state.open ? { ...state, busy: true, error: null } : state;
    case "done":
      return { open: null, busy: false, error: null, flash: { section: action.section, text: action.text } };
    case "failed":
      return { ...state, busy: false, error: action.message };
  }
}
