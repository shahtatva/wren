// Wren's voice, through the playback worklet in a 24 kHz AudioContext, the rate Aura-2 sends.
// "webaudio" plays straight to the speakers. "element" routes the same audio through an
// <audio> element, which some browsers treat as an echo-cancellation reference while they
// may not for Web Audio. The M0 echo spike decides which one to keep.

export type Position = { turn: number; seg: number; playedMs: number };

export type PlayerEvent =
  | { type: "segment-start" | "segment-end"; turn: number; seg: number }
  | { type: "drained" }
  | { type: "level"; rms: number };

export type Route = "webaudio" | "element";

export class Player {
  private requests = new Map<number, (position: Position | null) => void>();
  private nextId = 1;
  private playing = false;

  private constructor(
    private readonly context: AudioContext,
    private readonly node: AudioWorkletNode,
    private readonly element: HTMLAudioElement | null,
    onEvent: (event: PlayerEvent) => void,
  ) {
    node.port.onmessage = ({ data }) => {
      if (data.type === "position") {
        this.requests.get(data.id)?.(data.position);
        this.requests.delete(data.id);
        return;
      }
      if (data.type === "segment-start") this.playing = true;
      if (data.type === "drained") this.playing = false;
      onEvent(data as PlayerEvent);
    };
  }

  static async create(onEvent: (event: PlayerEvent) => void, route: Route = "webaudio"): Promise<Player> {
    const context = new AudioContext({ sampleRate: 24000 });
    await context.audioWorklet.addModule("/voice/playback-worklet.mjs");
    await context.resume();
    const node = new AudioWorkletNode(context, "wren-playback", { outputChannelCount: [1] });
    let element: HTMLAudioElement | null = null;
    if (route === "element") {
      const out = context.createMediaStreamDestination();
      node.connect(out);
      element = new Audio();
      element.srcObject = out.stream;
      await element.play();
    } else {
      node.connect(context.destination);
    }
    return new Player(context, node, element, onEvent);
  }

  /** Wren is audible right now, so the barge-in detector should ask for more energy. */
  get audible(): boolean {
    return this.playing;
  }

  push(turn: number, seg: number, pcm: Int16Array): void {
    const copy = pcm.slice(); // its own buffer, so it can be transferred
    this.node.port.postMessage({ type: "push", turn, seg, pcm: copy.buffer }, [copy.buffer]);
  }

  complete(turn: number, seg: number): void {
    this.node.port.postMessage({ type: "complete", turn, seg });
  }

  private ask(type: "pause" | "position"): Promise<Position | null> {
    const id = this.nextId++;
    return new Promise((resolve) => {
      this.requests.set(id, resolve);
      this.node.port.postMessage({ type, id });
    });
  }

  /** Pause at once (a 10 ms fade) and say where playback stopped. */
  pause(): Promise<Position | null> {
    return this.ask("pause");
  }

  position(): Promise<Position | null> {
    return this.ask("position");
  }

  resume(): void {
    this.node.port.postMessage({ type: "resume" });
  }

  /** Drop everything queued. Late audio for `turn` is ignored too. */
  flush(turn?: number): void {
    this.playing = false;
    this.node.port.postMessage({ type: "flush", turn });
  }

  /** For a new connection, which numbers turns from 1 again. */
  reset(): void {
    this.playing = false;
    this.node.port.postMessage({ type: "reset" });
  }

  close(): void {
    this.node.port.onmessage = null;
    this.element?.pause();
    void this.context.close();
  }
}
