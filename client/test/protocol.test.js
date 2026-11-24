import assert from 'node:assert/strict'
import { EventEmitter, once } from 'node:events'
import { test } from 'node:test'
import { WebSocket } from 'ws'
import { startGateway } from '../gateway/server.js'
import { connect } from '../src/transport.js'
import { mergeMessage } from '../src/message-cache.js'
import { startMockServer } from './fixtures/mock-server.js'

const room = 'room:general'
const password = 'test-password'
class BrowserSocket extends WebSocket {
  constructor(endpoint) {
    super(endpoint, { origin: 'http://127.0.0.1:5173' })
  }
}

async function setup(t, options = {}) {
  const mock = startMockServer({ port: 0, agentDelay: 25, ...options })
  t.after(() => mock.close())
  await once(mock.server, 'listening')
  const gateway = startGateway({ port: 0, tcpPort: mock.server.address().port })
  t.after(() => {
    for (const client of gateway.clients) client.terminate()
    gateway.close()
  })
  await once(gateway, 'listening')
  const endpoint = 'ws://127.0.0.1:' + gateway.address().port + '/ws'

  return async (timeout = 1000) => {
    const seen = []
    const changes = new EventEmitter()
    const connection = connect({
      endpoint, Socket: BrowserSocket, timeout,
      event(message) {
        seen.push(message)
        changes.emit('event', message)
      },
      closed() { changes.emit('closed') },
    })
    t.after(() => connection.close())
    await connection.ready
    return {
      ...connection,
      seen,
      disconnect() {
        const closed = once(changes, 'closed')
        connection.close()
        return closed
      },
      async waitFor(type, matches = () => true) {
        const found = seen.find(message => message.type === type && matches(message.data))
        if (found) return found.data
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => {
            changes.off('event', listener)
            reject(new Error('Missing event: ' + type))
          }, 1500)
          function listener(message) {
            if (message.type !== type || !matches(message.data)) return
            clearTimeout(timer)
            changes.off('event', listener)
            resolve(message.data)
          }
          changes.on('event', listener)
        })
      },
    }
  }
}

async function authenticate(browser, username, register = true) {
  if (register) await browser.request('auth.register', { username, password })
  return (await browser.request('auth.login', { username, password })).user
}

const send = (browser, text, conversationId = room) =>
  browser.request('message.send', { conversation_id: conversationId, text })
const mentions = (browser, taskId) => browser.seen.filter(event => event.data.task_id === taskId)

