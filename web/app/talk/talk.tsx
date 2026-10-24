"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { auth } from "@/lib/auth";
import { VoiceClient, type ClientEvent, type VoiceStatus } from "@/lib/voice/client";
import type { TurnMetrics } from "@/lib/voice/protocol";
import { DebugPanel } from "./debug-panel";
import { screenAfter, type Screen, type ScreenEvent } from "@/lib/talk-screen";
import { AgeStep, SignIn } from "./sign-in";

type Line = { key: string; speaker: "user" | "wren"; text: string; cut: boolean };

const STATUS: Record<VoiceStatus, string> = {
  idle: "Not started",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Wren is talking",
  paused: "Listening to you",
  reconnecting: "Reconnecting",
};

const subscribeNever = () => () => {};
const debugInUrl = () => new URLSearchParams(window.location.search).has("debug");
const noDebugOnServer = () => false;
const storedSession = () => (auth.hasSession() ? "yes" : "no");
const unknownOnServer = () => "unknown";

// In development the loopback dev identity can let the page in without a sign-in, so it starts on
// the talk screen and shows sign-in only if the server refuses. A production build starts on sign-in.
const WITHOUT_A_SESSION: Screen = process.env.NODE_ENV === "development" ? "talk" : "sign-in";

function lineKey(speaker: Line["speaker"], epoch: number, turn: number): string {
  return `${speaker}:${epoch}:${turn}`;
}

function addWren(lines: Line[], key: string, text: string): Line[] {
  const at = lines.findIndex((line) => line.key === key);
  if (at === -1) return [...lines, { key, speaker: "wren", text, cut: false }];
  const next = lines.slice();
  next[at] = { ...next[at], text: `${next[at].text} ${text}` };
  return next;
}

type Timing = { key: string; metrics: TurnMetrics };

function addTiming(list: Timing[], key: string, metrics: TurnMetrics): Timing[] {
  // A turn's metrics arrive twice, with its first audio and when it finishes. Keep the latest.
  return [...list.filter((timing) => timing.key !== key), { key, metrics }];
}

function Header({ children }: { children?: ReactNode }) {
  return (
    <header className="flex items-baseline justify-between pt-5 pb-3">
      <Link href="/" className="rounded text-lg font-semibold tracking-tight focus-visible:outline-2 focus-visible:outline-wren">
        Wren
      </Link>
      <div className="flex items-baseline gap-4">{children}</div>
    </header>
  );
}

function HeaderButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded text-sm text-foreground/70 underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-wren"
    >
      {label}
    </button>
  );
}

// Always on screen, so a session that starts by typing is disclosed too (wren-voice boundary 2).
const AI_NOTE = <p className="mt-3 text-sm text-foreground/70">Wren is an AI, not a person.</p>;

function MicGlyph({ muted }: { muted: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="size-9" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" strokeLinecap="round" />
      {muted ? <path d="M4 4l16 16" strokeLinecap="round" /> : null}
    </svg>
  );
}

