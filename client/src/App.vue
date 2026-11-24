<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { connect } from './transport.js'

const publicRoom = 'room:general'
const user = ref(null)
const username = ref('')
const password = ref('')
const registering = ref(false)
const authenticated = ref(false)
const busy = ref(false)
const status = ref('disconnected')
const notice = ref('')
const latency = ref(null)
const active = ref(publicRoom)
const users = ref([])
const conversations = reactive(Object.create(null))
const messages = reactive(Object.create(null))
const drafts = reactive(Object.create(null))
const unread = reactive(Object.create(null))
const tasks = reactive(Object.create(null))
const peer = ref('')
const keyword = ref('')
const history = ref(null)
const loading = ref(false)
const sending = ref(false)
const messagePane = ref(null)
const composer = ref(null)
const showSidebar = ref(false)
let transport
let heartbeat
let queryVersion = 0
let presenceVersion = 0

const connected = computed(() => authenticated.value && status.value === 'connected')
const room = computed(() => conversations[active.value])
const currentMessages = computed(() => history.value?.keyword
  ? history.value.messages
  : Object.values(messages[active.value] ?? {}).sort((a, b) => a.message_id - b.message_id))
const currentTasks = computed(() => Object.values(tasks)
  .filter(task => task.conversation_id === active.value))
const roomName = conversation => conversation?.kind === 'dm'
  ? conversation.peer?.username ?? '私聊'
  : '公共群聊'
const time = value => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
const stateText = {
  accepted: '已受理', running: '执行中', started: '执行中', succeeded: '完成',
  failed: '失败', interrupted: '已中断',
}

function merge(message, incoming = false) {
  const id = message.conversation_id
  const bucket = messages[id] ?? (messages[id] = {})
  const fresh = !bucket[message.message_id]
  bucket[message.message_id] = message
  if (incoming && fresh && id !== active.value) unread[id] = (unread[id] ?? 0) + 1
}

function handleEvent({ type, data }) {
  if (!authenticated.value) return
  if (type === 'presence.changed') {
    presenceVersion++
    users.value = data.users
  }
  if (type === 'message.new') {
    const message = data.message
    if (!conversations[message.conversation_id]) {
      conversations[message.conversation_id] = {
        conversation_id: message.conversation_id, kind: 'dm',
        peer: message.kind === 'user' ? message.sender : null,
      }
    }
    merge(message, true)
  }
  if (type === 'agent.task' || type === 'agent.tool') {
    const id = data.task_id
    if (!tasks[id]) tasks[id] = { task_id: id, conversation_id: data.conversation_id, tools: {} }
    const task = tasks[id]
    if (type === 'agent.tool') task.tools[data.call_id] = data
    else {
      Object.assign(task, data)
      if (data.state === 'failed') interruptTools(task)
    }
  }
}

function interruptTools(task) {
  for (const tool of Object.values(task.tools)) {
    if (tool.state === 'started') tool.state = 'interrupted'
  }
}

function disconnect() {
  authenticated.value = false
  status.value = 'disconnected'
  latency.value = null
  clearInterval(heartbeat)
  queryVersion++
  loading.value = false
  for (const task of Object.values(tasks)) {
    if (task.state === 'accepted' || task.state === 'running') task.state = 'interrupted'
    interruptTools(task)
  }
}

function clearAccount() {
  user.value = null
  users.value = []
  for (const group of [conversations, messages, drafts, unread, tasks]) {
    for (const key of Object.keys(group)) delete group[key]
  }
  history.value = null
  keyword.value = ''
  active.value = publicRoom
}

async function ping(connection) {
  const started = performance.now()
  try {
    await connection.request('ping')
    if (connection === transport && authenticated.value) latency.value = Math.round(performance.now() - started)
  } catch {
    if (connection === transport) latency.value = null
  }
}

async function authenticate() {
  busy.value = true
  notice.value = ''
  const name = username.value.trim()
  try {
    if (status.value !== 'connected') {
      status.value = 'connecting'
      const connection = connect({
        event: message => { if (connection === transport) handleEvent(message) },
        closed: () => {
          if (connection !== transport) return
          disconnect()
          notice.value = '连接已断开，请重新登录。'
        },
      })
      transport = connection
      await connection.ready
      status.value = 'connected'
    }
    const data = { username: name, password: password.value }
    if (registering.value) {
      await transport.request('auth.register', data)
      registering.value = false
      notice.value = '注册成功，请登录。'
      return
    }
    const { user: account } = await transport.request('auth.login', data)
    if (user.value?.user_id !== account.user_id) clearAccount()
    user.value = account
    password.value = ''
    authenticated.value = true
    const connection = transport
    const onlineVersion = presenceVersion
    clearInterval(heartbeat)
    heartbeat = setInterval(() => ping(connection), 15000)
    void ping(connection)
    const [online, list] = await Promise.all([
      connection.request('users.online'),
      connection.request('conversation.list'),
    ])
    if (connection !== transport || !authenticated.value) return
    if (onlineVersion === presenceVersion) users.value = online.users
    for (const conversation of list.conversations) conversations[conversation.conversation_id] = conversation
    await selectRoom(conversations[active.value] ? active.value : publicRoom)
  } catch (error) {
    if (status.value === 'connecting') disconnect()
    notice.value = error.message
  } finally {
    busy.value = false
  }
}

