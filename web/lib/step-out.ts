// The talk page's Step out button, apart from the page, so `node --test` can check it. A tap sends
// STEP_OUT_TEXT and stays pending until the server has answered it. While it's pending, another tap
// does nothing: a second "Step out of the interview." would cut off Wren's "Stepping out." or reach
// Wren as a message of its own.

import type { ClientEvent } from "./voice/client.ts";

/** The words a tap sends. They name the interview, so the server reads them as a pause at any length,
 * even merged onto an answer the user was still giving (voice/app/mock.py, step_out_reason). */
export const STEP_OUT_TEXT = "Step out of the interview.";

/** A tap the server hasn't answered yet, or null. `turn` is the user turn that carried its words, as
 * "epoch:turn", once the server has taken them as one. */
export type PendingStepOut = { turn: string | null } | null;

export const TAPPED: PendingStepOut = { turn: null };

/** A tap is pending until the mock ends (a "mock" event with on: false), the reply to its words ends,
 * or the connection does. A failed reply ends too, on the socket or over POST /turn. The end of an
 * earlier reply, which the tap cut in on, doesn't count, and nor does a reply that gave way because
 * the user kept talking ("continued"): its words carry on into the next turn. */
export function stepOutAfter(pending: PendingStepOut, event: ClientEvent): PendingStepOut {
  if (pending === null) return null;
  switch (event.type) {
    case "user":
      return event.text.includes(STEP_OUT_TEXT) ? { turn: `${event.epoch}:${event.turn}` } : pending;
    case "reply_end":
      return event.outcome !== "continued" && pending.turn === `${event.epoch}:${event.turn}` ? null : pending;
    case "mock":
      return event.on ? pending : null;
    case "ready":
    case "ended":
      return null;
    default:
      return pending;
  }
}
