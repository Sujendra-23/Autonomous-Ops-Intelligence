// AudioWorklet: convert Float32 audio frames to 16-bit PCM and batch them.
//
// The AudioContext is created at 16 kHz, so frames arriving here are already at
// the sample rate the backend/STT expects — we only convert float [-1,1] to
// signed 16-bit and post in ~128 ms chunks to keep WebSocket frames reasonable.

class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this._batch = [];
    this._target = 2048; // samples (~128 ms at 16 kHz)
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      const channel = input[0];
      for (let i = 0; i < channel.length; i++) {
        let s = Math.max(-1, Math.min(1, channel[i]));
        this._batch.push(s < 0 ? s * 0x8000 : s * 0x7fff);
      }
      if (this._batch.length >= this._target) {
        const out = Int16Array.from(this._batch);
        this.port.postMessage(out.buffer, [out.buffer]);
        this._batch = [];
      }
    }
    return true; // keep processor alive
  }
}

registerProcessor("pcm-worklet", PCMWorklet);
