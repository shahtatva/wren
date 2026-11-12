import type { Ref } from "react";
import { leadKey, type Line } from "@/lib/talk-transcript";

// Wren's words, so set casual. The page shows them before anyone has spoken.
const EMPTY = "Tap Wren and say hi. I'll listen, and you can cut in whenever you like.";

// The newest line that isn't yours leads, large. Wren speaks casual; an interviewer speaks heavy and
// linear, with no bird flavor. Earlier lines, and any line you cut in on, are soft.
function lineClass(line: Line, lead: boolean): string {
  if (line.speaker === "user") return "ml-auto max-w-[78%] text-right text-base leading-[1.4] text-ink-soft";
  const tone = line.cut ? "text-ink-soft" : "text-foreground";
  if (line.speaker === "interviewer") {
    return lead ? `text-2xl leading-tight font-extrabold ${tone}` : "text-[17px] leading-[1.4] font-extrabold text-ink-soft";
  }
  return lead ? `casual text-[27px] leading-[1.22] font-[560] ${tone}` : "casual text-[17px] leading-[1.4] text-ink-soft";
}

/** The conversation, newest at the bottom, just above Wren. Each line keeps its speaker label first:
 * "You" and "Wren" for screen readers only, and an interviewer's name in view, so two voices that aren't
 * yours can be told apart. */
export function Transcript({ lines, caption, bottomRef }: { lines: Line[]; caption: string; bottomRef: Ref<HTMLDivElement> }) {
  const lead = leadKey(lines);
  return (
    <section aria-label="Conversation" className="flex-1 overflow-y-auto">
      <div className="flex min-h-full flex-col justify-end gap-3.5 py-4">
        {lines.length === 0 && !caption ? (
          <p className="casual max-w-[30ch] text-[27px] leading-[1.22]">{EMPTY}</p>
        ) : (
          <ol className="flex flex-col gap-3.5">
            {lines.map((line) => (
              <li key={line.key} className="flex flex-col">
                <span className={line.speaker === "interviewer" ? "text-[13px] font-[650] text-ink-soft" : "sr-only"}>{line.label}</span>
                <p className={`break-words ${lineClass(line, line.key === lead)}`}>
                  {line.text}
                  {line.cut ? <span className="sr-only"> (you cut in here)</span> : null}
                </p>
              </li>
            ))}
            {caption ? (
              <li className="flex flex-col">
                <span className="sr-only">You</span>
                <p className="ml-auto max-w-[78%] text-right text-base leading-[1.4] break-words">{caption}</p>
              </li>
            ) : null}
          </ol>
        )}
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
