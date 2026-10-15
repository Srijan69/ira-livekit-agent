/**
 * Loudness, 0..1, from an analyser's frequency bins.
 *
 * The mean rather than the peak: a single loud frequency would make the
 * waveform jump, while the mean tracks loudness the way it is heard. Speech
 * sits low in this range, so it is lifted into something visible and then
 * clamped.
 */
export function levelFrom(data: ArrayLike<number>): number {
  if (data.length === 0) return 0;
  let total = 0;
  for (let i = 0; i < data.length; i += 1) total += data[i] ?? 0;
  return Math.min(1, (total / data.length / 255) * 2.4);
}

type Tap = {
  source: MediaStreamAudioSourceNode;
  analyser: AnalyserNode;
  data: Uint8Array<ArrayBuffer>;
};

/** Playback for IRA's voice, plus loudness taps for the waveform. */
export class AudioMeter {
  private context: AudioContext | null = null;
  private readonly taps = new Map<"agent" | "mic", Tap>();
  private element: HTMLMediaElement | null = null;

  /**
   * Play her track and tap it.
   *
   * The element LiveKit creates does the playing, and it goes into the DOM so
   * nothing can collect it mid-sentence. The analyser only observes and is
   * deliberately not connected to the speakers -- doing so would play her
   * twice.
   */
  attachAgent(track: {
    attach(): HTMLMediaElement;
    mediaStreamTrack: MediaStreamTrack;
  }): void {
    const element = track.attach();
    element.dataset["iraVoice"] = "";
    element.style.display = "none";
    if (this.element && this.element !== element) this.element.remove();
    if (!element.isConnected) document.body.appendChild(element);
    this.element = element;
    this.tap("agent", track.mediaStreamTrack);
  }

  /** Tap the microphone, so the waveform follows the boss while they talk. */
  attachMic(track: MediaStreamTrack): void {
    this.tap("mic", track);
  }

  level(which: "agent" | "mic"): number {
    const tap = this.taps.get(which);
    if (!tap) return 0;
    tap.analyser.getByteFrequencyData(tap.data);
    return levelFrom(tap.data);
  }

  close(): void {
    for (const tap of this.taps.values()) tap.source.disconnect();
    this.taps.clear();
    void this.context?.close();
    this.context = null;
    this.element?.remove();
    this.element = null;
  }

  private tap(which: "agent" | "mic", track: MediaStreamTrack): void {
    try {
      const context = this.context ?? new AudioContext();
      this.context = context;
      // Electron's default autoplay policy needs no gesture, but a suspended
      // context would read silence forever, so ask anyway.
      void context.resume();

      this.taps.get(which)?.source.disconnect();
      const source = context.createMediaStreamSource(new MediaStream([track]));
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.6;
      source.connect(analyser);

      this.taps.set(which, {
        source,
        analyser,
        data: new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount)),
      });
    } catch {
      // No analyser means a flat waveform, never a broken call.
    }
  }
}
