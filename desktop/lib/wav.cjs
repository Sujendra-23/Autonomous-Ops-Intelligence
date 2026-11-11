const fs = require("node:fs/promises");
function header(rate, bytes) {
  if (
    ![16000, 24000, 48000].includes(rate) ||
    bytes < 0 ||
    bytes > 0xffffffff - 36 ||
    bytes % 2
  )
    throw new Error("Invalid WAV parameters.");
  const out = Buffer.alloc(44);
  out.write("RIFF");
  out.writeUInt32LE(bytes + 36, 4);
  out.write("WAVE", 8);
  out.write("fmt ", 12);
  out.writeUInt32LE(16, 16);
  out.writeUInt16LE(1, 20);
  out.writeUInt16LE(1, 22);
  out.writeUInt32LE(rate, 24);
  out.writeUInt32LE(rate * 2, 28);
  out.writeUInt16LE(2, 32);
  out.writeUInt16LE(16, 34);
  out.write("data", 36);
  out.writeUInt32LE(bytes, 40);
  return out;
}
async function writeAll(handle, bytes, position) {
  let offset = 0;
  while (offset < bytes.length) {
    const result = await handle.write(
      bytes,
      offset,
      bytes.length - offset,
      position + offset,
    );
    if (!result.bytesWritten)
      throw new Error("Audio file write made no progress.");
    offset += result.bytesWritten;
  }
}
class WavWriter {
  constructor(file, rate) {
    this.file = file;
    this.rate = rate;
    this.bytes = 0;
    this.pending = Promise.resolve();
    this.handle = null;
    this.error = null;
    this.closing = false;
  }
  async open() {
    this.handle = await fs.open(this.file, "wx", 0o600);
    await writeAll(this.handle, header(this.rate, 0), 0);
  }
  append(data) {
    if (this.closing) throw new Error("The audio file is closing.");
    const chunk = Buffer.from(data);
    if (chunk.length % 2 || chunk.length > 65536)
      throw new Error("Invalid audio frame.");
    const position = 44 + this.bytes;
    this.bytes += chunk.length;
    this.pending = this.pending
      .then(() => writeAll(this.handle, chunk, position))
      .catch((error) => {
        this.error = error;
      });
    return this.pending;
  }
  async close() {
    this.closing = true;
    await this.pending;
    if (!this.handle) return;
    try {
      if (this.error)
        throw new Error(
          "The local audio file could not be fully saved. Check free disk space.",
        );
      await writeAll(this.handle, header(this.rate, this.bytes), 0);
      await this.handle.sync();
    } finally {
      await this.handle.close();
      this.handle = null;
    }
    return { file: this.file, bytes: this.bytes, sampleRate: this.rate };
  }
}
module.exports = { header, WavWriter, writeAll };
