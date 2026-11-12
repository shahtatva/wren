import assert from "node:assert/strict";
import { test } from "node:test";

import type { KnownInterview, Knows } from "../lib/knows-api.ts";
import {
  SECTIONS, counts, interviewLine, interviewTitle, memoriesIn, movedFrom, nothingSaved, orderInterviews, parseRoles, profileRows, sourceLabel,
  todayIn, whenLabel,
} from "../lib/knows-view.ts";

// A plain date read as UTC midnight shows the day before in Chicago, so this file runs there (Review Focus 2).
process.env.TZ = "America/Chicago";

const SOURCE = { mode: "checkin" as const, at: "2026-11-10T18:00:00+00:00" };
const EMPTY: Knows = { timezone: "America/Chicago", profile: null, pipeline: [], memories: [] };
const interview = (id: string, next_date: string | null, next_time: string | null = null, moved_from: KnownInterview["moved_from"] = []): KnownInterview => ({
  id, company: "Sprout Social", role: null, stage: "recruiter screen", next_date, next_time, notes: null, source: SOURCE, moved_from,
});

test("this file runs in Chicago", () => {
  assert.equal(new Date(Date.UTC(2026, 10, 14, 12)).getTimezoneOffset(), 360);
});

test("a plain date shows its own day, with the time when there is one", () => {
  assert.equal(whenLabel("2026-11-13", null), "Fri, Nov 13");
  assert.equal(whenLabel("2026-11-13", "14:00"), "Fri, Nov 13 at 2:00 PM");
  assert.equal(whenLabel("2026-11-13", "09:05"), "Fri, Nov 13 at 9:05 AM");
  assert.equal(whenLabel(null, null), null);
});

test("a source shows the owner's local day", () => {
  assert.equal(sourceLabel({ mode: "checkin", at: "2026-11-11T03:00:00+00:00" }, "America/Chicago"), "From a check-in, Nov 10");
  assert.equal(sourceLabel({ mode: "mock", at: "2026-11-09T18:00:00+00:00" }, "America/Chicago"), "From a mock interview, Nov 9");
  assert.equal(sourceLabel({ mode: null, at: "2026-11-10T18:00:00+00:00" }, "Asia/Kolkata"), "Saved Nov 10");
});

test("today is the owner's date, not the machine's", () => {
  assert.equal(todayIn("America/Chicago", new Date("2026-11-11T03:00:00Z")), "2026-11-10");
  assert.equal(todayIn("Asia/Kolkata", new Date("2026-11-10T20:00:00Z")), "2026-11-11");
});

test("interviews: upcoming soonest first, then undated, then past with the latest first", () => {
  const rows = [
    interview("past-old", "2026-11-01"), interview("undated", null), interview("later", "2026-11-20"),
    interview("today-late", "2026-11-11", "15:00"), interview("past-new", "2026-11-09"),
    interview("today-early", "2026-11-11", "09:00"), interview("today-any-time", "2026-11-11"),
  ];
  assert.deepEqual(orderInterviews(rows, "2026-11-11").map((row) => row.id),
    ["today-early", "today-late", "today-any-time", "later", "undated", "past-new", "past-old"]);
});

test("an interview reads as its company and role, then its stage and when", () => {
  assert.equal(interviewTitle({ ...interview("a", null), role: "BDR" }), "Sprout Social, BDR");
  assert.equal(interviewTitle(interview("a", null)), "Sprout Social");
  assert.equal(interviewLine(interview("a", "2026-11-13", "14:00")), "Recruiter screen, Fri, Nov 13 at 2:00 PM");
  assert.equal(interviewLine({ ...interview("a", null), stage: "take-home" }), "Take-home");
});

test("moved from shows only when the date or time changed", () => {
  assert.equal(movedFrom(interview("a", "2026-11-13", "14:00", [{ next_date: "2026-11-12", next_time: "14:00" }])), "Thu, Nov 12 at 2:00 PM");
  assert.equal(movedFrom(interview("a", "2026-11-14", "14:00", [{ next_date: "2026-11-14", next_time: "14:00" }])), null); // a stage change
  assert.equal(movedFrom(interview("a", "2026-11-14", null, [{ next_date: null, next_time: null }])), null); // newly scheduled
  assert.equal(movedFrom(interview("a", "2026-11-14")), null);
});

test("roles split on commas, without blanks or repeats", () => {
  assert.deepEqual(parseRoles(" BDR, , analyst,bdr ,"), ["BDR", "analyst"]);
  assert.deepEqual(parseRoles("   "), []);
});

test("About you lists name, background, roles and the time zone, and only the first three can change", () => {
  const knows = { ...EMPTY, profile: { display_name: null, summary: "MS in Information Management", target_roles: ["BDR", "Analyst"] } };
  assert.deepEqual(profileRows(knows), [
    { key: "display_name", label: "Name", value: null, editable: true },
    { key: "summary", label: "Background", value: "MS in Information Management", editable: true },
    { key: "target_roles", label: "Roles you're aiming for", value: "BDR, Analyst", editable: true },
    { key: "timezone", label: "Time zone", value: "America/Chicago", editable: false },
  ]);
});

test("counts and the not-saving-yet line follow what's there", () => {
  assert.equal(nothingSaved(EMPTY), true);
  const knows: Knows = {
    ...EMPTY, profile: { display_name: "Maya", summary: "", target_roles: [] }, pipeline: [interview("a", null)],
    memories: [{ id: "m", kind: "vibe", text: "Tired after a long week.", source: SOURCE }],
  };
  assert.equal(nothingSaved(knows), false);
  assert.deepEqual(counts(knows), { about: 1, interviews: 1, stories: 0, feedback: 0, moments: 0, lately: 1 });
});

test("sections keep the spec's order, names and kinds", () => {
  assert.deepEqual(SECTIONS.map((s) => [s.id, s.name, s.kind]), [
    ["about", "About you", null], ["interviews", "Interviews", null], ["stories", "Stories", "story"],
    ["feedback", "Feedback", "feedback"], ["moments", "Moments", "episode"], ["lately", "Lately", "vibe"],
  ]);
});

test("each memory section holds its own kind, in the server's order", () => {
  const memory = (id: string, kind: "story" | "feedback") => ({ id, kind, text: `Memory ${id}.`, source: SOURCE });
  const knows: Knows = { ...EMPTY, memories: [memory("f2", "feedback"), memory("s1", "story"), memory("f1", "feedback")] };
  assert.deepEqual(memoriesIn(knows, "feedback").map((m) => m.id), ["f2", "f1"]);
  assert.deepEqual(memoriesIn(knows, "stories").map((m) => m.id), ["s1"]);
  assert.deepEqual(memoriesIn(knows, "moments"), []);
});

test("a later change that keeps the date doesn't hide where the interview moved from", () => {
  const moved = interview("a", "2026-11-13", "14:00", [
    { next_date: "2026-11-13", next_time: "14:00" }, // the stage changed after the move
    { next_date: "2026-11-12", next_time: "14:00" },
  ]);
  assert.equal(movedFrom(moved), "Thu, Nov 12 at 2:00 PM");
});
