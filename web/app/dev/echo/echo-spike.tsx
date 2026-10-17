"use client";

import { useState } from "react";

// Plays a clip of Wren through one of three routes while recording the mic with echo
// cancellation on, then saves the recording. Run each route on laptop speakers and stay quiet.
// `python -m app.tools.echo_spike count <files>` then counts how many of Wren's words leaked
// back into the mic. The route with about zero is the one the player should use.

type Route = "webaudio" | "element" | "loopback";

const ROUTES: { id: Route; label: string; how: string }[] = [
  { id: "webaudio", label: "Web Audio", how: "Straight to the speakers, as the talk page plays today." },
  { id: "element", label: "Web Audio into an audio element", how: "Through a MediaStream and an <audio> element." },
  { id: "loopback", label: "WebRTC loopback", how: "Through a local peer connection, then an <audio> element." },
];

async function loopback(stream: MediaStream): Promise<{ stream: MediaStream; close: () => void }> {
  const sender = new RTCPeerConnection();
  const receiver = new RTCPeerConnection();
  sender.onicecandidate = (e) => e.candidate && void receiver.addIceCandidate(e.candidate);
  receiver.onicecandidate = (e) => e.candidate && void sender.addIceCandidate(e.candidate);
  const received = new Promise<MediaStream>((resolve) => {
    receiver.ontrack = (e) => resolve(e.streams[0] ?? new MediaStream([e.track]));
  });
  stream.getTracks().forEach((track) => sender.addTrack(track, stream));
  await sender.setLocalDescription(await sender.createOffer());
  await receiver.setRemoteDescription(sender.localDescription!);
  await receiver.setLocalDescription(await receiver.createAnswer());
  await sender.setRemoteDescription(receiver.localDescription!);
  return { stream: await received, close: () => (sender.close(), receiver.close()) };
}

async function run(route: Route): Promise<Blob> {
  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const recorder = new MediaRecorder(mic);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => chunks.push(e.data);

  const context = new AudioContext();
  const clip = await context.decodeAudioData(await (await fetch("/dev/echo-clip.wav")).arrayBuffer());
  const source = context.createBufferSource();
  source.buffer = clip;
  let cleanup = () => {};
  if (route === "webaudio") {
    source.connect(context.destination);
  } else {
    const out = context.createMediaStreamDestination();
    source.connect(out);
    const played = route === "loopback" ? await loopback(out.stream) : { stream: out.stream, close: () => {} };
    const element = new Audio();
    element.srcObject = played.stream;
    await element.play();
    cleanup = () => (element.pause(), played.close());
  }

  recorder.start();
  source.start();
  await new Promise((resolve) => (source.onended = resolve));
  await new Promise((resolve) => setTimeout(resolve, 500)); // catch the tail
  const done = new Promise((resolve) => (recorder.onstop = resolve));
  recorder.stop();
  await done;
  cleanup();
  mic.getTracks().forEach((track) => track.stop());
  void context.close();
  return new Blob(chunks, { type: recorder.mimeType });
}

export function EchoSpike() {
  const [busy, setBusy] = useState<Route | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const record = async (route: Route) => {
    setBusy(route);
    setError(null);
    try {
      const blob = await run(route);
      const name = `echo-${route}.${blob.type.includes("ogg") ? "ogg" : "webm"}`;
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      link.click();
      setDone((prev) => [...prev, name]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="mx-auto w-full max-w-[40rem] space-y-6 px-5 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Echo check</h1>
      <p className="leading-relaxed text-foreground/70">
        Use laptop speakers, not headphones, and stay quiet while each clip plays. Each run records the mic and saves
        the recording. Then run <code>python -m app.tools.echo_spike count</code> on the files.
      </p>
      <ol className="space-y-4">
        {ROUTES.map((route) => (
          <li key={route.id} className="flex items-center justify-between gap-4 border-t border-feather pt-4">
            <div>
              <p className="font-medium">{route.label}</p>
              <p className="text-sm text-foreground/70">{route.how}</p>
            </div>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => record(route.id)}
              aria-label={`Record with ${route.label}`}
              className="shrink-0 rounded-md px-3 py-2 font-medium text-wren hover:bg-wren/10 focus-visible:outline-2 focus-visible:outline-wren disabled:opacity-50"
            >
              {busy === route.id ? "Recording" : "Record"}
            </button>
          </li>
        ))}
      </ol>
      {done.length ? <p className="text-sm">Saved {done.join(", ")}.</p> : null}
      {error ? <p className="text-sm text-brick">{error}</p> : null}
    </main>
  );
}
