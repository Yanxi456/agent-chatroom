export function connect({ event, closed, timeout = 10000, endpoint = browserEndpoint(), Socket = WebSocket }) {
  const socket = new Socket(endpoint)
  const pending = new Map()
  let sequence = 0
  const ready = new Promise((resolve, reject) => {
    const deadline = setTimeout(() => {
      reject(new Error('连接超时'))
      socket.close()
    }, timeout)
    socket.addEventListener('open', () => {
      clearTimeout(deadline)
      resolve()
    }, { once: true })
    socket.addEventListener('close', event => {
      clearTimeout(deadline)
      reject(new Error(closeReason(event)))
    }, { once: true })
  })
  socket.addEventListener('message', ({ data }) => {
    let message
    try {
      message = JSON.parse(data)
      if (message?.v !== 1) throw new Error()
    } catch {
      socket.close(1002, 'Invalid protocol message')
      return
    }
    if (message.type !== 'response') {
      event(message)
      return
    }
    const request = pending.get(message.request_id)
    if (!request) return
    pending.delete(message.request_id)
    clearTimeout(request.timer)
    if (message.data?.ok) request.resolve(message.data.result)
    else request.reject(Object.assign(new Error(message.data?.error?.message ?? '请求失败'), {
      code: message.data?.error?.code,
    }))
  })
  socket.addEventListener('close', event => {
    const reason = closeReason(event)
    for (const request of pending.values()) {
      clearTimeout(request.timer)
      request.reject(Object.assign(new Error(reason), { uncertain: true }))
    }
    pending.clear()
    closed(reason)
  })

  return {
    ready,
    close: () => socket.close(),
    request(type, data = {}) {
      if (socket.readyState !== Socket.OPEN) return Promise.reject(new Error('尚未连接'))
      const requestId = String(++sequence)
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId)
          reject(Object.assign(new Error('请求超时'), { uncertain: true }))
        }, timeout)
        pending.set(requestId, { timer, resolve, reject })
        try {
          socket.send(JSON.stringify({ v: 1, type, request_id: requestId, data }))
        } catch (error) {
          clearTimeout(timer)
          pending.delete(requestId)
          reject(error)
        }
      })
    },
  }
}

function closeReason(event) {
  if (event.reason === 'TCP service unavailable') return '网关已连接，但 TCP 聊天服务不可用。'
  if (event.code === 1006) return '无法连接聊天网关，请检查网关是否已启动。'
  if (event.reason === 'TCP connection closed') return 'TCP 聊天服务已断开，请重新登录。'
  return '连接已断开，请重新登录。'
}

function browserEndpoint() {
  const url = new URL('/ws', location.href)
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return url
}
