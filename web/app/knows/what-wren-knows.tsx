"use client";

import Link from "next/link";
import { useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { Bird } from "@/lib/bird";
import { knowsApi, type InterviewFields, type KnownProfile, type Knows, type Outcome, type ProfileKey } from "@/lib/knows-api";
import { CLOSED, formAfter } from "@/lib/knows-form";
import {
  COPY, SECTIONS, counts, memoriesIn, nothingSaved, orderInterviews, parseRoles, profileRows, todayIn, type SectionId,
} from "@/lib/knows-view";
import { FOCUS, OUTLINE, QUIET } from "@/lib/ui";
import { InterviewItem, MemoryItem, ProfileItem, type Controls } from "./items";

// Trust screens stay plain (wren-voice): the page shows what Wren uses, worded as stored, and says
// what each change does.

type Page =
  | { state: "loading" }
  | { state: "ready"; knows: Knows; today: string }
  | { state: "signed-out" | "refused"; message: string }
  | { state: "failed" };

// What a profile field's Save and Clear send. The time zone isn't edited here.
const saving = (key: ProfileKey, value: string): Partial<KnownProfile> =>
  key === "display_name" ? { display_name: value.trim() || null } : key === "summary" ? { summary: value.trim() } : { target_roles: parseRoles(value) };
const clearing = (key: ProfileKey): Partial<KnownProfile> =>
  key === "display_name" ? { display_name: null } : key === "summary" ? { summary: "" } : { target_roles: [] };

function Chevron() {
  return (
    <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M15 5l-7 7 7 7" />
    </svg>
  );
}

export function WhatWrenKnows() {
  const [page, setPage] = useState<Page>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [form, dispatch] = useReducer(formAfter, CLOSED);
  const inFlight = useRef(false); // one change on its way at a time, even between two quick taps
  const focusNext = useRef<string | null>(null); // a heading to focus once the page has re-rendered

  // Next shows this page again from its hidden state when the owner comes back, which runs this again,
  // so the page always shows what Wren knows now. A late answer from before is dropped.
  useEffect(() => {
    let live = true;
    knowsApi.load().then((outcome) => {
      if (!live) return;
      if (outcome.ok) setPage({ state: "ready", knows: outcome.value, today: todayIn(outcome.value.timezone, new Date()) });
      else if (outcome.reason === "signed-out" || outcome.reason === "refused") setPage({ state: outcome.reason, message: outcome.message });
      else setPage({ state: "failed" });
    });
    return () => {
      live = false;
    };
  }, [attempt]);

  // Leaving the page closes any open edit, so it doesn't come back half done.
  useLayoutEffect(() => () => dispatch({ type: "reset" }), []);

  useEffect(() => {
    if (focusNext.current) document.getElementById(focusNext.current)?.focus();
    focusNext.current = null;
  });

  const update = (change: (knows: Knows) => Knows) =>
    setPage((current) => (current.state === "ready" ? { ...current, knows: change(current.knows) } : current));

  /** Send one change, then say what happened in its section. `gone` (a 404) means the row was already
   * deleted: a delete counts as done, and an edit takes the row off the page. */
  async function change<T>(
    section: SectionId,
    send: () => Promise<Outcome<T>>,
    done: { apply: (value: T) => void; text: string; focusHeading?: boolean },
    gone: { apply: () => void; text: string },
  ) {
    if (inFlight.current) return;
    inFlight.current = true;
    dispatch({ type: "submit" });
    const outcome = await send();
    inFlight.current = false;
    if (outcome.ok || outcome.reason === "gone") {
      if (outcome.ok) done.apply(outcome.value);
      else gone.apply();
      if (done.focusHeading || !outcome.ok) focusNext.current = `h-${section}`;
      dispatch({ type: "done", section, text: outcome.ok ? done.text : gone.text });
    } else if (outcome.reason === "signed-out" || outcome.reason === "refused") {
      dispatch({ type: "reset" });
      setPage({ state: outcome.reason, message: outcome.message });
    } else {
      dispatch({ type: "failed", message: COPY.notSaved });
    }
  }

  const controlsFor = (id: string): Controls => ({
    mode: form.open?.id === id ? form.open.mode : "idle",
    busy: form.busy,
    error: form.open?.id === id ? form.error : null,
    onEdit: () => dispatch({ type: "edit", id }),
    onCancel: () => dispatch({ type: "cancel" }),
    onAskDelete: () => dispatch({ type: "ask-delete" }),
  });

  const header = (
    <>
      <div className="px-3 pt-1.5">
        <Link href="/talk" className={`inline-flex min-h-11 items-center gap-1 rounded px-2 text-[15px] underline-offset-4 hover:underline ${FOCUS}`}>
          <Chevron />
          Back to talk
        </Link>
      </div>
      <header className="relative px-5 pt-2.5 pb-5">
        <h1 className="casual max-w-[230px] text-[42px] leading-[1.02] font-extrabold tracking-[-0.015em] text-wren">What Wren knows</h1>
        <div aria-hidden className="absolute top-0 right-[18px] h-[90px] w-[120px]">
          <Bird pose="perched" className="h-full w-full" />
          <svg viewBox="0 0 160 120" className="absolute inset-0 h-full w-full overflow-visible" fill="none">
            <path d="M10 117 Q80 112 158 118" style={{ stroke: "var(--branch)" }} strokeWidth={5} strokeLinecap="round" />
          </svg>
        </div>
        <p className="mt-4 max-w-[34ch] text-[17px] leading-[1.45]">{COPY.intro}</p>
        {page.state === "ready" && nothingSaved(page.knows) ? (
          <p className="mt-3 max-w-[34ch] text-[15px] leading-[1.45]">{COPY.notSavingYet}</p>
        ) : null}
      </header>
    </>
  );

  const footer = (
    <footer className="px-5 py-6 text-center">
      {page.state === "ready" ? <p className="text-[15px]">{COPY.end}</p> : null}
      <p className="mt-1 text-[13px] text-ink-soft">Wren is an AI, not a person.</p>
    </footer>
  );

  if (page.state !== "ready") {
    return (
      <main className="mx-auto w-full max-w-[40rem]">
        {header}
        <div className="px-5 pb-4 text-[17px] leading-[1.45]">
          {page.state === "loading" ? (
            <p role="status">{COPY.loading}</p>
          ) : page.state === "failed" ? (
            <>
              <p role="alert">{COPY.loadFailed}</p>
              <button
                type="button"
                onClick={() => {
                  setPage({ state: "loading" });
                  setAttempt((n) => n + 1);
                }}
                className={`${OUTLINE} mt-3`}
              >
                Try again
              </button>
            </>
          ) : (
            <>
              <p>{page.message}</p>
              <Link href="/talk" className={`${QUIET} mt-2`}>
                {page.state === "signed-out" ? "Sign in" : "Back to Wren"}
              </Link>
            </>
          )}
        </div>
        {footer}
      </main>
    );
  }

  const { knows, today } = page;
  const total = counts(knows);
  const tz = knows.timezone;

  const memoryList = (section: SectionId) =>
    memoriesIn(knows, section).map((memory) => (
      <li key={memory.id} className="border-t-[1.5px] border-feather py-3.5">
        <MemoryItem
          memory={memory}
          tz={tz}
          controls={controlsFor(memory.id)}
          onSave={(text) =>
            change(section, () => knowsApi.editMemory(memory.id, text),
              { apply: (saved) => update((k) => ({ ...k, memories: k.memories.map((m) => (m.id === saved.id ? saved : m)) })), text: COPY.saved },
              { apply: () => update((k) => ({ ...k, memories: k.memories.filter((m) => m.id !== memory.id) })), text: COPY.gone })
          }
          onDelete={() =>
            change(section, () => knowsApi.deleteMemory(memory.id),
              { apply: () => update((k) => ({ ...k, memories: k.memories.filter((m) => m.id !== memory.id) })), text: COPY.deletedMemory, focusHeading: true },
              { apply: () => update((k) => ({ ...k, memories: k.memories.filter((m) => m.id !== memory.id) })), text: COPY.deletedMemory })
          }
        />
      </li>
    ));

  const interviewList = orderInterviews(knows.pipeline, today).map((interview) => {
    const without = (k: Knows) => ({ ...k, pipeline: k.pipeline.filter((row) => row.id !== interview.id) });
    return (
      <li key={interview.id} className="border-t-[1.5px] border-feather py-3.5">
        <InterviewItem
          interview={interview}
          tz={tz}
          controls={controlsFor(interview.id)}
          onSave={(fields: InterviewFields) =>
            change("interviews", () => knowsApi.editInterview(interview.id, fields),
              { apply: (saved) => update((k) => ({ ...k, pipeline: k.pipeline.map((row) => (row.id === saved.id ? saved : row)) })), text: COPY.saved },
              { apply: () => update(without), text: COPY.gone })
          }
          onDelete={() =>
            change("interviews", () => knowsApi.deleteInterview(interview.id),
              { apply: () => update(without), text: COPY.deletedInterview, focusHeading: true },
              { apply: () => update(without), text: COPY.deletedInterview })
          }
        />
      </li>
    );
  });

  const profileList = profileRows(knows).map((row) => {
    const key = row.key; // the time zone row has no Edit button, so only the three profile fields send changes
    const setProfile = (profile: KnownProfile) => update((k) => ({ ...k, profile }));
    return (
      <li key={key} className="border-t-[1.5px] border-feather py-3.5">
        <ProfileItem
          row={row}
          controls={controlsFor(`profile:${key}`)}
          onSave={(value) =>
            key !== "timezone" &&
            change("about", () => knowsApi.editProfile(saving(key, value)), { apply: setProfile, text: COPY.saved }, { apply: () => {}, text: COPY.notSaved })
          }
          onClear={() =>
            key !== "timezone" &&
            change("about", () => knowsApi.editProfile(clearing(key)), { apply: setProfile, text: COPY.cleared, focusHeading: true }, { apply: () => {}, text: COPY.notSaved })
          }
        />
      </li>
    );
  });

  const listFor = (section: SectionId) =>
    section === "about" ? profileList : section === "interviews" ? interviewList : memoryList(section);

  return (
    <main className="mx-auto w-full max-w-[40rem]">
      {header}
      <nav aria-label="Sections" className="flex gap-5 overflow-x-auto px-5 pb-3.5 whitespace-nowrap [scrollbar-width:none]">
        {SECTIONS.map((section) => (
          <a key={section.id} href={`#${section.id}`} className={`inline-flex min-h-11 items-center gap-1.5 rounded text-[15px] underline-offset-4 hover:underline ${FOCUS}`}>
            {section.name}
            <span className="text-ink-soft">{total[section.id]}</span>
          </a>
        ))}
      </nav>
      {SECTIONS.map((section, index) => {
        const items = listFor(section.id);
        return (
          <section
            key={section.id}
            id={section.id}
            aria-labelledby={`h-${section.id}`}
            className={`px-5 pt-6 pb-2.5 ${index % 2 === 0 ? "bg-background" : "bg-toffee"}`}
          >
            <h2 id={`h-${section.id}`} tabIndex={-1} className={`casual rounded text-[27px] leading-[1.1] font-[750] text-wren ${FOCUS}`}>
              {section.name}
            </h2>
            <p className="mt-1.5 text-[15px] leading-[1.4] text-ink-soft">{section.note}</p>
            {form.flash?.section === section.id ? (
              <p role="status" className="mt-3 text-[15px] font-[650]">
                {form.flash.text}
              </p>
            ) : null}
            {items.length > 0 ? (
              <ul className="mt-2">{items}</ul>
            ) : (
              <p className="py-3.5 text-[15px] text-ink-soft">{COPY.empty}</p>
            )}
          </section>
        );
      })}
      {footer}
    </main>
  );
}
