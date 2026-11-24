<script setup>
const { session } = defineProps({ session: { type: Object, required: true } })
const {
  user, users, conversations, active, unread, showSidebar, room, roomName,
  demo, mock, latency, peer, keyword, reviewing, loading, history,
  currentMessages, time, currentTasks, stateText, notice, uncertain,
  sending, drafts, logout, selectRoom, openDm, loadHistory, reviewUncertain,
  resolveUncertain, send, onEnter, insertEmoji, setMessagePane, setComposer,
} = session
</script>

<template>
  <main class="workspace">
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
      <form v-if="!demo" class="inline" @submit.prevent="openDm(peer)">
        <input v-model="peer" aria-label="私聊用户名" placeholder="输入用户名"><button>私聊</button>
      </form>
    </aside>
    <section class="chat">
      <header>
        <button class="sidebar-toggle" aria-controls="chat-sidebar" :aria-expanded="showSidebar" @click="showSidebar = !showSidebar">会话</button>
        <h1>{{ roomName(room) }}</h1>
        <span>{{ mock ? '模拟联调 · ' : '' }}{{ demo ? '本地演示' : latency === null ? '已连接' : latency + ' ms' }}</span>
      </header>
      <form class="search inline" @submit.prevent="loadHistory()">
        <input v-model="keyword" :disabled="reviewing" aria-label="搜索消息" placeholder="搜索当前会话"><button :disabled="loading || reviewing">搜索</button>
        <button v-if="history?.keyword" type="button" :disabled="reviewing" @click="keyword = ''; loadHistory()">退出搜索</button>
      </form>
      <div :ref="setMessagePane" class="messages">
        <button v-if="history?.next != null" :disabled="loading || reviewing" @click="loadHistory(true)">加载更早的消息</button>
        <p v-if="!currentMessages.length" class="muted">{{ loading ? '正在加载…' : history?.keyword ? '没有匹配的消息' : '暂无消息' }}</p>
        <article v-for="message in currentMessages" :key="message.message_id" :class="{ own: message.sender.user_id === user.user_id }">
          <small>{{ message.sender.username }} · {{ time(message.created_at_ms) }}</small>
          <div class="bubble">{{ message.text }}</div>
        </article>
      </div>
      <details v-if="currentTasks.length" class="tasks">
        <summary>{{ mock ? '模拟 Agent' : 'Agent' }} 任务（{{ currentTasks.length }}）</summary>
        <div v-for="task in currentTasks" :key="task.task_id">
          <strong>{{ stateText[task.state] ?? '已受理' }}</strong> {{ task.error?.message }}
          <div v-for="tool in task.tools" :key="tool.call_id">
            {{ tool.name }} · {{ stateText[tool.state] }}
            {{ tool.result_count != null ? ' · ' + tool.result_count + ' 条' : '' }} {{ tool.error?.message }}
          </div>
        </div>
      </details>
      <p v-if="notice" class="notice" role="status">{{ notice }} <button @click="notice = ''">关闭</button></p>
      <div v-if="uncertain[active]" class="notice" role="status">
        <p>上一条发送结果待确认。请查看历史，再决定是否重发。</p>
        <button :disabled="loading || reviewing" @click="reviewUncertain">{{ reviewing ? '正在核对历史…' : '查看历史' }}</button>
        <template v-if="uncertain[active].checked">
          <p v-if="uncertain[active].matches.length">
            找到 {{ uncertain[active].matches.length }} 条同内容消息，重发可能产生重复。
            <span v-for="match in uncertain[active].matches" :key="match.message_id">
              消息 #{{ match.message_id }}（{{ new Date(match.created_at_ms).toLocaleString('zh-CN') }}）
            </span>
          </p>
          <p v-else>已查完发送后的会话历史，未找到同内容消息。</p>
          <button :disabled="sending" @click="resolveUncertain(true)">确认重发</button>
          <button :disabled="sending" @click="resolveUncertain(false)">取消重发</button>
        </template>
      </div>
      <form class="composer" @submit.prevent="send()">
        <textarea :ref="setComposer" v-model="drafts[active]" :disabled="!!uncertain[active]" aria-label="消息" placeholder="输入消息，或使用 @Agent 发起任务" rows="2" @keydown.enter="onEnter"></textarea>
        <div class="actions">
          <button type="button" :disabled="!!uncertain[active]" aria-label="插入笑脸表情" @click="insertEmoji">🙂</button>
          <button v-if="!demo" type="button" :disabled="sending || !!uncertain[active]" @click="send('summary')">总结</button>
          <button v-if="!demo" type="button" :disabled="sending || !!uncertain[active]" @click="send('todos')">提取待办</button>
          <button class="primary send" :disabled="sending || !!uncertain[active] || !drafts[active]?.trim()">发送</button>
        </div>
      </form>
    </section>
  </main>
</template>
