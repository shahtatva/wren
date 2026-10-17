// Mic capture, on the audio thread: resample to 16 kHz mono and post 20 ms frames of 16-bit PCM,
// each with its level, to the main thread. The voice server forwards them to Nova-3 as they come.

const OUT_RATE = 16000;
const FRAME_SAMPLES = 320; // 20 ms at 16 kHz

export class Resampler {
  constructor(inRate, outRate) {
    this.step = inRate / outRate;
    this.pos = 0; // read position, relative to the start of the next block
    this.last = 0; // the previous block's last filtered sample
    this.lowpass = 0;
    // A one-pole low-pass below the new Nyquist, so hiss and sibilance don't alias into speech.
    this.alpha = inRate > outRate ? 1 - Math.exp((-2 * Math.PI * 0.45 * outRate) / inRate) : 1;
  }

  process(input) {
    const filtered = new Float32Array(input.length);
    let lp = this.lowpass;
    for (let i = 0; i < input.length; i++) {
      lp += this.alpha * (input[i] - lp);
      filtered[i] = lp;
    }
    this.lowpass = lp;
    const out = [];
    let pos = this.pos;
    while (pos < filtered.length - 1) {
      const i = Math.floor(pos);
      const a = i < 0 ? this.last : filtered[i];
      out.push(a + (filtered[i + 1] - a) * (pos - i));
      pos += this.step;
    }
    this.pos = pos - filtered.length;
    if (filtered.length) this.last = filtered[filtered.length - 1];
    return Float32Array.from(out);
  }
}

export class Framer {
  constructor(frameSamples = FRAME_SAMPLES) {
    this.size = frameSamples;
    this.buf = new Float32Array(frameSamples);
    this.fill = 0;
  }

  push(samples) {
    const frames = [];
    for (let i = 0; i < samples.length; i++) {
      this.buf[this.fill++] = samples[i];
      if (this.fill === this.size) {
        frames.push(this.frame());
        this.fill = 0;
      }
    }
    return frames;
  }

  frame() {
    const pcm = new Int16Array(this.size);
    let sum = 0;
    for (let i = 0; i < this.size; i++) {
      const v = Math.max(-1, Math.min(1, this.buf[i]));
      sum += v * v;
      pcm[i] = Math.round(v < 0 ? v * 32768 : v * 32767);
    }
    return { pcm, rms: Math.sqrt(sum / this.size) };
  }
}

const Base = globalThis.AudioWorkletProcessor ?? class {};

class CaptureProcessor extends Base {
  constructor() {
    super();
    this.resampler = new Resampler(sampleRate, OUT_RATE);
    this.framer = new Framer();
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (const { pcm, rms } of this.framer.push(this.resampler.process(channel))) {
        this.port.postMessage({ pcm: pcm.buffer, rms }, [pcm.buffer]);
      }
    }
    return true;
  }
}

if (typeof registerProcessor === "function") registerProcessor("wren-capture", CaptureProcessor);
