// Hears the user start talking over Wren, so playback can pause at once without a round trip
// to the server. The server then confirms with real words, and echo or an "mm-hm" resumes.
//
// Onset needs 100 ms of voice within the last 160 ms, so a dip between syllables doesn't
// restart the count. Measured in Chrome: requiring 120 ms of unbroken voice took 299 ms to
// pause after a short first syllable, against the spec's 300 ms. While Wren is audible the bar
// is higher, since some of Wren's own voice can leak back into the mic even with echo
// cancellation on.

const MIN_RMS = 0.006; // about 200 in 16-bit terms, the same floor the server's detector uses
const FLOOR_CAP = 0.01; // the first frame seeds the floor, but no higher than a quiet room
const WINDOW_MS = 160;
const QUIET_TO_REARM_MS = 150;

export class Vad {
  private readonly startMs: number;
  private readonly speakingBoost: number;
  private floor: number | null = null;
  private recent: { at: number; voiced: boolean }[] = [];
  private quietSince: number | null = null;
  private armed = true;
  onsetAt: number | null = null; // when the voice that fired the last onset began

  constructor({ startMs = 100, speakingBoost = 2.5 }: { startMs?: number; speakingBoost?: number } = {}) {
    this.startMs = startMs;
    this.speakingBoost = speakingBoost;
  }

  push(rms: number, nowMs: number, wrenSpeaking: boolean, frameMs = 20): "onset" | null {
    if (this.floor === null) this.floor = Math.min(Math.max(rms, 1e-5), FLOOR_CAP);
    const bar = Math.max(this.floor * 3, MIN_RMS) * (wrenSpeaking ? this.speakingBoost : 1);
    const voiced = rms > bar;
    this.recent.push({ at: nowMs, voiced });
    while (this.recent.length && this.recent[0].at <= nowMs - WINDOW_MS) this.recent.shift();
    if (voiced) {
      this.quietSince = null;
      const voicedMs = this.recent.filter((frame) => frame.voiced).length * frameMs;
      if (this.armed && voicedMs >= this.startMs) {
        this.armed = false;
        this.onsetAt = this.recent.find((frame) => frame.voiced)!.at;
        return "onset";
      }
      return null;
    }
    this.quietSince ??= nowMs;
    if (nowMs - this.quietSince >= QUIET_TO_REARM_MS) this.armed = true;
    this.floor = rms < this.floor ? rms : this.floor + (rms - this.floor) * 0.02;
    this.floor = Math.max(this.floor, 1e-5);
    return null;
  }
}
