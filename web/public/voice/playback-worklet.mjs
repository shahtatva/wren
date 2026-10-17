// Wren's voice, on the audio thread. Segments of 24 kHz PCM play in order. Playback can pause
// and resume with a 10 ms fade (no clicks), report exactly how much of the current segment has
// played (the server keeps only the words the user heard), and drop a cancelled turn at once.

export class PlaybackQueue {
  constructor(sampleRate = 24000, fadeMs = 10) {
    this.rate = sampleRate;
    this.step = 1 / Math.max(1, Math.round((fadeMs / 1000) * sampleRate));
    this.segments = []; // { turn, seg, chunks, offset, complete, played, started }
    this.gain = 1;
    this.target = 1;
    this.dropped = []; // turns flushed by an interruption; their late audio is ignored
    this.hadAudio = false;
  }

  find(turn, seg) {
    return this.segments.find((s) => s.turn === turn && s.seg === seg);
  }

  push(turn, seg, int16) {
    if (this.dropped.includes(turn)) return;
    const data = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) data[i] = int16[i] / 32768;
    let segment = this.find(turn, seg);
    if (!segment) {
      segment = { turn, seg, chunks: [], offset: 0, complete: false, played: 0, started: false };
      this.segments.push(segment);
    }
    segment.chunks.push(data);
    this.hadAudio = true;
  }

  complete(turn, seg) {
    const segment = this.find(turn, seg);
    if (segment) segment.complete = true;
  }

  pause() {
    this.target = 0;
  }

  resume() {
    this.target = 1;
  }

  flush(turn) {
    this.segments = [];
    this.gain = this.target = 1;
    this.hadAudio = false;
    if (turn !== undefined) {
      this.dropped.push(turn);
      if (this.dropped.length > 16) this.dropped.shift();
    }
  }

  /** A new connection numbers turns from 1 again: forget everything, including dropped turns. */
  reset() {
    this.flush();
    this.dropped = [];
  }

  position() {
    const s = this.segments[0];
    return s ? { turn: s.turn, seg: s.seg, playedMs: (s.played / this.rate) * 1000 } : null;
  }

  get active() {
    return this.segments.length > 0;
  }

  render(out) {
    const events = [];
    for (let i = 0; i < out.length; i++) {
      if (this.gain === 0 && this.target === 0) {
        out[i] = 0; // paused: hold the position
        continue;
      }
      let sample = 0;
      while (this.segments.length) {
        const s = this.segments[0];
        if (s.chunks.length) {
          if (!s.started) {
            s.started = true;
            events.push({ type: "segment-start", turn: s.turn, seg: s.seg });
          }
          const chunk = s.chunks[0];
          sample = chunk[s.offset++];
          s.played++;
          if (s.offset >= chunk.length) {
            s.chunks.shift();
            s.offset = 0;
          }
          break;
        }
        if (!s.complete) break; // more audio is on its way: play silence until it lands
        events.push({ type: "segment-end", turn: s.turn, seg: s.seg });
        this.segments.shift();
        if (!this.segments.length && this.hadAudio) {
          this.hadAudio = false;
          events.push({ type: "drained" });
        }
      }
      out[i] = sample * this.gain;
      if (this.gain !== this.target) {
        // Snap within a step, so repeated steps land exactly on 0 or 1 despite float drift.
        const delta = this.target - this.gain;
        this.gain = Math.abs(delta) <= this.step + 1e-9 ? this.target : this.gain + Math.sign(delta) * this.step;
      }
    }
    return events;
  }
}

const Base = globalThis.AudioWorkletProcessor ?? class {};
const LEVEL_EVERY = 8; // blocks: about every 43 ms at 24 kHz

class PlaybackProcessor extends Base {
  constructor() {
    super();
    this.queue = new PlaybackQueue(sampleRate);
    this.blocks = 0;
    this.level = 0;
    this.port.onmessage = ({ data }) => {
      const q = this.queue;
      if (data.type === "push") q.push(data.turn, data.seg, new Int16Array(data.pcm));
      else if (data.type === "complete") q.complete(data.turn, data.seg);
      else if (data.type === "resume") q.resume();
      else if (data.type === "flush") q.flush(data.turn);
      else if (data.type === "reset") q.reset();
      else if (data.type === "pause" || data.type === "position") {
        if (data.type === "pause") q.pause();
        this.port.postMessage({ type: "position", id: data.id, position: q.position() });
      }
    };
  }

  process(_inputs, outputs) {
    const channels = outputs[0];
    const out = channels[0];
    for (const event of this.queue.render(out)) this.port.postMessage(event);
    for (let c = 1; c < channels.length; c++) channels[c].set(out);
    let sum = 0;
    for (let i = 0; i < out.length; i++) sum += out[i] * out[i];
    this.level = Math.max(this.level * 0.8, Math.sqrt(sum / out.length));
    if (++this.blocks % LEVEL_EVERY === 0) this.port.postMessage({ type: "level", rms: this.level });
    return true;
  }
}

if (typeof registerProcessor === "function") registerProcessor("wren-playback", PlaybackProcessor);