test('three clients can broadcast, isolate private messages, paginate and refresh presence', { timeout: 7000 }, async t => {
  const open = await setup(t)
  const [alice, bob, carol] = await Promise.all([open(), open(), open()])
  await assert.rejects(alice.request('conversation.list'), { code: 'AUTH_REQUIRED' })
  const aliceUser = await authenticate(alice, 'Alice')
  const bobUser = await authenticate(bob, 'bob')
  const carolUser = await authenticate(carol, 'carol')
  await alice.waitFor('presence.changed', data => data.users.length === 3)
  assert.equal(aliceUser.username, 'alice')
  await assert.rejects(carol.request('auth.login', { username: 'carol', password }), { code: 'INVALID_STATE' })

  const { message: group } = await send(alice, '  Study 中文🙂  ')
  const [fromBob, fromCarol] = await Promise.all([bob, carol].map(browser =>
    browser.waitFor('message.new', data => data.message.message_id === group.message_id)))
  assert.deepEqual(fromBob.message, fromCarol.message)
  assert.equal(group.text, 'Study 中文🙂')
  assert.equal(group.task_id, null)

  const { conversation: dm } = await alice.request('conversation.open_dm', { peer_username: 'BOB' })
  const { conversation: same } = await bob.request('conversation.open_dm', { peer_username: 'alice' })
  assert.equal(dm.conversation_id, same.conversation_id)
  assert.deepEqual(dm.peer, bobUser)
  const { message: secret } = await send(alice, '仅私聊', dm.conversation_id)
  await bob.waitFor('message.new', data => data.message.message_id === secret.message_id)
  await assert.rejects(carol.request('history.list', { conversation_id: dm.conversation_id }), { code: 'FORBIDDEN' })
  assert.ok(!carol.seen.some(event => event.data.message?.message_id === secret.message_id))
  assert.ok(!alice.seen.some(event => event.data.message?.message_id === group.message_id))
  assert.ok(!(await carol.request('conversation.list')).conversations.some(value => value.conversation_id === dm.conversation_id))

  for (const text of ['study two', 'ignored', 'STUDY three']) await send(bob, text)
  const query = { conversation_id: room, keyword: 'Study', limit: 2 }
  const [latest, pong] = await Promise.all([alice.request('history.list', query), alice.request('ping')])
  assert.deepEqual(pong, {})
  assert.deepEqual(latest.messages.map(message => message.text), ['study two', 'STUDY three'])
  const older = await alice.request('history.list', { ...query, before_id: latest.next_before_id })
  assert.deepEqual(older.messages.map(message => message.message_id), [group.message_id])
  assert.equal(older.next_before_id, null)
  await assert.rejects(alice.request('history.list', { ...query, limit: 51 }), { code: 'INVALID_ARGUMENT' })
  await assert.rejects(alice.request('ping', { extra: true }), { code: 'INVALID_ARGUMENT' })
  await assert.rejects(alice.request('no.such.request'), { code: 'UNKNOWN_TYPE' })

  await bob.disconnect()
  const offline = data => data.users.length === 2 && !data.users.some(user => user.user_id === bobUser.user_id)
  await alice.waitFor('presence.changed', offline)
  const { message: missed } = await send(alice, '离线期间的消息', dm.conversation_id)
  const bobAgain = await open()
  await authenticate(bobAgain, 'bob', false)
  const history = await bobAgain.request('history.list', { conversation_id: dm.conversation_id })
  assert.deepEqual(history.messages.map(message => message.message_id), [secret.message_id, missed.message_id])
  assert.ok((await bobAgain.request('users.online')).users.some(user => user.user_id === carolUser.user_id))
})

test('times out and loses acknowledgements without treating either as a confirmed delivery', { timeout: 6000 }, async t => {
  const open = await setup(t, {
    responseFault(request) {
      if (request.data.text === 'quiet timeout') return 'drop'
      if (request.data.text === 'disconnect after store') return 'disconnect'
    },
  })
  const alice = await open(600)
  await authenticate(alice, 'alice')
  await assert.rejects(send(alice, 'quiet timeout'), { uncertain: true, message: '请求超时' })
  assert.deepEqual(await alice.request('ping'), {})
  await assert.rejects(send(alice, 'disconnect after store'), { uncertain: true })
  const reconnected = await open()
  await authenticate(reconnected, 'alice', false)
  const result = await reconnected.request('history.list', { conversation_id: room })
  assert.deepEqual(result.messages.map(message => message.text), ['quiet timeout', 'disconnect after store'])
})

test('only requester receives Agent state and tool activity while participants receive the result', { timeout: 6000 }, async t => {
  const open = await setup(t, { agentDelay: 100 })
  const [alice, bob, carol] = await Promise.all([open(), open(), open()])
  await authenticate(alice, 'alice')
  await authenticate(bob, 'bob')
  await authenticate(carol, 'carol')
  const { conversation } = await alice.request('conversation.open_dm', { peer_username: 'bob' })
  const id = conversation.conversation_id
  await send(bob, '第一项待办', id)
  const { task_id: taskId, message } = await alice.request('agent.run', {
    conversation_id: id, mode: 'todos', instruction: '提取待办',
  })
  assert.equal(message.text, '@Agent 提取待办')
  await assert.rejects(alice.request('agent.run', { conversation_id: id, instruction: '再来' }), { code: 'AGENT_BUSY' })
  await send(bob, '触发以后发送', id)
  const done = await alice.waitFor('agent.task', data => data.task_id === taskId && data.state === 'succeeded')
  const agentReply = await bob.waitFor('message.new', data => data.message.message_id === done.message_id)
  assert.equal(agentReply.message.kind, 'agent')
  assert.equal(agentReply.message.task_id, taskId)
  assert.deepEqual(agentReply.message.sender, { user_id: 'agent', username: 'Agent' })
  const events = mentions(alice, taskId).map(event => event.data)
  assert.deepEqual(events.map(event => event.state), ['running', 'started', 'succeeded', 'succeeded'])
  assert.equal(events[1].call_id, events[2].call_id)
  assert.equal(events[2].result_count, 1)
  assert.ok(alice.seen.findIndex(event => event.data.message?.message_id === done.message_id) <
    alice.seen.findIndex(event => event.data.message_id === done.message_id))
  assert.deepEqual(mentions(bob, taskId), [])
  await carol.request('ping')
  assert.ok(!carol.seen.some(event => event.data.message?.task_id === taskId))
})

