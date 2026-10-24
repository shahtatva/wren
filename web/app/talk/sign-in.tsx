"use client";

import { useReducer, useState, type FormEvent } from "react";
import { auth } from "@/lib/auth";
import { START_FORM, signInForm } from "@/lib/sign-in-form";

// Trust screens stay plain (wren-voice): they say what's happening and what to do, nothing more.

const field =
  "w-full rounded-md border border-feather bg-transparent px-3 py-2 placeholder:text-foreground/50 focus-visible:outline-2 focus-visible:outline-wren";
const primary =
  "rounded-md bg-wren px-4 py-2 font-medium text-background disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-wren";
const quiet =
  "rounded-md px-3 py-2 font-medium text-wren hover:bg-wren/10 disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-wren";

export function SignIn({ onSignedIn }: { onSignedIn: (needsAge: boolean) => void }) {
  const [form, dispatch] = useReducer(signInForm, START_FORM);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (form.busy) return;
    dispatch({ type: "submit" });
    const result = await auth.signIn(form.email.trim(), form.password);
    if (!result.ok) {
      dispatch({ type: "refused", message: result.message });
      return;
    }
    dispatch({ type: "done" });
    onSignedIn(result.needsAge);
  };

  return (
    <form onSubmit={onSubmit} aria-describedby="sign-in-help" className="max-w-[22rem] space-y-4">
      <h1 className="text-xl leading-snug text-balance">Sign in to talk with Wren.</h1>
      <div className="space-y-1">
        <label htmlFor="email" className="block text-sm text-foreground/70">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={form.email}
          onChange={(event) => dispatch({ type: "email", value: event.target.value })}
          className={field}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor="password" className="block text-sm text-foreground/70">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={form.password}
          onChange={(event) => dispatch({ type: "password", value: event.target.value })}
          className={field}
        />
      </div>
      {form.error ? (
        <p role="alert" className="text-sm text-brick">
          {form.error}
        </p>
      ) : null}
      <button type="submit" disabled={form.busy} className={primary}>
        {form.busy ? "Signing in" : "Sign in"}
      </button>
      <p id="sign-in-help" className="text-sm text-foreground/70">
        Wren is invite-only for now. If you&apos;ve lost your password, ask whoever set up your account.
      </p>
    </form>
  );
}

export function AgeStep({ onConfirmed, onSignedOut }: { onConfirmed: () => void; onSignedOut: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [under18, setUnder18] = useState(false);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    const result = await auth.confirmAge(Intl.DateTimeFormat().resolvedOptions().timeZone);
    setBusy(false);
    if (result.ok) onConfirmed();
    else if (result.code === "unauthorized") onSignedOut(result.message);
    else setError(result.message);
  };

  if (under18) {
    // wren-voice boundary 1: say so kindly, stop coaching, and point to real people.
    return (
      <div className="max-w-[34ch] space-y-3">
        <h1 className="text-xl leading-snug text-balance">Thanks for telling me.</h1>
        <p className="leading-relaxed">
          I&apos;m built for adults, so I can&apos;t coach you. A school counselor or your career office can help you get
          ready.
        </p>
      </div>
    );
  }
  return (
    <div className="max-w-[34ch] space-y-4">
      <h1 className="text-xl leading-snug text-balance">I&apos;m built for adults. Are you 18 or older?</h1>
      {error ? (
        <p role="alert" className="text-sm text-brick">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={confirm} disabled={busy} className={primary}>
          Yes, I&apos;m 18 or older
        </button>
        <button type="button" onClick={() => setUnder18(true)} disabled={busy} className={quiet}>
          No, I&apos;m under 18
        </button>
      </div>
    </div>
  );
}
