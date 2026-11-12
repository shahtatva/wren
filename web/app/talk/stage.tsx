"use client";

import { useLayoutEffect, useRef, type Ref } from "react";
import { Bird, Perch } from "@/lib/bird";
import type { BirdView } from "@/lib/bird-state";
import { FOCUS, QUIET } from "@/lib/ui";

function MicGlyph({ off }: { off: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="size-9" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" strokeLinecap="round" />
      {off ? <path d="M4 4l16 16" strokeLinecap="round" /> : null}
    </svg>
  );
}

/** Wren on its branch is the talk button, and its posture shows what Wren is doing. The talk page writes
 * Wren's voice level to the root (--level), which moves the beak. In a mock interview a plain mic button
 * takes Wren's place, so the interviewer gets no bird. The status line and the button's label are the
 * talk page's from before Perch; the hint repeats the label for sighted users, so screen readers skip it. */
export function Stage({ view, mock, onTalk, levelRef }: { view: BirdView; mock: boolean; onTalk: () => void; levelRef: Ref<HTMLDivElement> }) {
  const off = view.label === "Turn the mic on";
  return (
    <div ref={levelRef}>
      {mock ? (
        <div className="grid h-[clamp(150px,28dvh,236px)] place-items-center">
          <button
            type="button"
            aria-label={view.label}
            onClick={onTalk}
            className={`grid size-[72px] cursor-pointer place-items-center rounded-full text-background ${off ? "bg-ink-soft" : "bg-wren"} ${FOCUS}`}
          >
            <MicGlyph off={off} />
          </button>
        </div>
      ) : (
        <Perch pose={view.pose} className="-mx-5">
          <button type="button" aria-label={view.label} onClick={onTalk} className={`perch-bird cursor-pointer rounded-[28px] ${FOCUS}`}>
            <Bird />
          </button>
        </Perch>
      )}
      <p id="voice-status" aria-live="polite" className="text-center font-[650]">
        {view.status}
      </p>
      <p aria-hidden className="text-center text-sm text-ink-soft">
        {view.hint}
      </p>
    </div>
  );
}

// While an interviewer has the floor: say who, and keep one tap to step out in reach. The small bird is
// only a sign that Wren is waiting nearby: never a button, never posed, and never given the voice level,
// so it doesn't mouth the interviewer's words.
export function MockBar({ name, onStepOut }: { name: string | null; onStepOut: () => void }) {
  const stepOut = useRef<HTMLButtonElement>(null);
  // The bar leaves with the interview, and its button with it. If the button had focus, the message box
  // takes it, so the keyboard isn't dropped back at the top of the page. A layout effect's cleanup runs
  // while the button is still on the page.
  useLayoutEffect(() => {
    const button = stepOut.current;
    return () => {
      if (button !== null && document.activeElement === button) document.getElementById("message")?.focus();
    };
  }, []);
  return (
    <div className="flex items-center justify-between gap-3 pt-1">
      <p className="min-w-0 text-[15px] font-[650] break-words">{name ? `Mock interview with ${name}` : "Mock interview"}</p>
      <div className="flex shrink-0 items-center gap-2">
        <Bird pose="perched" className="h-8 w-11" />
        <button ref={stepOut} type="button" onClick={onStepOut} className={QUIET}>
          Step out
        </button>
      </div>
    </div>
  );
}
