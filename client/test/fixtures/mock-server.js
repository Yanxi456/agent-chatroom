import { createServer } from 'node:net'
import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto'
import { encodeFrame, FrameDecoder } from '../../gateway/framing.js'

const room = { conversation_id: 'room:general', kind: 'group', peer: null }
const agent = { user_id: 'agent', username: 'Agent' }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const usernamePattern = /^[A-Za-z0-9_]{3,32}$/
const requestPattern = /^[A-Za-z0-9_-]{1,64}$/
const size = (value, min, max) => typeof value === 'string' && Array.from(value).length >= min && Array.from(value).length <= max
const integer = (value, min, max) => Number.isSafeInteger(value) && value >= min && value <= max
const asciiLower = value => value.replace(/[A-Z]/g, letter => letter.toLowerCase())
const error = (code, message) => ({ code, message })
const invalid = () => { throw error('INVALID_ARGUMENT', '请求参数无效') }
const requiredText = (value, max) => {
  if (typeof value !== 'string' || !size(value.trim(), 1, max)) invalid()
  return value.trim()
}
const requestFields = new Map([
  ['auth.register', ['username', 'password']],
  ['auth.login', ['username', 'password']],
  ['auth.logout', []],
  ['users.online', []],
  ['conversation.list', []],
  ['conversation.open_dm', ['peer_username']],
  ['message.send', ['conversation_id', 'text']],
  ['history.list', ['conversation_id', 'before_id', 'limit', 'keyword']],
  ['ping', []],
  ['agent.run', ['conversation_id', 'instruction', 'mode']],
])

