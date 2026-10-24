// Sign-in for the talk page. The browser never talks to Supabase: these call the voice server,
// which holds the Supabase key (sign-in plan, docs/superpowers/plans/2026-10-19-sign-in.md).
//
// The access token lives only in memory. The refresh token is kept in localStorage, so a reload
// stays signed in. The password is never stored: it goes to the voice server once, at sign-in.

// The import names its .ts file so `node --test` can load this module, as web/tests do.
import { VOICE_SERVER_URL } from "./voice-server.ts";

const REFRESH_KEY = "wren.refresh";
const EARLY_MS = 60_000; // refresh when less than a minute is left

// Shown when the voice server can't be reached at all (wren-voice: what happened, what to do next).
export const UNREACHABLE = "I can't reach sign-in right now. Try again in a minute.";
export const SIGNED_OUT = "You've been signed out. Sign in again to keep going.";

export type Refusal = { ok: false; code: string; message: string };
export type SignInResult = { ok: true; needsAge: boolean } | Refusal;
type Tokens = { access_token: string; refresh_token: string; expires_in: number };
type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

type Options = {
  base?: string;
  fetch?: typeof globalThis.fetch;
  storage?: () => Store | null;
  now?: () => number;
};

function browserStorage(): Store | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // storage can be blocked, as in some private windows: then sign-in lasts until a reload
  }
}

async function refusal(response: Response | null): Promise<Refusal> {
  if (response) {
    try {
      const data = await response.json();
      if (typeof data?.message === "string") return { ok: false, code: String(data.code ?? "error"), message: data.message };
    } catch {
      // not a voice-server refusal: say it couldn't be reached
    }
  }
  return { ok: false, code: "unavailable", message: UNREACHABLE };
}

export class Auth {
  // Plain fields, so `node --test` can strip the types.
  private readonly base: string;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly storage: () => Store | null;
  private readonly now: () => number;
  private access: { token: string; expiresAt: number } | null = null;
  private refreshing: Promise<boolean> | null = null;

  constructor(options: Options = {}) {
    this.base = options.base ?? VOICE_SERVER_URL;
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.storage = options.storage ?? browserStorage;
    this.now = options.now ?? (() => Date.now());
  }

  /** Whether this page holds a sign-in, from this load or a stored refresh token. */
  hasSession(): boolean {
    return this.access !== null || Boolean(this.storage()?.getItem(REFRESH_KEY));
  }

  async signIn(email: string, password: string): Promise<SignInResult> {
    const response = await this.post("/auth/signin", { email, password });
    if (!response?.ok) return refusal(response);
    const data = await response.json();
    this.keep(data);
    return { ok: true, needsAge: data.age_confirmed === false };
  }

  async confirmAge(tz: string): Promise<{ ok: true } | Refusal> {
    const token = await this.getAccessToken();
    if (!token) return { ok: false, code: "unauthorized", message: SIGNED_OUT };
    const response = await this.post("/auth/age", { tz }, token);
    return response?.ok ? { ok: true } : refusal(response);
  }

  /** The access token, refreshed first when it has less than a minute left. Null when signed out. */
  async getAccessToken(): Promise<string | null> {
    if (this.access && this.access.expiresAt - this.now() > EARLY_MS) return this.access.token;
    await this.refresh();
    return this.access && this.access.expiresAt > this.now() ? this.access.token : null;
  }

  async signOut(): Promise<void> {
    const token = await this.getAccessToken();
    this.forget();
    if (token) await this.post("/auth/signout", {}, token);
  }

  /** One refresh at a time: a refresh token works once, so a second caller waits for the first. */
  private refresh(): Promise<boolean> {
    this.refreshing ??= this.refreshOnce().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  private async refreshOnce(): Promise<boolean> {
    const refreshToken = this.storage()?.getItem(REFRESH_KEY);
    if (!refreshToken) return false;
    const response = await this.post("/auth/refresh", { refresh_token: refreshToken });
    if (response?.status === 401) {
      this.forget(); // the server refused it: sign in again
      return false;
    }
    if (!response?.ok) return false; // can't tell now: keep the refresh token for the next try
    this.keep(await response.json());
    return true;
  }

  private keep(tokens: Tokens): void {
    this.access = { token: tokens.access_token, expiresAt: this.now() + tokens.expires_in * 1000 };
    this.storage()?.setItem(REFRESH_KEY, tokens.refresh_token);
  }

  private forget(): void {
    this.access = null;
    this.storage()?.removeItem(REFRESH_KEY);
  }

  private async post(path: string, body: object, token?: string): Promise<Response | null> {
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (token) headers.authorization = `Bearer ${token}`;
    try {
      return await this.fetcher(`${this.base}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
    } catch {
      return null;
    }
  }
}

/** The talk page's sign-in. */
export const auth = new Auth();
