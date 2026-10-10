// マイク音声を 16 kHz・16 bit・モノラルに変換して 20 ms ごとに送る AudioWorklet。
class PcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.buf = new Int16Array(320);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    // 単純な間引き（平均）でのダウンサンプリング
    while (this.pos < ch.length) {
      const start = Math.floor(this.pos);
      const end = Math.min(ch.length, Math.floor(this.pos + this.ratio));
      let sum = 0;
      for (let i = start; i < end; i++) sum += ch[i];
      const v = end > start ? sum / (end - start) : ch[start];
      this.buf[this.n++] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.slice().buffer, []);
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}

registerProcessor("pcm-recorder", PcmRecorder);