test('Agent unavailable does not interrupt ordinary chat', { timeout: 5000 }, async t => {
  const open = await setup(t, { agentFailure: 'unavailable' })
  const alice = await open()
  await authenticate(alice, 'alice')
  const { task_id: taskId } = await alice.request('agent.run', { conversation_id: room, instruction: '总结' })
  const failure = await alice.waitFor('agent.task', data => data.task_id === taskId && data.state === 'failed')
  assert.equal(failure.error.code, 'AGENT_UNAVAILABLE')
  assert.equal((await send(alice, '普通消息仍可发送')).message.task_id, null)
})

test('tool failure reports the same call ID and finishes the task', { timeout: 5000 }, async t => {
  const open = await setup(t, { agentFailure: 'tool' })
  const alice = await open()
  await authenticate(alice, 'alice')
  const { task_id: taskId } = await alice.request('agent.run', { conversation_id: room, instruction: '总结' })
  const failure = await alice.waitFor('agent.task', data => data.task_id === taskId && data.state === 'failed')
  assert.equal(failure.error.code, 'AGENT_FAILED')
  const tools = mentions(alice, taskId).filter(event => event.type === 'agent.tool').map(event => event.data)
  assert.deepEqual(tools.map(data => data.state), ['started', 'failed'])
  assert.equal(tools[0].call_id, tools[1].call_id)
  assert.equal(tools[1].error.code, 'INTERNAL_ERROR')
})

test('Agent timeout leaves the started tool for the client to interrupt and frees both task slots', { timeout: 6000 }, async t => {
  const open = await setup(t, { agentFailure: 'timeout', agentTimeout: 600 })
  const [alice, bob, carol] = await Promise.all([open(), open(), open()])
  await authenticate(alice, 'alice')
  await authenticate(bob, 'bob')
  await authenticate(carol, 'carol')
  const run = client => client.request('agent.run', { conversation_id: room, instruction: '总结' })
  const [a, b, c] = await Promise.all([run(alice), run(bob), run(carol)])
  await assert.rejects(run(alice), { code: 'AGENT_BUSY' })
  for (const [client, task] of [[alice, a], [bob, b]]) {
    await client.waitFor('agent.tool', data => data.task_id === task.task_id && data.state === 'started')
  }
  assert.ok(!mentions(carol, c.task_id).some(event => event.data.state === 'running'))
  const terminal = await alice.waitFor('agent.task', data => data.task_id === a.task_id && data.state === 'failed')
  assert.equal(terminal.error.code, 'AGENT_TIMEOUT')
  assert.deepEqual(mentions(alice, a.task_id).map(event => event.data.state), ['running', 'started', 'failed'])
  await carol.waitFor('agent.task', data => data.task_id === c.task_id && data.state === 'running')
  assert.ok((await run(alice)).task_id)
  assert.equal((await send(bob, '超时后可继续聊天')).message.task_id, null)
  const { conversation } = await alice.request('conversation.open_dm', { peer_username: 'bob' })
  const { message } = await send(alice, '超时后可继续私聊', conversation.conversation_id)
  await bob.waitFor('message.new', data => data.message.message_id === message.message_id)
  const history = await alice.request('history.list', { conversation_id: room })
  assert.ok(!history.messages.some(message => message.task_id === a.task_id && message.kind === 'agent'))
})

test('merges duplicate history and push messages without adding an extra entry', () => {
  const cache = Object.create(null)
  const message = { conversation_id: room, message_id: 42 }
  assert.equal(mergeMessage(cache, message), true)
  assert.equal(mergeMessage(cache, { ...message }), false)
  assert.deepEqual(Object.keys(cache[room]), ['42'])
})
