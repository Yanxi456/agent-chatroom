const room = 'room:general'
const histories = new Map()

export function connectDemo({ closed }) {
  let user
  let disconnected = false

  return {
    ready: Promise.resolve(),
    close() {
      disconnected = true
      closed()
    },
    async request(type, data = {}) {
      if (disconnected) throw new Error('演示连接已关闭')
      if (type === 'auth.login' || type === 'auth.register') {
        const username = data.username.toLowerCase()
        const account = { user_id: username, username }
        if (type === 'auth.login') user = account
        return { user: account }
      }
      if (!user) throw new Error('请先登录')
      if (!histories.has(user.user_id)) histories.set(user.user_id, [])
      const messages = histories.get(user.user_id)
      switch (type) {
        case 'auth.logout':
          user = undefined
          return {}
        case 'users.online':
          return { users: [user] }
        case 'conversation.list':
          return { conversations: [{ conversation_id: room, kind: 'group', peer: null }] }
        case 'history.list': {
          const matches = messages.filter(message =>
            (!data.before_id || message.message_id < data.before_id) &&
            (!data.keyword || message.text.toLowerCase().includes(data.keyword.toLowerCase())))
          const page = matches.slice(-20)
          return { messages: page, next_before_id: matches.length > page.length ? page[0].message_id : null }
        }
        case 'message.send': {
          const message = {
            message_id: messages.length + 1, conversation_id: room,
            sender: user, kind: 'user', text: data.text,
            created_at_ms: Date.now(), task_id: null,
          }
          messages.push(message)
          return { message }
        }
        default:
          throw new Error('本地演示仅支持公共群聊和历史检索')
      }
    },
  }
}