async function logout() {
  const connection = transport
  transport = undefined
  disconnect()
  clearAccount()
  notice.value = ''
  try {
    await connection.request('auth.logout')
  } catch {
    // Closing the transport also releases the server session.
  } finally {
    connection.close()
  }
}

async function selectRoom(id) {
  active.value = id
  showSidebar.value = false
  unread[id] = 0
  keyword.value = ''
  await loadHistory()
  if (active.value === id && messagePane.value) messagePane.value.scrollTop = messagePane.value.scrollHeight
}

async function openDm(name) {
  if (!name?.trim() || !connected.value) return
  try {
    const connection = transport
    const { conversation } = await connection.request('conversation.open_dm', { peer_username: name.trim() })
    if (connection !== transport || !authenticated.value) return
    conversations[conversation.conversation_id] = conversation
    peer.value = ''
    await selectRoom(conversation.conversation_id)
  } catch (error) {
    notice.value = error.message
  }
}

async function loadHistory(older = false) {
  if (!connected.value) return
  const previous = older ? history.value : null
  const id = active.value
  const term = previous?.keyword ?? keyword.value.trim()
  const version = ++queryVersion
  if (!older) history.value = null
  loading.value = true
  try {
    const data = { conversation_id: id }
    if (term) data.keyword = term
    if (previous?.next !== null && previous?.next !== undefined) data.before_id = previous.next
    const result = await transport.request('history.list', data)
    if (version !== queryVersion) return
    const pane = messagePane.value
    const oldHeight = pane?.scrollHeight ?? 0
    const oldTop = pane?.scrollTop ?? 0
    for (const message of result.messages) merge(message)
    history.value = {
      keyword: term, next: result.next_before_id,
      messages: term ? [...result.messages, ...(previous?.messages ?? [])] : [],
    }
    if (older) {
      await nextTick()
      if (version === queryVersion && pane) pane.scrollTop = oldTop + pane.scrollHeight - oldHeight
    }
  } catch (error) {
    if (version === queryVersion) notice.value = error.message
  } finally {
    if (version === queryVersion) loading.value = false
  }
}

async function send(mode) {
  if (!connected.value || sending.value) return
  const id = active.value
  const snapshot = drafts[id] ?? ''
  const text = snapshot.trim()
  const agent = mode || text.startsWith('@Agent ')
  const body = text.startsWith('@Agent ') ? text.slice(7).trim() : text
  const instruction = mode && !body ? (mode === 'summary' ? '总结当前会话' : '提取当前会话的待办事项') : body
  if (agent ? !instruction : !text) return
  sending.value = true
  notice.value = ''
  try {
    const data = agent
      ? { conversation_id: id, instruction, ...(mode ? { mode } : {}) }
      : { conversation_id: id, text }
    const connection = transport
    const { message, task_id } = await connection.request(agent ? 'agent.run' : 'message.send', data)
    if (connection !== transport || !authenticated.value) return
    merge(message)
    if (task_id && !tasks[task_id]) tasks[task_id] = { task_id, conversation_id: id, state: 'accepted', tools: {} }
    if (drafts[id] === snapshot) drafts[id] = ''
  } catch (error) {
    notice.value = error.uncertain ? '发送结果待确认。请重新加载历史后再决定是否重发。' : error.message
  } finally {
    sending.value = false
  }
}

function onEnter(event) {
  if (event.shiftKey || event.isComposing) return
  event.preventDefault()
  void send()
}

async function insertEmoji() {
  const input = composer.value
  const id = active.value
  const text = drafts[id] ?? ''
  const start = input.selectionStart
  const end = input.selectionEnd
  const emoji = '🙂'
  drafts[id] = text.slice(0, start) + emoji + text.slice(end)
  await nextTick()
  input.focus()
  input.setSelectionRange(start + emoji.length, start + emoji.length)
}

watch(() => currentMessages.value.at(-1)?.message_id, async (latest, previous) => {
  const pane = messagePane.value
  if (!pane || latest === previous || history.value?.keyword) return
  if (pane.scrollHeight - pane.scrollTop - pane.clientHeight > 64) return
  await nextTick()
  pane.scrollTop = pane.scrollHeight
})

