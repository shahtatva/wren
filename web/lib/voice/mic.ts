// The mic, through the capture worklet: 20 ms frames of 16 kHz PCM, each with its level.
// Echo cancellation, noise suppression and auto gain stay on: they're what keep Wren's own
// voice and the room out of the transcript.

export type MicFrame = { pcm: ArrayBuffer; rms: number };

export type Mic = {
  setEnabled(on: boolean): void;
  stop(): void;
};

export async function startMic(onFrame: (frame: MicFrame) => void): Promise<Mic> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const context = new AudioContext();
  try {
    await context.audioWorklet.addModule("/voice/capture-worklet.mjs");
    await context.resume();
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
    throw error;
  }
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "wren-capture");
  const silent = context.createGain();
  silent.gain.value = 0; // keeps the worklet running without playing the mic back
  source.connect(node).connect(silent).connect(context.destination);

  let enabled = true;
  node.port.onmessage = (event: MessageEvent<MicFrame>) => {
    if (enabled) onFrame(event.data);
  };
  return {
    setEnabled(on) {
      enabled = on;
      stream.getAudioTracks().forEach((track) => (track.enabled = on));
    },
    stop() {
      node.port.onmessage = null;
      stream.getTracks().forEach((track) => track.stop());
      void context.close();
    },
  };
}
