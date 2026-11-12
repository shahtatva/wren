import { notFound } from "next/navigation";
import type { CSSProperties } from "react";
import { Bird, Perch } from "@/lib/bird";
import type { Pose } from "@/lib/bird-state";
import { MockTile, SwitchTile } from "./mock-tile";

// Development only: Wren in every pose on its branch, for checking the bird by eye. Speaking holds a
// steady voice level here; on the talk page the level comes from Wren's voice.
const POSES: Pose[] = ["perched", "looking", "reconnecting", "speaking", "thinking", "leaning", "tucked", "listening"];

export default function PerchPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return (
    <main className="mx-auto grid w-full max-w-[60rem] gap-x-8 gap-y-6 px-5 py-6 sm:grid-cols-2">
      {POSES.map((pose) => (
        <section key={pose} aria-label={pose}>
          <h2 className="text-sm font-[650] text-ink-soft">{pose}</h2>
          <div style={pose === "speaking" ? ({ "--level": 0.6 } as CSSProperties) : undefined}>
            <Perch pose={pose}>
              <div className="perch-bird">
                <Bird />
              </div>
            </Perch>
          </div>
        </section>
      ))}
      <section aria-label="mock interview">
        <h2 className="text-sm font-[650] text-ink-soft">mock interview</h2>
        <MockTile />
      </section>
      <section aria-label="talk button handoff">
        <h2 className="text-sm font-[650] text-ink-soft">mock starts and ends</h2>
        <SwitchTile />
      </section>
    </main>
  );
}
