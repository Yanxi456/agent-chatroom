import { createConnection } from 'node:net'
import { WebSocket, WebSocketServer } from 'ws'
import { decodeObject, encodeFrame, FrameDecoder, maxFrame } from './framing.js'

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
    const decoder = new FrameDecoder()

    socket.on('message', (payload, binary) => {
      try {
        if (binary || !payload.length) throw new Error('Expected text')
        decodeObject(payload)
        if (upstream.writableLength > maxFrame * 4) throw new Error('Upstream backlog')
        upstream.write(encodeFrame(payload))
      } catch {
        upstream.destroy()
        socket.close(1008, 'Invalid message or backlog')
      }
    })
    upstream.on('data', chunk => {
      try {
        for (const { json } of decoder.feed(chunk)) {
          if (socket.readyState !== WebSocket.OPEN) break
          if (socket.bufferedAmount > maxFrame * 4) throw new Error('WebSocket backlog')
          socket.send(json)
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