export function startMockServer({
  port = 8765, agentDelay = 150, agentFailure = false, agentTimeout = 60_000,
  responseFault = () => undefined,
} = {}) {
  const accounts = new Map()
  const sockets = new Set()
  const online = new Map()
  const conversations = new Map()
  const ledger = []
  const pending = new Set()
  const queue = []
  const timers = new Set()
  let activeTasks = 0
  let taskNumber = 0

  function write(session, message) {
    if (!session.socket.destroyed) session.socket.write(encodeFrame(Buffer.from(JSON.stringify(message))))
  }

  function event(session, type, data) {
    write(session, { v: 1, type, data })
  }

  function respond(session, request, result, failure) {
    const data = failure ? { ok: false, error: failure } : { ok: true, result }
    const fault = !failure && responseFault(request, result)
    if (fault === 'disconnect') session.socket.destroy()
    else if (fault !== 'drop') write(session, { v: 1, type: 'response', request_id: request.request_id, data })
  }

  function users() {
    return Array.from(online.values(), session => session.user)
  }

  function presence() {
    const snapshot = { users: users() }
    for (const session of online.values()) event(session, 'presence.changed', snapshot)
  }

  function publish(message, excluded) {
    const members = conversations.get(message.conversation_id)
    for (const session of online.values()) {
      if (session !== excluded && (!members || members.includes(session.user))) {
        event(session, 'message.new', { message })
      }
    }
  }

  function store(conversationId, sender, kind, text, taskId = null) {
    const message = {
      message_id: ledger.length + 1, conversation_id: conversationId, sender,
      kind, text, created_at_ms: Date.now(), task_id: taskId,
    }
    ledger.push(message)
    return message
  }

  function permitted(id, user) {
    if (typeof id !== 'string') invalid()
    if (id === room.conversation_id) return
    const members = conversations.get(id)
    if (!members) throw error('CONVERSATION_NOT_FOUND', '会话不存在')
    if (!members.includes(user)) throw error('FORBIDDEN', '无权访问该会话')
  }

  function view(id, members, user) {
    return { conversation_id: id, kind: 'dm', peer: members.find(member => member !== user) }
  }

  function page(id, { before_id: before = Infinity, limit = 20, keyword } = {}) {
    const needle = keyword ? asciiLower(keyword) : ''
    const matches = ledger.filter(message => message.conversation_id === id &&
      message.message_id < before && asciiLower(message.text).includes(needle))
    const messages = matches.slice(-limit)
    return { messages, next_before_id: matches.length > messages.length ? messages[0].message_id : null }
  }

  function after(action, delay = agentDelay) {
    const timer = setTimeout(() => {
      timers.delete(timer)
      action()
    }, delay)
    timers.add(timer)
    return timer
  }

  function dispatch() {
    while (activeTasks < 2 && queue.length) {
      const { requester, message } = queue.shift()
      activeTasks++
      const ids = { task_id: message.task_id, conversation_id: message.conversation_id }
      const notify = (type, data) => {
        const session = online.get(requester.user_id)
        if (session) event(session, type, { ...ids, ...data })
      }
      let finished = false
      const complete = () => {
        finished = true
        clearTimeout(timeout)
        timers.delete(timeout)
        pending.delete(requester.user_id)
        activeTasks--
        dispatch()
      }
      notify('agent.task', { state: 'running' })
      const timeout = after(() => {
        notify('agent.task', { state: 'failed', error: error('AGENT_TIMEOUT', '模拟 Agent 执行超时') })
        complete()
      }, agentTimeout)
      after(() => {
        if (finished) return
        if (agentFailure === 'unavailable') {
          notify('agent.task', { state: 'failed', error: error('AGENT_UNAVAILABLE', '模拟模型不可用') })
          complete()
          return
        }
        const tool = { call_id: 'tool-1', name: 'read_history' }
        notify('agent.tool', { ...tool, state: 'started' })
        if (agentFailure === 'timeout') return
        after(() => {
          if (finished) return
          if (agentFailure === 'tool') {
            notify('agent.tool', { ...tool, state: 'failed', error: error('INTERNAL_ERROR', '模拟历史查询失败') })
            notify('agent.task', { state: 'failed', error: error('AGENT_FAILED', '模拟 Agent 无法继续执行') })
            complete()
            return
          }
          const history = page(ids.conversation_id, { before_id: message.message_id })
          notify('agent.tool', { ...tool, state: 'succeeded', result_count: history.messages.length })
          const reply = store(ids.conversation_id, agent, 'agent',
            '【模拟 Agent】已读取触发前的 ' + history.messages.length + ' 条测试消息。', ids.task_id)
          publish(reply)
          notify('agent.task', { state: 'succeeded', message_id: reply.message_id })
          complete()
        })
      })
    }
  }

  function handle(session, request) {
    const { v, type, request_id: id, data } = request
    if (v !== 1 || typeof id !== 'string' || !requestPattern.test(id) || session.ids.has(id)) {
      session.socket.destroy()
      return
    }
    session.ids.add(id)
    try {
      if (typeof type !== 'string' || !object(data) ||
          Object.keys(request).some(key => !['v', 'type', 'request_id', 'data'].includes(key))) invalid()
      if (!requestFields.has(type)) throw error('UNKNOWN_TYPE', '未知请求类型')
      if (Object.keys(data).some(key => !requestFields.get(type).includes(key))) invalid()
      const ok = result => respond(session, request, result)

      if (type === 'ping') return ok({})
      if (type === 'auth.register' || type === 'auth.login') {
        if (session.user) throw error('INVALID_STATE', '当前连接已经登录')
        if (typeof data.username !== 'string' || !usernamePattern.test(data.username) ||
            !size(data.password, 8, 128)) invalid()
        const name = data.username.toLowerCase()
        if (name === 'agent') invalid()
        const account = accounts.get(name)
        if (type === 'auth.register') {
          if (account) throw error('USERNAME_TAKEN', '用户名已注册')
          const user = { user_id: 'usr_' + name, username: name }
          const salt = randomBytes(16)
          accounts.set(name, { user, salt, digest: scryptSync(data.password, salt, 32) })
          return ok({ user })
        }
        if (!account || !timingSafeEqual(account.digest, scryptSync(data.password, account.salt, 32))) {
          throw error('INVALID_CREDENTIALS', '用户名或密码错误')
        }
        if (online.has(account.user.user_id)) throw error('ALREADY_ONLINE', '账号已在线')
        session.user = account.user
        online.set(session.user.user_id, session)
        ok({ user: session.user })
        presence()
        return
      }

      const user = session.user
      if (!user) throw error('AUTH_REQUIRED', '请先登录')
      switch (type) {
        case 'auth.logout':
          ok({})
          session.socket.end()
          return
        case 'users.online':
          return ok({ users: users() })
        case 'conversation.list':
          return ok({ conversations: [room, ...Array.from(conversations)
            .filter(([, members]) => members.includes(user))
            .map(([id, members]) => view(id, members, user))] })
        case 'conversation.open_dm': {
          if (typeof data.peer_username !== 'string' || !usernamePattern.test(data.peer_username)) invalid()
          const peer = accounts.get(data.peer_username.toLowerCase())?.user
          if (!peer) throw error('USER_NOT_FOUND', '用户不存在')
          if (peer === user) invalid()
          const id = 'dm:' + [user.user_id, peer.user_id].sort().join(':')
          if (!conversations.has(id)) conversations.set(id, [user, peer])
          return ok({ conversation: view(id, conversations.get(id), user) })
        }
        case 'history.list':
          permitted(data.conversation_id, user)
          if (data.before_id !== undefined && !integer(data.before_id, 1, Number.MAX_SAFE_INTEGER)) invalid()
          if (data.limit !== undefined && !integer(data.limit, 1, 50)) invalid()
          return ok(page(data.conversation_id, {
            before_id: data.before_id, limit: data.limit,
            keyword: data.keyword === undefined ? undefined : requiredText(data.keyword, 100),
          }))
        case 'message.send': {
          permitted(data.conversation_id, user)
          const message = store(data.conversation_id, user, 'user', requiredText(data.text, 2000))
          ok({ message })
          publish(message, session)
          return
        }
        case 'agent.run': {
          permitted(data.conversation_id, user)
          const instruction = requiredText(data.instruction, 2000)
          if (data.mode !== undefined && !['auto', 'summary', 'todos'].includes(data.mode)) invalid()
          if (pending.has(user.user_id) || queue.length >= 4) throw error('AGENT_BUSY', '任务队列繁忙')
          pending.add(user.user_id)
          const message = store(data.conversation_id, user, 'user', '@Agent ' + instruction, 'task-' + ++taskNumber)
          ok({ task_id: message.task_id, message })
          publish(message, session)
          queue.push({ requester: user, message })
          dispatch()
          return
        }
      }
    } catch (failure) {
      respond(session, request, undefined, failure.code ? failure : error('INTERNAL_ERROR', '模拟服务内部错误'))
    }
  }

  const server = createServer(socket => {
    const decoder = new FrameDecoder()
    const session = { socket, ids: new Set() }
    sockets.add(socket)
    const idle = setTimeout(() => socket.destroy(), 45_000)
    socket.on('data', chunk => {
      try {
        for (const { value } of decoder.feed(chunk)) {
          if (socket.destroyed || socket.writableEnded) break
          idle.refresh()
          handle(session, value)
        }
      } catch {
        socket.destroy()
      }
    })
    socket.on('error', () => socket.destroy())
    socket.on('close', () => {
      clearTimeout(idle)
      sockets.delete(socket)
      if (session.user && online.get(session.user.user_id) === session) {
        online.delete(session.user.user_id)
        presence()
      }
    })
  })
  server.listen(port, '127.0.0.1')

  return {
    server,
    close() {
      for (const timer of timers) clearTimeout(timer)
      for (const socket of sockets) socket.destroy()
      return new Promise(resolve => server.close(resolve))
    },
  }
}