onUnmounted(() => {
  clearInterval(heartbeat)
  transport?.close()
})
</script>

<template>
  <main v-if="!authenticated" class="login-page">
    <section class="login-card">
      <div class="brand">智能聊天室</div>
      <h1>{{ registering ? '创建账号' : user ? '重新登录' : '登录' }}</h1>
      <form @submit.prevent="authenticate">
        <label>用户名<input v-model="username" required autocomplete="username" pattern="[A-Za-z0-9_]{3,32}"></label>
        <label>密码<input v-model="password" required type="password" :autocomplete="registering ? 'new-password' : 'current-password'"></label>
        <button class="primary" :disabled="busy">{{ busy ? '处理中…' : registering ? '注册' : '登录' }}</button>
      </form>
      <p v-if="notice" role="status">{{ notice }}</p>
      <button type="button" class="text-button" :disabled="busy" @click="registering = !registering; notice = ''">
        {{ registering ? '返回登录' : '创建账号' }}
      </button>
    </section>
  </main>

  <main v-else class="workspace">
    <aside id="chat-sidebar" class="sidebar" :class="{ expanded: showSidebar }">
      <div class="brand">智能聊天室</div>
      <div class="account">{{ user.username }} <button @click="logout">退出</button></div>
      <h2>会话</h2>
      <button v-for="conversation in conversations" :key="conversation.conversation_id" class="row"
        :class="{ selected: conversation.conversation_id === active }" @click="selectRoom(conversation.conversation_id)">
        {{ roomName(conversation) }} <span v-if="unread[conversation.conversation_id]">{{ unread[conversation.conversation_id] }}</span>
      </button>
      <h2>在线用户</h2>
      <button v-for="online in users.filter(entry => entry.user_id !== user.user_id)" :key="online.user_id"
        class="row" @click="openDm(online.username)">{{ online.username }}</button>
      <form class="inline" @submit.prevent="openDm(peer)">
        <input v-model="peer" aria-label="私聊用户名" placeholder="输入用户名"><button>私聊</button>
      </form>
    </aside>
    <section class="chat">
      <header>
        <button class="sidebar-toggle" aria-controls="chat-sidebar" :aria-expanded="showSidebar" @click="showSidebar = !showSidebar">会话</button>
        <h1>{{ roomName(room) }}</h1><span>{{ latency === null ? '已连接' : latency + ' ms' }}</span>
      </header>
      <form class="search inline" @submit.prevent="loadHistory()">
        <input v-model="keyword" aria-label="搜索消息" placeholder="搜索当前会话"><button :disabled="loading">搜索</button>
        <button v-if="history?.keyword" type="button" @click="keyword = ''; loadHistory()">退出搜索</button>
      </form>
      <div ref="messagePane" class="messages">
        <button v-if="history?.next != null" :disabled="loading" @click="loadHistory(true)">加载更早的消息</button>
        <p v-if="!currentMessages.length" class="muted">{{ loading ? '正在加载…' : '暂无消息' }}</p>
        <article v-for="message in currentMessages" :key="message.message_id" :class="{ own: message.sender.user_id === user.user_id }">
          <small>{{ message.sender.username }} · {{ time(message.created_at_ms) }}</small>
          <div class="bubble">{{ message.text }}</div>
        </article>
      </div>
      <details v-if="currentTasks.length" class="tasks">
        <summary>Agent 任务（{{ currentTasks.length }}）</summary>
        <div v-for="task in currentTasks" :key="task.task_id">
          <strong>{{ stateText[task.state] ?? '已受理' }}</strong> {{ task.error?.message }}
          <div v-for="tool in task.tools" :key="tool.call_id">
            {{ tool.name }} · {{ stateText[tool.state] }}
            {{ tool.result_count != null ? ' · ' + tool.result_count + ' 条' : '' }} {{ tool.error?.message }}
          </div>
        </div>
      </details>
      <p v-if="notice" class="notice" role="status">{{ notice }} <button @click="notice = ''">关闭</button></p>
      <form class="composer" @submit.prevent="send()">
        <textarea ref="composer" v-model="drafts[active]" aria-label="消息" placeholder="输入消息，或使用 @Agent 发起任务" rows="2" @keydown.enter="onEnter"></textarea>
        <div class="actions">
          <button type="button" aria-label="插入笑脸表情" @click="insertEmoji">🙂</button>
          <button type="button" :disabled="sending" @click="send('summary')">总结</button>
          <button type="button" :disabled="sending" @click="send('todos')">提取待办</button>
          <button class="primary send" :disabled="sending || !drafts[active]?.trim()">发送</button>
        </div>
      </form>
    </section>
  </main>
</template>
