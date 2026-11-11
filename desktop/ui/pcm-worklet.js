class DesktopPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.squareSum = 0;
    this.clipped = 0;
    this.port.onmessage = (event) => {
      if (event.data === "flush") {
        this.flush();
        this.port.postMessage({ flushed: true });
      }
    };
  }
  flush() {
    if (!this.samples.length) return;
    this.port.postMessage({
      quality: {
        rms: Math.sqrt(this.squareSum / this.samples.length),
        clippedFraction: this.clipped / this.samples.length,
      },
    });
    this.squareSum = 0;
    this.clipped = 0;
    const out = Int16Array.from(this.samples);
    this.samples = [];
    this.port.postMessage(out.buffer, [out.buffer]);
  }
  process(inputs) {
    const channels = inputs[0];
    if (channels?.length) {
      for (let i = 0; i < channels[0].length; i++) {
        let value = 0;
        for (const channel of channels) value += channel[i] || 0;
        value = value / channels.length;
        this.squareSum += value * value;
        if (Math.abs(value) >= 0.985) this.clipped++;
        value = Math.max(-1, Math.min(1, value));
        this.samples.push(Math.round(value * (value < 0 ? 32768 : 32767)));
      }
      if (this.samples.length >= 2048) this.flush();
    }
    return true;
  }
}
registerProcessor("desktop-pcm", DesktopPCM);
