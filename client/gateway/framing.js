export const maxFrame = 1_048_576
const utf8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })

export function decodeObject(payload) {
  const json = utf8.decode(payload)
  const value = JSON.parse(json)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected JSON object')
  return { json, value }
}

export function encodeFrame(payload) {
  if (payload.length < 1 || payload.length > maxFrame) throw new Error('Invalid frame length')
  const frame = Buffer.allocUnsafe(payload.length + 4)
  frame.writeUInt32BE(payload.length)
  payload.copy(frame, 4)
  return frame
}

export class FrameDecoder {
  *feed(chunk) {
    this.#remaining = Buffer.concat([this.#remaining, chunk])
    while (this.#remaining.length >= 4) {
      const length = this.#remaining.readUInt32BE()
      if (length < 1 || length > maxFrame) throw new Error('Invalid frame length')
      if (this.#remaining.length < length + 4) return
      const payload = this.#remaining.subarray(4, length + 4)
      this.#remaining = this.#remaining.subarray(length + 4)
      yield decodeObject(payload)
    }
  }

  #remaining = Buffer.alloc(0)
}