export function Talk() {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [caption, setCaption] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [hearing, setHearing] = useState(false);
  const [micOn, setMicOn] = useState(true);
  const [info, setInfo] = useState<{ memory: string; model: string } | null>(null);
  const [timings, setTimings] = useState<Timing[]>([]);
  const [draft, setDraft] = useState("");
  const debugParam = useSyncExternalStore(subscribeNever, debugInUrl, noDebugOnServer);
  const showDebug = process.env.NODE_ENV === "development" || debugParam;
  const session = useSyncExternalStore(subscribeNever, storedSession, unknownOnServer);
  const [chosenScreen, setScreen] = useState<Screen | null>(null);
  const [signedInNow, setSignedIn] = useState<boolean | null>(null);
  const screen: Screen | null =
    chosenScreen ?? (session === "unknown" ? null : session === "yes" ? "talk" : WITHOUT_A_SESSION);
  const signedIn = signedInNow ?? session === "yes";

  const client = useRef<VoiceClient | null>(null);
  const ring = useRef<HTMLSpanElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => () => client.current?.stop(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [lines, caption]);

  // A sign-in event: show the right screen, and let go of the session or the stored sign-in when it says to.
  const go = useCallback((event: ScreenEvent) => {
    const change = screenAfter(event);
    if (change.stopClient) {
      client.current?.stop();
      client.current = null;
      setMicOn(true);
    }
    if (change.forgetSignIn) {
      auth.forget();
      setSignedIn(false);
    }
    setScreen(change.screen);
  }, []);

  const onEvent = useCallback((event: ClientEvent) => {
    switch (event.type) {
      case "status":
        setStatus(event.status);
        break;
      case "ready":
        setInfo({ memory: event.memory, model: event.model });
        break;
      case "hearing":
        setHearing(event.available);
        break;
      case "caption":
        setCaption(event.text);
        break;
      case "notice":
        setNotice(event.text);
        break;
      case "user":
        setLines((prev) => [...prev, { key: lineKey("user", event.epoch, event.turn), speaker: "user", text: event.text, cut: false }]);
        break;
      case "wren":
        setLines((prev) => addWren(prev, lineKey("wren", event.epoch, event.turn), event.text));
        break;
      case "drop": {
        // That turn merged into the next one: the user hadn't finished.
        const keys = [lineKey("user", event.epoch, event.turn), lineKey("wren", event.epoch, event.turn)];
        setLines((prev) => prev.filter((line) => !keys.includes(line.key)));
        break;
      }
      case "cut": {
        const key = lineKey("wren", event.epoch, event.turn);
        setLines((prev) => prev.map((line) => (line.key === key ? { ...line, cut: true } : line)));
        break;
      }
      case "metrics":
        setTimings((prev) => addTiming(prev, `${event.epoch}:${event.metrics.turn}`, event.metrics));
        break;
      case "refused":
        if (event.code === "age_unconfirmed") setNotice(null); // the age step says it
        go({ type: "refused", code: event.code });
        break;
      case "ended":
        // The session is over (the cap, or a refusal). The next tap or message starts a new one.
        client.current = null;
        setMicOn(true);
        break;
    }
  }, [go]);

  // Wren's voice level drives the ring directly, about 23 times a second, without re-rendering.
  const onLevel = useCallback((rms: number) => {
    ring.current?.style.setProperty("--level", Math.min(1, rms * 5).toFixed(3));
  }, []);

  const begin = useCallback(async () => {
    if (client.current) return client.current;
    const voice = new VoiceClient({ onEvent, onLevel, getToken: () => auth.getAccessToken() });
    client.current = voice;
    await voice.start();
    return voice;
  }, [onEvent, onLevel]);

  const onTalk = async () => {
    const voice = client.current;
    if (!voice) {
      await begin();
      return;
    }
    if (status === "reconnecting") {
      voice.reconnect();
      return;
    }
    const next = !micOn || !hearing; // tapping while paused for idleness turns listening back on
    setMicOn(next);
    voice.setMic(next);
  };

  const onSend = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    const voice = await begin();
    await voice.sendText(text);
  };

  const onSignedIn = (needsAge: boolean) => {
    setSignedIn(true);
    setNotice(null);
    go({ type: "signed-in", needsAge });
  };

  const onAgeSignedOut = (message: string) => {
    setNotice(message); // the sign-in screen shows why it's back
    go({ type: "age-signed-out" });
  };

  const onSignOut = async () => {
    client.current?.stop();
    client.current = null;
    await auth.signOut();
    setSignedIn(false);
    setLines([]);
    setTimings([]);
    setCaption("");
    setNotice(null);
    setInfo(null);
    setMicOn(true);
    setScreen("sign-in");
  };

  if (screen !== "talk") {
    return (
      <main className="mx-auto flex h-dvh w-full max-w-[40rem] flex-col px-5">
        <Header>{screen === "age" && signedIn ? <HeaderButton label="Sign out" onClick={onSignOut} /> : null}</Header>
        <section className="flex-1 overflow-y-auto pt-[18vh] pb-4">
          {notice && screen === "sign-in" ? (
            <p role="status" className="pb-4 text-sm text-brick">
              {notice}
            </p>
          ) : null}
          {screen === "sign-in" ? (
            <SignIn onSignedIn={onSignedIn} />
          ) : screen === "age" ? (
            <AgeStep onConfirmed={() => go({ type: "age-confirmed" })} onSignedOut={onAgeSignedOut} />
          ) : null}
        </section>
        <footer className="border-t border-feather pb-5">{AI_NOTE}</footer>
      </main>
    );
  }

  const started = status !== "idle";
  const muted = started && !micOn;
  const ringStyle =
    status === "listening" && micOn
      ? "wren-breathe border-2"
      : status === "speaking"
        ? "transition-[border-width] duration-75"
        : status === "paused"
          ? "border-2 border-dashed"
          : status === "reconnecting"
            ? "border-2 border-dotted opacity-60"
            : "border-0";
  const speakingWidth = status === "speaking" ? { borderWidth: "calc(2px + var(--level, 0) * 12px)" } : undefined;

  return (
    <main className="mx-auto flex h-dvh w-full max-w-[40rem] flex-col px-5">
      <Header>
        <p aria-live="polite" className="text-sm text-foreground/70">
          {muted ? "Muted" : STATUS[status]}
        </p>
        {signedIn ? (
          <HeaderButton label="Sign out" onClick={onSignOut} />
        ) : (
          <HeaderButton label="Sign in" onClick={() => go({ type: "sign-in-asked" })} />
        )}
      </Header>

      <section aria-label="Conversation" className="flex-1 overflow-y-auto py-4">
        {lines.length === 0 && !caption ? (
          <p className="max-w-[32ch] pt-[18vh] text-xl leading-snug text-foreground/70">
            Tap the mic and say hi. I&apos;ll listen, and you can cut in whenever you like.
          </p>
        ) : (
          <ol className="space-y-3">
            {lines.map((line) => (
              <li key={line.key} className="grid grid-cols-[3rem_1fr] gap-x-3">
                <span className="pt-0.5 text-sm text-foreground/70">{line.speaker === "wren" ? "Wren" : "You"}</span>
                <p className={line.cut ? "leading-relaxed text-foreground/70" : "leading-relaxed"}>
                  {line.text}
                  {line.cut ? <span className="sr-only"> (you cut in here)</span> : null}
                </p>
              </li>
            ))}
            {caption ? (
              <li className="grid grid-cols-[3rem_1fr] gap-x-3">
                <span className="pt-0.5 text-sm text-foreground/70">You</span>
                <p className="leading-relaxed text-foreground/70">{caption}</p>
              </li>
            ) : null}
          </ol>
        )}
        <div ref={bottom} />
      </section>

      {notice ? (
        <p role="status" className="pb-2 text-sm text-brick">
          {notice}
        </p>
      ) : null}

      <footer className="border-t border-feather pt-4 pb-5">
        <div className="flex items-center gap-5">
          <button
            type="button"
            onClick={onTalk}
            aria-label={!started ? "Start talking" : status === "reconnecting" ? "Reconnect" : muted || !hearing ? "Turn the mic on" : "Mute"}
            className={`relative grid size-20 shrink-0 place-items-center rounded-full text-background transition-colors focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-wren ${muted ? "bg-foreground/40" : "bg-wren"}`}
          >
            <span
              ref={ring}
              aria-hidden
              style={speakingWidth}
              className={`pointer-events-none absolute -inset-2 rounded-full border-wren ${ringStyle}`}
            />
            {status === "thinking" ? (
              <span aria-hidden className="wren-orbit pointer-events-none absolute -inset-3">
                <span className="absolute top-0 left-1/2 size-2 -translate-x-1/2 rounded-full bg-wren" />
              </span>
            ) : null}
            <MicGlyph muted={muted} />
          </button>
          <form onSubmit={onSend} className="flex min-w-0 flex-1 gap-2">
            <label htmlFor="message" className="sr-only">
              Message
            </label>
            <input
              id="message"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Or type here"
              autoComplete="off"
              className="min-w-0 flex-1 rounded-md border border-feather bg-transparent px-3 py-2 placeholder:text-foreground/50 focus-visible:outline-2 focus-visible:outline-wren"
            />
            <button
              type="submit"
              className="rounded-md px-3 py-2 font-medium text-wren hover:bg-wren/10 focus-visible:outline-2 focus-visible:outline-wren"
            >
              Send
            </button>
          </form>
        </div>
        {AI_NOTE}
      </footer>

      {showDebug ? <DebugPanel metrics={timings.map((timing) => timing.metrics)} info={info} /> : null}
    </main>
  );
}
