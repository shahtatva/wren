// Shared class strings for the app's controls (Perch). Every control shows a visible focus ring, and
// every tap target is at least 44px tall.

export const FOCUS = "focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-foreground";

export const FIELD = `h-12 w-full min-w-0 rounded-full border-2 border-ink-soft bg-field px-[18px] text-foreground placeholder:text-ink-soft ${FOCUS}`;

export const AREA = `w-full rounded-2xl border-2 border-ink-soft bg-field px-3 py-2.5 text-foreground placeholder:text-ink-soft ${FOCUS}`;

export const PRIMARY = `inline-flex h-12 items-center justify-center rounded-full bg-wren px-5 font-[650] text-background disabled:opacity-60 ${FOCUS}`;

export const OUTLINE = `inline-flex min-h-11 items-center justify-center rounded-full border-2 border-ink-soft px-[18px] text-foreground disabled:opacity-60 ${FOCUS}`;

export const QUIET = `inline-flex min-h-11 items-center rounded px-1 font-[650] text-wren underline underline-offset-4 disabled:opacity-60 ${FOCUS}`;

export const DANGER = `inline-flex min-h-11 items-center justify-center rounded-full bg-brick px-5 font-[650] text-background disabled:opacity-60 ${FOCUS}`;
