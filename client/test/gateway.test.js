import assert from 'node:assert/strict'
import { once } from 'node:events'
import { createServer } from 'node:net'
import { test } from 'node:test'
import { WebSocket } from 'ws'
import { startGateway } from '../gateway/server.js'

function frame(message) {
  const body = Buffer.from(JSON.stringify(message))
  const header = Buffer.alloc(4)
  header.writeUInt32BE(body.length)
  return Buffer.concat([header, body])
}

test('forwards UTF-8 requests, fragmented and coalesced frames, and closes the TCP session', { timeout: 5000 }, async t => {
  const backend = createServer()
  backend.listen(0, '127.0.0.1')
  t.after(() => backend.close())
  await once(backend, 'listening')

  const gateway = startGateway({ port: 0, tcpPort: backend.address().port })
  t.after(() => gateway.close())
  await once(gateway, 'listening')

  const accepted = once(backend, 'connection')
  const browser = new WebSocket('ws://127.0.0.1:' + gateway.address().port + '/ws', {
    origin: 'http://localhost:5173',
  })
  const opened = once(browser, 'open')
  t.after(() => browser.terminate())
  const [upstream] = await accepted
  t.after(() => upstream.destroy())
  await opened

  const request = { v: 1, type: 'message.send', request_id: '1', data: { text: '你好🙂' } }
  const expected = frame(request)
  const received = new Promise(resolve => {
    let buffer = Buffer.alloc(0)
    upstream.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk])
      if (buffer.length >= expected.length) resolve(buffer)
    })
  })
  browser.send(JSON.stringify(request))
  assert.deepEqual(await received, expected)

  const reply = { v: 1, type: 'response', request_id: '1', data: { ok: true, result: {} } }
  const push = { v: 1, type: 'presence.changed', data: { users: [] } }
  const events = new Promise(resolve => {
    const messages = []
    browser.on('message', data => {
      messages.push(JSON.parse(data))
      if (messages.length === 2) resolve(messages)
    })
  })
  const replyFrame = frame(reply)
  upstream.write(replyFrame.subarray(0, 2))
  await new Promise(resolve => setTimeout(resolve, 20))
  upstream.write(Buffer.concat([replyFrame.subarray(2), frame(push)]))
  assert.deepEqual(await events, [reply, push])

  const finished = once(upstream, 'close')
  browser.close()
  await finished
})
