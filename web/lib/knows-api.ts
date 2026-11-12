// What the "What Wren knows" page reads and changes, through the voice server's /knows routes. The
// browser talks only to the voice server (spec: "The voice server's side"). No React, so `node --test`
// can check it.

// The imports name their .ts files so `node --test` can load this module, as web/tests do.
import { SIGNED_OUT, auth } from "./auth.ts";
import { VOICE_SERVER_URL } from "./voice-server.ts";

export type MemoryKind = "story" | "feedback" | "episode" | "vibe";
/** Where an item came from: its session's mode and start, or, when that session is gone, mode null and
 * when the item was saved. */
export type Source = { mode: "checkin" | "mock" | null; at: string };
export type KnownMemory = { id: string; kind: MemoryKind; text: string; source: Source };
/** Dates are "YYYY-MM-DD" and times "HH:MM", local to the owner. */
export type InterviewFields = { company: string; role: string | null; stage: string; next_date: string | null; next_time: string | null; notes: string | null };
export type KnownInterview = InterviewFields & { id: string; source: Source; moved_from: { next_date: string | null; next_time: string | null }[] };
export type KnownProfile = { display_name: string | null; summary: string; target_roles: string[] };
export type ProfileKey = keyof KnownProfile;
export type Knows = { timezone: string; profile: KnownProfile | null; pipeline: KnownInterview[]; memories: KnownMemory[] };

export type Outcome<T> =
  | { ok: true; value: T }
  | { ok: false; reason: "signed-out" | "refused"; message: string } // 401, or a 403 with a message: the server's words
  | { ok: false; reason: "gone" | "failed" }; // 404; anything else, network errors included

type Options = { base?: string; fetch?: typeof globalThis.fetch; token?: () => Promise<string | null> };

const FAILED = { ok: false, reason: "failed" } as const;
const GONE = { ok: false, reason: "gone" } as const;

async function messageOf(response: Response): Promise<string | null> {
  try {
    const data = await response.json();
    return typeof data?.message === "string" ? data.message : null;
  } catch {
    return null;
  }
}

export class KnowsApi {
  // Plain fields, so `node --test` can strip the types.
  private readonly base: string;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly token: () => Promise<string | null>;

  constructor(options: Options = {}) {
    this.base = options.base ?? VOICE_SERVER_URL;
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.token = options.token ?? (() => auth.getAccessToken());
  }

  load(): Promise<Outcome<Knows>> {
    return this.call("GET", "/knows");
  }

  editMemory(id: string, text: string): Promise<Outcome<KnownMemory>> {
    return this.call("PATCH", `/knows/memories/${encodeURIComponent(id)}`, { text });
  }

  deleteMemory(id: string): Promise<Outcome<null>> {
    return this.call("DELETE", `/knows/memories/${encodeURIComponent(id)}`);
  }

  editInterview(id: string, fields: InterviewFields): Promise<Outcome<KnownInterview>> {
    return this.call("PATCH", `/knows/pipeline/${encodeURIComponent(id)}`, fields);
  }

  deleteInterview(id: string): Promise<Outcome<null>> {
    return this.call("DELETE", `/knows/pipeline/${encodeURIComponent(id)}`);
  }

  editProfile(changes: Partial<KnownProfile>): Promise<Outcome<KnownProfile>> {
    return this.call("PATCH", "/knows/profile", changes);
  }

  private async call<T>(method: string, path: string, body?: object): Promise<Outcome<T>> {
    let token: string | null;
    try {
      token = await this.token();
    } catch {
      return FAILED; // a sign-in that can't be refreshed right now: try again later, don't sign out
    }
    // No token still asks: on the owner's machine the voice server lets the dev identity in.
    const headers: Record<string, string> = {};
    if (token) headers.authorization = `Bearer ${token}`;
    if (body) headers["content-type"] = "application/json";
    let response: Response;
    try {
      response = await this.fetcher(`${this.base}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch {
      return FAILED;
    }
    if (response.status === 204) return { ok: true, value: null as T };
    if (response.ok) {
      try {
        return { ok: true, value: (await response.json()) as T };
      } catch {
        return FAILED;
      }
    }
    if (response.status === 404) return GONE;
    const message = await messageOf(response);
    if (response.status === 401) return { ok: false, reason: "signed-out", message: message ?? SIGNED_OUT };
    if (response.status === 403 && message) return { ok: false, reason: "refused", message };
    return FAILED;
  }
}

/** The page's link to the voice server, with the talk page's sign-in. */
export const knowsApi = new KnowsApi();
