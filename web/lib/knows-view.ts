// How the "What Wren knows" page words, orders and dates what Wren knows (spec: "What Wren knows").
// Pure, so `node --test` can check it. Trust-page copy stays plain (wren-voice).

// The import names its .ts file so `node --test` can load this module, as web/tests do.
import type { InterviewFields, KnownInterview, KnownMemory, Knows, MemoryKind, ProfileKey, Source } from "./knows-api.ts";

export type SectionId = "about" | "interviews" | "stories" | "feedback" | "moments" | "lately";
type Section = { id: SectionId; name: string; note: string; kind: MemoryKind | null };

export const SECTIONS: readonly Section[] = [
  { id: "about", name: "About you", note: "Wren keeps these in mind every time you talk.", kind: null },
  { id: "interviews", name: "Interviews", note: "Companies, stages and dates. Wren brings up anything in the next seven days.", kind: null },
  { id: "stories", name: "Stories", note: "The examples you tell in interviews.", kind: "story" },
  { id: "feedback", name: "Feedback", note: "Patterns in how you answer, from mocks and debriefs.", kind: "feedback" },
  { id: "moments", name: "Moments", note: "Things that happened along the way.", kind: "episode" },
  { id: "lately", name: "Lately", note: "How you've said you feel.", kind: "vibe" },
];

export const COPY = {
  intro: "Everything Wren remembers about you. Edit or delete anything. Changes count from your next message.",
  // Until live extraction lands (spec: "Decided by the owner"). The extraction work deletes it.
  notSavingYet: "Wren doesn't save memories from your talks yet, so most of this page stays empty for now. You can fill in About you today.",
  empty: "Nothing here yet.",
  notSet: "Not set",
  loading: "Loading what Wren knows.",
  loadFailed: "Can't reach Wren's memory right now. Try again in a minute.",
  notSaved: "That didn't save. Try again in a minute.",
  saved: "Saved. Wren uses your version from your next message.",
  deletedMemory: "Deleted. Wren won't use it again.",
  deletedInterview: "Deleted. Wren won't bring it up again.",
  cleared: "Cleared. Wren won't use it again.",
  gone: "That one was already deleted.",
  askMemory: "Delete this memory? Wren won't use it again.",
  askInterview: "Delete this interview? Wren won't bring it up again.",
  askClear: "Clear this? Wren won't use it again.",
  rolesHelp: "Separate roles with commas.",
  end: "That's everything Wren remembers.",
} as const;

// Newer ICU puts a narrow no-break space before AM and PM. Plain spaces read and test the same everywhere.
const plain = (text: string) => text.replace(/[  ]/g, " ");

// Interview dates and times are plain local values, so they format in UTC: read as a moment, "2026-11-13"
// would be the evening before anywhere west of Greenwich.
const DAY = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const TIME = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: "UTC" });

/** Today's date in the owner's zone, as "YYYY-MM-DD". */
export function todayIn(tz: string, now: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** "Fri, Nov 13 at 2:00 PM", "Fri, Nov 13", or null with no date. */
export function whenLabel(date: string | null, time: string | null): string | null {
  if (!date) return null;
  const day = plain(DAY.format(new Date(`${date}T00:00:00Z`)));
  return time ? `${day} at ${plain(TIME.format(new Date(`1970-01-01T${time}:00Z`)))}` : day;
}

/** Which talk an item came from, and its day in the owner's zone. */
export function sourceLabel(source: Source, tz: string): string {
  const day = plain(new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: tz }).format(new Date(source.at)));
  if (source.mode === "mock") return `From a mock interview, ${day}`;
  if (source.mode === "checkin") return `From a check-in, ${day}`;
  return `Saved ${day}`;
}

export function interviewTitle(row: InterviewFields): string {
  return row.role ? `${row.company}, ${row.role}` : row.company;
}

/** The stage, capitalized for display only, then when. */
export function interviewLine(row: InterviewFields): string {
  const stage = row.stage.charAt(0).toUpperCase() + row.stage.slice(1);
  const when = whenLabel(row.next_date, row.next_time);
  return when ? `${stage}, ${when}` : stage;
}

/** When the interview was before it moved, or null if the last change didn't move it. */
export function movedFrom(row: KnownInterview): string | null {
  const before = row.moved_from[0];
  if (!before || (before.next_date === row.next_date && before.next_time === row.next_time)) return null;
  return whenLabel(before.next_date, before.next_time);
}

const compare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const byCompany = (a: KnownInterview, b: KnownInterview) => compare(a.company, b.company);
// An interview with no time sorts after every timed one that day.
const moment = (row: KnownInterview) => `${row.next_date} ${row.next_time ?? "99:99"}`;

/** Upcoming soonest first, then undated, then past with the latest first. */
export function orderInterviews(rows: readonly KnownInterview[], today: string): KnownInterview[] {
  const upcoming = rows.filter((row) => row.next_date !== null && row.next_date >= today);
  const undated = rows.filter((row) => row.next_date === null);
  const past = rows.filter((row) => row.next_date !== null && row.next_date < today);
  return [
    ...upcoming.sort((a, b) => compare(moment(a), moment(b)) || byCompany(a, b)),
    ...undated.sort(byCompany),
    ...past.sort((a, b) => compare(moment(b), moment(a)) || byCompany(a, b)),
  ];
}

/** A memory section's memories, in the server's order: newest first. */
export function memoriesIn(knows: Knows, section: SectionId): KnownMemory[] {
  const kind = SECTIONS.find((s) => s.id === section)?.kind;
  return kind ? knows.memories.filter((memory) => memory.kind === kind) : [];
}

export type ProfileRow = { key: ProfileKey | "timezone"; label: string; value: string | null; editable: boolean };

/** About you. The time zone comes from the device at the age step, so it isn't edited here. */
export function profileRows(knows: Knows): ProfileRow[] {
  const profile = knows.profile;
  return [
    { key: "display_name", label: "Name", value: profile?.display_name || null, editable: true },
    { key: "summary", label: "Background", value: profile?.summary || null, editable: true },
    { key: "target_roles", label: "Roles you're aiming for", value: profile?.target_roles.join(", ") || null, editable: true },
    { key: "timezone", label: "Time zone", value: knows.timezone, editable: false },
  ];
}

/** How many items each jump link shows. About you counts the fields that are filled in. */
export function counts(knows: Knows): Record<SectionId, number> {
  const count = (section: SectionId) => {
    if (section === "about") return profileRows(knows).filter((row) => row.editable && row.value !== null).length;
    if (section === "interviews") return knows.pipeline.length;
    return memoriesIn(knows, section).length;
  };
  return Object.fromEntries(SECTIONS.map((section) => [section.id, count(section.id)])) as Record<SectionId, number>;
}

/** Whether the not-saving-yet line shows: only with no memories and no interviews, so it's never wrong beside real ones. */
export function nothingSaved(knows: Knows): boolean {
  return knows.pipeline.length === 0 && knows.memories.length === 0;
}

/** Roles typed as one line, split on commas, without blanks or repeats (in any case). */
export function parseRoles(text: string): string[] {
  const seen = new Set<string>();
  const roles: string[] = [];
  for (const part of text.split(",")) {
    const role = part.trim();
    if (role && !seen.has(role.toLowerCase())) {
      seen.add(role.toLowerCase());
      roles.push(role);
    }
  }
  return roles;
}
