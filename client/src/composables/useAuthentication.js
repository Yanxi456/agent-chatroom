import { computed, onUnmounted, ref } from 'vue'
import { connect } from '../transport/websocket.js'
import { connectDemo } from '../mocks/demo.js'

export function useAuthentication({
  notice, handleEvent, resetAfterDisconnect, clearAccountState, initializeChat,
}) {
  const canOpenDemo = import.meta.env.DEV
  const demo = canOpenDemo && (
    import.meta.env.MODE === 'demo' || new URLSearchParams(location.search).get('demo') === '1'
  )
  const mock = !demo && canOpenDemo && import.meta.env.MODE === 'mock'
  const user = ref(null)
  const username = ref('')
  const password = ref('')
  const registering = ref(false)
  const authenticated = ref(false)
  const busy = ref(false)
  const status = ref('disconnected')
  const latency = ref(null)
  let transport
  let heartbeat

  const connected = computed(() => authenticated.value && status.value === 'connected')

  function disconnect() {
    authenticated.value = false
    status.value = 'disconnected'
    latency.value = null
    clearInterval(heartbeat)
    resetAfterDisconnect()
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
    notice.value = ''
    const length = Array.from(password.value).length
    if (!demo && (length < 8 || length > 128)) {
      notice.value = '密码长度须为 8–128 个字符。'
      return
    }
    busy.value = true
    const name = username.value.trim()
    try {
      if (status.value !== 'connected') {
        status.value = 'connecting'
        const connection = (demo ? connectDemo : connect)({
          event: message => { if (connection === transport) handleEvent(message) },
          closed: reason => {
            if (connection !== transport) return
            disconnect()
            notice.value = reason ?? '连接已断开，请重新登录。'
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
      if (user.value?.user_id !== account.user_id) resetAccount()
      user.value = account
      password.value = ''
      authenticated.value = true
      const connection = transport
      clearInterval(heartbeat)
      if (!demo) {
        heartbeat = setInterval(() => ping(connection), 15000)
        void ping(connection)
      }
      await initializeChat(connection)
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
    resetAccount()
    notice.value = ''
    try {
      await connection.request('auth.logout')
    } catch {
      // Closing the transport also releases the server session.
    } finally {
      connection.close()
    }
  }

  function resetAccount() {
    user.value = null
    clearAccountState()
  }

  onUnmounted(() => {
    clearInterval(heartbeat)
    transport?.close()
  })

  function toggleRegistering() {
    registering.value = !registering.value
    notice.value = ''
  }

  const getConnection = () => transport

  return {
    authenticated, username, password, demo, mock, canOpenDemo, registering,
    user, busy, latency, connected, authenticate, logout, toggleRegistering, getConnection,
  }
}
