<script setup>
import { computed } from 'vue'
import ChatAvatar from './ChatAvatar.vue'
import ChatIcon from './ChatIcon.vue'

const { session } = defineProps({ session: { type: Object, required: true } })
const {
  currentMessages, user, time, reviewing, loading, history, loadHistory, setMessagePane,
} = session

const groupInterval = 5 * 60 * 1000
const agentPrefix = '@Agent '
const dateFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' })
const days = computed(() => {
  const result = []
  const today = dateFormat.format(Date.now())
  let day
  let group
  let previous

  for (const message of currentMessages.value) {
    const date = dateFormat.format(message.created_at_ms)
    const agent = message.kind === 'agent'
    if (day?.date !== date) {
      day = { date, label: date === today ? '今天' : date, groups: [], id: message.message_id }
      result.push(day)
      group = null
    }
    const gap = message.created_at_ms - (previous?.created_at_ms ?? 0)
    if (!group || agent || group.agent || group.sender.user_id !== message.sender.user_id ||
        gap < 0 || gap > groupInterval) {
      group = {
        id: message.message_id, sender: message.sender, timestamp: message.created_at_ms,
        agent, own: !agent && message.sender.user_id === user.value.user_id, messages: [],
      }
      day.groups.push(group)
    }
    group.messages.push(message)
    previous = message
  }
  return result
})

const isAgentRequest = message => message.kind === 'user' && message.task_id != null && message.text.startsWith(agentPrefix)
</script>

<template>
  <div :ref="setMessagePane" class="messages">
    <p v-if="history?.keyword && currentMessages.length" class="search-results-label">搜索结果 · {{ history.keyword }}</p>
    <button v-if="history?.next != null" class="older-messages" :disabled="loading || reviewing" @click="loadHistory(true)">加载更早的消息</button>
    <div v-if="!currentMessages.length" class="empty-messages" role="status">
      <ChatIcon :name="history?.keyword ? 'search' : 'chat'" />
      <p>{{ loading ? '正在加载…' : history?.keyword ? '没有匹配的消息' : '暂无消息' }}</p>
    </div>
    <template v-for="day in days" :key="day.id">
      <div class="date-divider">{{ day.label }}</div>
      <article v-for="group in day.groups" :key="group.id" class="message-group" :class="{ own: group.own }">
        <ChatAvatar :name="group.sender.username" :agent="group.agent" />
        <div class="message-stack">
          <div class="message-meta">
            <strong>{{ group.agent ? 'Agent' : group.sender.username }}</strong>
            <time :datetime="new Date(group.timestamp).toISOString()">{{ time(group.timestamp) }}</time>
          </div>
          <div v-for="message in group.messages" :key="message.message_id" class="bubble"
            :class="{ 'agent-result': group.agent, 'agent-request': isAgentRequest(message) }">
            <template v-if="group.agent"><h2>Agent 结果</h2>{{ message.text }}</template>
            <template v-else-if="isAgentRequest(message)"><span class="agent-tag">@Agent</span>{{ message.text.slice(agentPrefix.length) }}</template>
            <template v-else>{{ message.text }}</template>
          </div>
        </div>
      </article>
    </template>
  </div>
</template>
