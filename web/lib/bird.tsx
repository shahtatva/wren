// Wren, drawn: a small wren that perches on a branch and is the talk button. Its posture shows what Wren
// is doing (spec: "The bird"). CSS in app/globals.css colors and poses the parts by class. No hooks, so
// the app icon (next/og) can render it too, with its colors as attributes.

import type { ReactNode, Ref } from "react";
import type { Pose } from "./bird-state.ts";

export type BirdPaint = { body: string; dark: string; bars: string; belly: string; brow: string; eye: string; beak: string; legs: string };

// Apricot day's bird, for the app icon. The same values are --bird, --bird-dark, … in globals.css.
export const BIRD_DAY: BirdPaint = {
  body: "#8a4421",
  dark: "#6a3017",
  bars: "#3b1a0c",
  belly: "#f7ddb8",
  brow: "#fff4e4",
  eye: "#24120a",
  beak: "#3b2416",
  legs: "#6a3f22",
};

// One drawing for both: in the app each part gets its class, and CSS colors and poses it. With `paint`
// (the icon), colors are attributes, and what only a pose shows (shut eye, song lines, sound arcs) is left out.
function Parts({ paint }: { paint?: BirdPaint }) {
  const group = (className: string) => (paint ? {} : { className });
  const fill = (className: string, color: keyof BirdPaint) => (paint ? { fill: paint[color] } : { className });
  const line = (className: string, color: keyof BirdPaint, width: number) =>
    paint
      ? { fill: "none", stroke: paint[color], strokeWidth: width, strokeLinecap: "round" as const }
      : { className, strokeWidth: width };
  return (
    <>
      <g {...group("bird-hop")}>
        <g {...group("bird-tail")}>
          <path {...fill("bird-dark", "dark")} d="M46 68 L23 22 Q21 15 28 14 L33 14 Q37 15 38 19 L58 63 Z" />
          <path {...line("bird-bars", "bars", 2.4)} d="M28 27 L37 24 M32 37 L41 34 M37 47 L45 44" />
        </g>
        <path {...line("bird-legs", "legs", 2.6)} d="M70 104 L67 116 M82 104 L82 116" />
        <ellipse {...fill("bird-body", "body")} cx={72} cy={78} rx={35} ry={28} />
        <path {...fill("bird-belly", "belly")} d="M58 98 Q78 106 100 92 Q107 82 103 72 Q90 88 58 98 Z" />
        <path {...fill("bird-dark", "dark")} d="M45 74 Q62 58 87 69 Q81 88 56 88 Q45 84 45 74 Z" />
        <path {...line("bird-bars", "bars", 2.2)} d="M56 71 L60 84 M64 68 L68 83 M72 68 L75 82" />
        <g {...group("bird-head")}>
          <circle {...fill("bird-body", "body")} cx={98} cy={54} r={19} />
          <path {...line("bird-brow", "brow", 3.2)} d="M85 48 Q97 40 111 47" />
          <circle {...fill("bird-eye", "eye")} cx={103} cy={53} r={3.4} />
          {paint ? null : <path className="bird-eye-shut" d="M99.5 54 Q103 57 106.5 54" />}
          <path {...fill("bird-beak bird-beak-top", "beak")} d="M114 51 Q125 52 136 58 Q125 57.5 115 56.5 Z" />
          <path {...fill("bird-beak bird-beak-bot", "beak")} d="M115 56.5 Q124 58 133 59.5 Q124 61.5 114 60 Z" />
        </g>
      </g>
      {paint ? null : (
        <>
          <g className="bird-song">
            <path className="s1" d="M142.6 53.4 A8 8 0 0 1 142.6 62.6" />
            <path className="s2" d="M147.5 50 A14 14 0 0 1 147.5 66" />
            <path className="s3" d="M152.4 46.5 A20 20 0 0 1 152.4 69.5" />
          </g>
          <g className="bird-hear">
            <path className="h1" d="M105.2 20.5 A30 30 0 0 1 126 35" />
            <path className="h2" d="M106.6 12.6 A38 38 0 0 1 132.9 31" />
            <path className="h3" d="M108 4.7 A46 46 0 0 1 139.8 27" />
          </g>
        </>
      )}
    </>
  );
}

/** The bird alone. Give it a pose to pose it by itself, or put it in a Perch, which poses what sits on it.
 * `width` sets its size in pixels, which the icon needs. */
export function Bird({ pose, className, paint, width }: { pose?: Pose; className?: string; paint?: BirdPaint; width?: number }) {
  const size = width ? { width, height: Math.round(width * 0.75) } : {};
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 160 120"
      aria-hidden
      className={paint ? undefined : `bird ${className ?? ""}`.trim()}
      data-pose={pose}
      {...size}
    >
      <Parts paint={paint} />
    </svg>
  );
}

/** The branch, edge to edge, and whatever sits on it (class `perch-bird`). Wren's voice level, set as
 * --level on the perch, reaches the beak and the song lines. */
export function Perch({ pose, ref, className, children }: { pose: Pose; ref?: Ref<HTMLDivElement>; className?: string; children: ReactNode }) {
  return (
    <div ref={ref} className={`perch ${className ?? ""}`.trim()} data-pose={pose}>
      <svg className="perch-branch" viewBox="0 0 390 60" preserveAspectRatio="none" aria-hidden>
        <path className="branch-line" d="M-10 34 C 90 26, 170 28, 250 31 S 360 37, 400 28" />
        <path className="branch-twig" d="M292 33 Q312 20 332 14" />
        <path className="branch-leaf" d="M332 14 q10 -10 22 -6 q-8 12 -22 6 Z" />
        <path className="branch-twig" d="M60 30 Q50 44 36 50" />
        <path className="branch-leaf" d="M36 50 q-14 2 -20 12 q14 2 20 -12 Z" />
      </svg>
      {children}
    </div>
  );
}
