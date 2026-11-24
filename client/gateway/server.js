import { createConnection } from 'node:net'
import { WebSocket, WebSocketServer } from 'ws'

const maxFrame = 1_048_576
const utf8 = new TextDecoder('utf-8', { fatal: true })

function validate(payload) {
  const json = utf8.decode(payload)
  const value = JSON.parse(json)
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Expected JSON object')
  }
  return json
}

export function startGateway({
  port = Number(process.env.GATEWAY_PORT ?? 8766),
  tcpHost = process.env.CHAT_HOST ?? '127.0.0.1',
  tcpPort = Number(process.env.CHAT_PORT ?? 8765),
  allowedOrigin = process.env.CHAT_ORIGIN,
} = {}) {
  const server = new WebSocketServer({
    host: '127.0.0.1', port, path: '/ws', maxPayload: maxFrame,
    verifyClient: ({ origin }) => allowedOrigin
      ? origin === allowedOrigin
      : /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin ?? ''),
  })
  server.on('connection', socket => {
    const upstream = createConnection({ host: tcpHost, port: tcpPort })
    let remaining = Buffer.alloc(0)

    socket.on('message', (payload, binary) => {
      try {
        if (binary || !payload.length) throw new Error('Expected text')
        validate(payload)
        if (upstream.writableLength > maxFrame * 4) throw new Error('Upstream backlog')
        const frame = Buffer.allocUnsafe(payload.length + 4)
        frame.writeUInt32BE(payload.length)
        payload.copy(frame, 4)
        upstream.write(frame)
      } catch {
        upstream.destroy()
        socket.close(1008, 'Invalid message or backlog')
      }
    })
    upstream.on('data', chunk => {
      try {
        remaining = Buffer.concat([remaining, chunk])
        while (remaining.length >= 4) {
          const length = remaining.readUInt32BE()
          if (length === 0 || length > maxFrame) throw new Error('Invalid frame length')
          if (remaining.length < length + 4) break
          const json = validate(remaining.subarray(4, length + 4))
          if (socket.readyState !== WebSocket.OPEN) break
          if (socket.bufferedAmount > maxFrame * 4) throw new Error('WebSocket backlog')
          socket.send(json)
          remaining = remaining.subarray(length + 4)
        }
      } catch {
        upstream.destroy()
        socket.close(1011, 'Invalid upstream frame or backlog')
      }
    })
    upstream.on('error', () => socket.close(1011, 'TCP service unavailable'))
    upstream.on('close', () => socket.close(1000, 'TCP connection closed'))
    socket.on('error', () => upstream.destroy())
    socket.on('close', () => upstream.destroy())
  })
  return server
}
