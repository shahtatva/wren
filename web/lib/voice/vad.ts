// Hears the user start talking over Wren, so playback can pause at once without a round trip
// to the server. The server then confirms with real words, and echo or an "mm-hm" resumes.
//
// Onset needs 200 ms of voice within the last 260 ms, so a dip between syllables doesn't
// restart the count, and a laugh or a clatter in the room doesn't pause Wren. A clear start
// still pauses within the spec's 300 ms. While Wren is audible the bar is higher, since some
// of Wren's own voice can leak back into the mic even with echo cancellation on.
//
// Other people talking nearby mustn't pause Wren either (docs/superpowers/specs/2026-11-06-room-noise-design.md).
// What sets the owner apart is distance: they talk a few inches from the phone, so their voice
// reaches the mic much louder than anyone a metre away. So while Wren speaks, when the owner is
// normally quiet, the detector learns how loud the room's talkers get, and only voice clearly
// louder than that counts. The room's quiet floor alone can't do this: two people taking turns
// leave gaps, and the floor sits in those gaps.

const MIN_RMS = 0.006; // about 200 in 16-bit terms, the same floor the server's detector uses
const FLOOR_CAP = 0.01; // the first frame seeds the floor, but no higher than a quiet room
const WINDOW_MS = 260;
const QUIET_TO_REARM_MS = 150;

// Mirrored in voice/app/voice/vad.py.
const ROOM_FRAMES = 400; // the last 8 s of 20 ms frames heard while Wren speaks
const ROOM_RECENT_FRAMES = 120; // and the last 2.4 s of them: new talk raises the bar within a second, two cut-ins don't
const ROOM_MIN_FRAMES = 50; // a second of them before the room's level counts
const ROOM_PERCENTILE = 0.8; // how loud the talkers get; the owner's short cut-ins stay under a fifth
const ROOM_MARGIN = 2; // the owner's voice must be twice as loud as the room's talkers (6 dB)
const ROOM_TRUST_FRAMES = 150; // no pause until 3 s of the room are heard: early talk mustn't count as the owner

/** How loud the room's talkers get: a high percentile of the levels heard while the owner is quiet. */
export class RoomTalk {
  private levels: number[] = [];

  observe(rms: number): void {
    this.levels.push(rms);
    if (this.levels.length > ROOM_FRAMES) this.levels.shift();
  }

  /** How many of the last ROOM_FRAMES levels it has heard. */
  get frames(): number {
    return this.levels.length;
  }

  level(): number | null {
    if (this.levels.length < ROOM_MIN_FRAMES) return null;
    return Math.max(percentile(this.levels), percentile(this.levels.slice(-ROOM_RECENT_FRAMES)));
  }
}

function percentile(levels: number[]): number {
  const sorted = [...levels].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * ROOM_PERCENTILE))];
}

export class Vad {
  private readonly startMs: number;
  private readonly speakingBoost: number;
  private readonly room = new RoomTalk();
  private floor: number | null = null;
  private recent: { at: number; voiced: boolean }[] = [];
  private quietSince: number | null = null;
  private armed = true;
  onsetAt: number | null = null; // when the voice that fired the last onset began

  constructor({ startMs = 200, speakingBoost = 2.5 }: { startMs?: number; speakingBoost?: number } = {}) {
    this.startMs = startMs;
    this.speakingBoost = speakingBoost;
  }

  /** `roomFrame`: the owner is normally quiet now (Wren is speaking, or about to), so learn the room from it. */
  push(rms: number, nowMs: number, wrenSpeaking: boolean, frameMs = 20, roomFrame = wrenSpeaking): "onset" | null {
    if (this.floor === null) this.floor = Math.min(Math.max(rms, 1e-5), FLOOR_CAP);
    if (roomFrame) this.room.observe(rms);
    let bar = Math.max(this.floor * 3, MIN_RMS) * (wrenSpeaking ? this.speakingBoost : 1);
    const talkers = wrenSpeaking ? this.room.level() : null;
    if (talkers !== null) bar = Math.max(bar, talkers * ROOM_MARGIN);
    // Until the room has been heard for a while, people who start talking as Wren does could be
    // taken for the owner: Wren keeps talking.
    if (wrenSpeaking && this.room.frames < ROOM_TRUST_FRAMES) bar = Infinity;
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
