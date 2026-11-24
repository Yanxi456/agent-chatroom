import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useAuthentication } from './useAuthentication.js'
import { mergeMessage } from './message-cache.js'
import { stateText, useAgentTasks } from './useAgentTasks.js'

export function useChatSession() {
  const publicRoom = 'room:general'
  const uncertain = reactive(Object.create(null))
  const reviewing = ref(false)
  const notice = ref('')
  const active = ref(publicRoom)
  const users = ref([])
  const conversations = reactive(Object.create(null))
  const messages = reactive(Object.create(null))
  const drafts = reactive(Object.create(null))
  const unread = reactive(Object.create(null))
  const peer = ref('')
  const keyword = ref('')
  const history = ref(null)
  const loading = ref(false)
  const sending = ref(false)
  const messagePane = ref(null)
  const composer = ref(null)
  const showSidebar = ref(false)
  let queryVersion = 0
  let presenceVersion = 0
  let reviewVersion = 0

  const {
    authenticated, username, password, demo, mock, canOpenDemo, registering,
    user, busy, latency, connected, authenticate, logout, toggleRegistering, getConnection,
  } = useAuthentication({ notice, handleEvent, resetAfterDisconnect, clearAccountState, initializeChat })

  const room = computed(() => conversations[active.value])
  const currentMessages = computed(() => history.value?.keyword
    ? history.value.messages
    : Object.values(messages[active.value] ?? {}).sort((a, b) => a.message_id - b.message_id))
  const { currentTasks, handleAgentEvent, interruptTasks, clearTasks, acceptTask } = useAgentTasks(active)
  const roomName = conversation => conversation?.kind === 'dm'
    ? conversation.peer?.username ?? '私聊'
    : '公共群聊'
  const time = value => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

  function merge(message, incoming = false) {
    const id = message.conversation_id
    const fresh = mergeMessage(messages, message)
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
    handleAgentEvent(type, data)
  }

  function resetAfterDisconnect() {
    queryVersion++
    reviewVersion++
    reviewing.value = false
    loading.value = false
    for (const pending of Object.values(uncertain)) pending.checked = false
    interruptTasks()
  }

  function clearAccountState() {
    users.value = []
    for (const group of [conversations, messages, drafts, unread, uncertain]) {
      for (const key of Object.keys(group)) delete group[key]
    }
    clearTasks()
    history.value = null
    keyword.value = ''
    active.value = publicRoom
  }

  async function initializeChat(connection) {
    const onlineVersion = presenceVersion
    const [online, list] = await Promise.all([
      connection.request('users.online'),
      connection.request('conversation.list'),
    ])
    if (connection !== getConnection() || !authenticated.value) return
    if (onlineVersion === presenceVersion) users.value = online.users
    for (const conversation of list.conversations) conversations[conversation.conversation_id] = conversation
    await selectRoom(conversations[active.value] ? active.value : publicRoom)
  }

  async function selectRoom(id) {
    reviewVersion++
    reviewing.value = false
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
      const connection = getConnection()
      const { conversation } = await connection.request('conversation.open_dm', { peer_username: name.trim() })
      if (connection !== getConnection() || !authenticated.value) return
      conversations[conversation.conversation_id] = conversation
      peer.value = ''
      await selectRoom(conversation.conversation_id)
    } catch (error) {
      notice.value = error.message
    }
  }

  async function loadHistory(older = false) {
    if (!connected.value) return false
    const previous = older ? history.value : null
    const id = active.value
    const term = previous?.keyword ?? keyword.value.trim()
    if (Array.from(term).length > 100) {
      notice.value = '搜索关键词最多可输入 100 个字符。'
      return false
    }
    const version = ++queryVersion
    if (!older) history.value = null
    loading.value = true
    try {
      const data = { conversation_id: id }
      if (term) data.keyword = term
      if (previous?.next !== null && previous?.next !== undefined) data.before_id = previous.next
      const result = await getConnection().request('history.list', data)
      if (version !== queryVersion) return false
      const pane = messagePane.value
      const oldHeight = pane?.scrollHeight ?? 0
      const oldTop = pane?.scrollTop ?? 0
      for (const message of result.messages) merge(message)
      history.value = {
        keyword: term, next: result.next_before_id,
        messages: term ? Array.from(new Map([
          ...result.messages, ...(previous?.messages ?? []),
        ].map(message => [message.message_id, message])).values())
          .sort((a, b) => a.message_id - b.message_id) : [],
      }
      if (older) {
        await nextTick()
        if (version === queryVersion && pane) pane.scrollTop = oldTop + pane.scrollHeight - oldHeight
      }
      return version === queryVersion ? result : false
    } catch (error) {
      if (version === queryVersion) notice.value = error.message
      return false
    } finally {
      if (version === queryVersion) loading.value = false
    }
  }

  async function reviewUncertain() {
    const id = active.value
    const pending = uncertain[id]
    if (!pending || reviewing.value || loading.value) return
    const version = ++reviewVersion
    reviewing.value = true
    pending.checked = false
    keyword.value = ''
    const matches = []
    const senderId = user.value.user_id
    try {
      let older = false
      while (true) {
        const page = await loadHistory(older)
        if (!page || version !== reviewVersion || uncertain[id] !== pending) return
        for (const message of page.messages) {
          if (message.message_id > pending.baseline &&
              message.sender.user_id === senderId &&
              message.text === pending.expectedText &&
              (message.task_id !== null) === pending.agent) {
            matches.push(message)
          }
        }
        if (page.next_before_id === null || page.next_before_id <= pending.baseline) break
        older = true
      }
      pending.matches = matches.sort((a, b) => a.message_id - b.message_id)
      pending.checked = true
    } finally {
      if (version === reviewVersion) reviewing.value = false
    }
  }

  function resolveUncertain(retry) {
    const id = active.value
    const pending = uncertain[id]
    if (!pending?.checked || reviewing.value || sending.value) return
    delete uncertain[id]
    if (retry) {
      drafts[id] = pending.snapshot
      void send(pending.mode)
    } else if (drafts[id] === pending.snapshot) {
      drafts[id] = ''
    }
  }

  async function send(mode) {
    if (!connected.value || sending.value) return
    const id = active.value
    if (uncertain[id]) return
    const snapshot = drafts[id] ?? ''
    const text = snapshot.trim()
    const agent = Boolean(mode || text.startsWith('@Agent '))
    const body = text.startsWith('@Agent ') ? text.slice(7).trim() : text
    const instruction = mode && !body ? (mode === 'summary' ? '总结当前会话' : '提取当前会话的待办事项') : body
    if (agent ? !instruction : !text) return
    if (Array.from(agent ? instruction : text).length > 2000) {
      notice.value = '消息或指令最多可输入 2000 个字符。'
      return
    }
    sending.value = true
    notice.value = ''
    const connection = getConnection()
    const baseline = Object.values(messages[id] ?? {}).reduce(
      (latest, message) => Math.max(latest, message.message_id), 0,
    )
    try {
      const data = agent
        ? { conversation_id: id, instruction, ...(mode ? { mode } : {}) }
        : { conversation_id: id, text }
      const { message, task_id } = await connection.request(agent ? 'agent.run' : 'message.send', data)
      if (connection !== getConnection() || !authenticated.value) return
      merge(message)
      acceptTask(task_id, id)
      if (drafts[id] === snapshot) drafts[id] = ''
    } catch (error) {
      if (connection !== getConnection()) return
      if (error.uncertain) {
        uncertain[id] = {
          snapshot, mode, baseline, agent, checked: false, matches: [],
          expectedText: agent ? '@Agent ' + instruction : text,
        }
      } else notice.value = error.message
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

  const chatSession = {
    user, users, conversations, active, unread, showSidebar, room, roomName,
    demo, mock, latency, peer, keyword, reviewing, loading, history,
    currentMessages, time, currentTasks, stateText, notice, uncertain,
    sending, drafts, logout, selectRoom, openDm, loadHistory, reviewUncertain,
    resolveUncertain, send, onEnter, insertEmoji,
    setMessagePane: element => { messagePane.value = element },
    setComposer: element => { composer.value = element },
  }

  return {
    authenticated, username, password, demo, mock, canOpenDemo, registering,
    user, busy, notice, authenticate, toggleRegistering, chatSession,
  }
}
