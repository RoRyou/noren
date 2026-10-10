// マイク（16 kHz で送る）と、AI の音声（24 kHz）の再生。
// 半二重：AI が話している間はマイクの音声を送らない（タブレットのスピーカーの声を拾わないため）。

export class Mic {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;
  muted = false;

  async start(onChunk: (pcm: ArrayBuffer) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/pcm-recorder.js");
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-recorder");
    this.node.port.onmessage = (e) => {
      if (!this.muted) onChunk(e.data as ArrayBuffer);
    };
    src.connect(this.node);
    // 出力に接続しないと Chrome はノードを動かさない（出力は無音なので、スピーカーからは何も出ない）
    this.node.connect(this.ctx.destination);
    await this.ctx.resume();
  }

  stop(): void {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.ctx = null;
    this.node = null;
    this.stream = null;
  }
}

export class Player {
  private ctx: AudioContext | null = null;
  private nextAt = 0;
  private sources = new Set<AudioBufferSourceNode>();
  onIdle: () => void = () => {};
  onBusy: () => void = () => {};

  // タップの直後に呼ぶ（iPad Safari は操作なしで音を出せない）
  unlock(): void {
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate: 24000 });
    void this.ctx.resume();
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  play(pcm: ArrayBuffer): void {
    this.unlock();
    const ctx = this.ctx!;
    const int16 = new Int16Array(pcm);
    const buf = ctx.createBuffer(1, int16.length, 24000);
    const data = buf.getChannelData(0);
    for (let i = 0; i < int16.length; i++) data[i] = int16[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime + 0.02, this.nextAt);
    src.start(startAt);
    this.nextAt = startAt + buf.duration;
    if (this.sources.size === 0) this.onBusy();
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (this.sources.size === 0) this.onIdle();
    };
  }

  // AI の発話をすぐ止める（お客様が割り込んだとき）
  stop(): void {
    this.sources.forEach((s) => {
      try {
        s.stop();
      } catch {
        /* すでに止まっている */
      }
    });
    this.sources.clear();
    this.nextAt = 0;
    this.onIdle();
  }
}
