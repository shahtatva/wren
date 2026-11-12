"use client";

import Link from "next/link";
import {
  useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode,
} from "react";
import { auth } from "@/lib/auth";
import { birdView } from "@/lib/bird-state";
import { VoiceClient, type ClientEvent, type VoiceStatus } from "@/lib/voice/client";
import type { TurnMetrics } from "@/lib/voice/protocol";
import { DebugPanel } from "./debug-panel";
import { STEP_OUT_TEXT, TAPPED, stepOutAfter, type PendingStepOut } from "@/lib/step-out";
import { screenAfter, type Screen, type ScreenEvent } from "@/lib/talk-screen";
import { EMPTY_TRANSCRIPT, transcriptAfter } from "@/lib/talk-transcript";
import { FIELD, FOCUS, PRIMARY, QUIET } from "@/lib/ui";
import { AgeStep, SignIn } from "./sign-in";
import { MockBar, Stage } from "./stage";
import { Transcript } from "./transcript";

const subscribeNever = () => () => {};
const debugInUrl = () => new URLSearchParams(window.location.search).has("debug");
const noDebugOnServer = () => false;
const storedSession = () => (auth.hasSession() ? "yes" : "no");
const unknownOnServer = () => "unknown";

// In development the loopback dev identity can let the page in without a sign-in, so it starts on
// the talk screen and shows sign-in only if the server refuses. A production build starts on sign-in.
const WITHOUT_A_SESSION: Screen = process.env.NODE_ENV === "development" ? "talk" : "sign-in";

type Timing = { key: string; metrics: TurnMetrics };

function addTiming(list: Timing[], key: string, metrics: TurnMetrics): Timing[] {
  // A turn's metrics arrive twice, with its first audio and when it finishes. Keep the latest.
  return [...list.filter((timing) => timing.key !== key), { key, metrics }];
}

function Header({ children, below }: { children?: ReactNode; below?: ReactNode }) {
  return (
    <header className="pt-3 pb-1">
      <div className="flex items-center justify-between gap-4">
        <Link href="/" className={`casual inline-flex min-h-11 items-center rounded text-[26px] font-extrabold tracking-tight text-wren ${FOCUS}`}>
          Wren
        </Link>
        <div className="flex items-center gap-4">{children}</div>
      </div>
      {below}
    </header>
  );
}

function HeaderButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={`${QUIET} text-[15px]`}>
      {label}
    </button>
  );
}

// Always on screen, so a session that starts by typing is disclosed too (wren-voice boundary 2).
const AI_NOTE = <p className="mt-3 text-center text-[13px] text-ink-soft">Wren is an AI, not a person.</p>;

export function Talk() {
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [{ lines, mock }, dispatch] = useReducer(transcriptAfter, EMPTY_TRANSCRIPT);
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
  const level = useRef<HTMLDivElement>(null); // the stage, which carries Wren's voice level to the beak
  const bottom = useRef<HTMLDivElement>(null);
  const stepOut = useRef<PendingStepOut>(null); // a Step out tap the server hasn't answered yet

  // Next keeps this page hidden, not unmounted, when the user goes elsewhere (cacheComponents; see
  // node_modules/next/dist/docs/01-app/02-guides/preserving-ui-state.md). Hiding runs this cleanup, and it
  // starts the page over as an unmount would. Without it the ref keeps a stopped client, so after coming
  // back from What Wren knows the next tap would do nothing.
  useLayoutEffect(
    () => () => {
      client.current?.stop();
      client.current = null;
      stepOut.current = null;
      dispatch({ type: "clear" });
      setCaption("");
      setNotice(null);
      setTimings([]);
      setInfo(null);
      setHearing(false);
      setMicOn(true);
    },
    [],
  );
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
      dispatch({ type: "ended" }); // no session, so no mock
    }
    if (change.forgetSignIn) {
      auth.forget();
      setSignedIn(false);
    }
    setScreen(change.screen);
  }, []);

  const onEvent = useCallback((event: ClientEvent) => {
    stepOut.current = stepOutAfter(stepOut.current, event);
    switch (event.type) {
      case "status":
        setStatus(event.status);
        break;
      case "ready":
        setInfo({ memory: event.memory, model: event.model });
        dispatch(event); // a new connection means no mock, unless a "mock" message follows
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
      case "wren":
      case "mock":
      case "drop":
      case "cut":
        dispatch(event);
        break;
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
        dispatch(event);
        break;
    }
  }, [go]);

  // Wren's voice level moves the bird's beak directly, about 23 times a second, without re-rendering.
  const onLevel = useCallback((rms: number) => {
    level.current?.style.setProperty("--level", Math.min(1, rms * 5).toFixed(3));
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

  const say = async (text: string) => {
    const voice = await begin();
    await voice.sendText(text);
  };

  const onSend = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    await say(text);
  };

  // One tap at a time: until the server answers it, another tap would cut off "Stepping out." or reach Wren itself.
  const onStepOut = () => {
    if (stepOut.current !== null) return;
    stepOut.current = TAPPED;
    void say(STEP_OUT_TEXT);
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
    dispatch({ type: "clear" });
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
            <p role="status" className="pb-4 text-[15px] text-brick">
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

  const view = birdView({ status, micOn, hearing, mock: mock.on });

  return (
    <main className="mx-auto flex h-dvh w-full max-w-[40rem] flex-col px-5">
      <Header below={mock.on ? <MockBar name={mock.name} onStepOut={onStepOut} /> : null}>
        <Link href="/knows" className={`inline-flex min-h-11 items-center rounded text-[15px] underline-offset-4 hover:underline ${FOCUS}`}>
          What Wren knows
        </Link>
        {signedIn ? (
          <HeaderButton label="Sign out" onClick={onSignOut} />
        ) : (
          <HeaderButton label="Sign in" onClick={() => go({ type: "sign-in-asked" })} />
        )}
      </Header>

      <Transcript lines={lines} caption={caption} bottomRef={bottom} />

      <Stage view={view} mock={mock.on} onTalk={onTalk} levelRef={level} />

      {notice ? (
        <p role="status" className="pt-2 text-center text-[15px] text-brick">
          {notice}
        </p>
      ) : null}

      <footer className="pt-3 pb-4">
        <form onSubmit={onSend} className="flex gap-2">
          <label htmlFor="message" className="sr-only">
            Message
          </label>
          <input
            id="message"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Or type here"
            autoComplete="off"
            className={FIELD}
          />
          <button type="submit" className={PRIMARY}>
            Send
          </button>
        </form>
        {AI_NOTE}
      </footer>

      {showDebug ? <DebugPanel metrics={timings.map((timing) => timing.metrics)} info={info} /> : null}
    </main>
  );
}
