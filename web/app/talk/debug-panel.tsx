"use client";

import { useState } from "react";
import type { TurnMetrics } from "@/lib/voice/protocol";

// For measuring the voice loop: time to first audio for the last voice turn, the session
// median, where the time went, and the three timestamps the server logs on every turn.

type Props = {
  metrics: TurnMetrics[];
  info: { memory: string; model: string } | null;
};

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = values.toSorted((a, b) => a - b);
  return sorted[Math.ceil(sorted.length / 2) - 1];
}

function ms(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${value.toLocaleString()} ms`;
}

function clock(iso: string | null): string {
  if (!iso) return "–";
  const date = new Date(iso);
  return `${date.toLocaleTimeString([], { hour12: false })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-foreground/70">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

export function DebugPanel({ metrics, info }: Props) {
  const [open, setOpen] = useState(true);
  const voiced = metrics.filter((m) => m.ttfa_ms !== null);
  const last = voiced.at(-1);
  const typical = median(voiced.map((m) => m.ttfa_ms as number));
  const memoryOff = info?.memory.startsWith("off") ?? false;

  return (
    <aside
      aria-label="Voice timing"
      className="fixed right-3 bottom-3 w-[17rem] rounded-lg border border-feather bg-background/95 p-3 text-[13px] shadow-sm backdrop-blur"
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-baseline justify-between rounded text-left focus-visible:outline-2 focus-visible:outline-wren"
      >
        <span className="text-foreground/70">Time to first audio</span>
        <span className="text-lg font-semibold tabular-nums">{last ? ms(last.ttfa_ms) : "–"}</span>
      </button>
      {open ? (
        <div className="mt-2 space-y-3">
          {last ? (
            <>
              <p className="text-foreground/70">
                Median {ms(typical)} over {voiced.length} {voiced.length === 1 ? "turn" : "turns"}. Target under
                1,000.
              </p>
              <dl className="space-y-0.5">
                <Row label="End of speech to turn" value={ms(last.endpoint_ms)} />
                <Row label="Memory search" value={memoryOff ? "off" : ms(last.retrieval_ms)} />
                <Row label="First LLM token" value={ms(last.llm_ttft_ms)} />
                <Row label="First audio byte" value={ms(last.tts_ttfb_ms)} />
              </dl>
              <dl className="space-y-0.5 border-t border-feather pt-2">
                <Row label="Speech ended" value={clock(last.speech_end_at)} />
                <Row label="First token" value={clock(last.first_token_at)} />
                <Row label="First audio" value={clock(last.first_audio_at)} />
              </dl>
            </>
          ) : (
            <p className="text-foreground/70">Say something to Wren. Each spoken turn is timed here.</p>
          )}
          {info ? (
            <p className="border-t border-feather pt-2 text-foreground/70">
              {info.model}. Memory {info.memory}.
            </p>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
