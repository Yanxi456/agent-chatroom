<script setup>
import { nextTick, ref } from 'vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import HistorySearch from '../components/HistorySearch.vue'
import MessageList from '../components/MessageList.vue'
import AgentTasks from '../components/AgentTasks.vue'
import MessageComposer from '../components/MessageComposer.vue'
import ChatIcon from '../components/ChatIcon.vue'

const { session } = defineProps({ session: { type: Object, required: true } })
const {
  showSidebar, room, roomName, users, mock, demo, latency, notice, uncertain, active,
  loading, reviewing, reviewUncertain, sending, resolveUncertain,
} = session

const showSearch = ref(true)
const search = ref(null)

async function toggleSearch() {
  showSearch.value = !showSearch.value
  if (showSearch.value) {
    await nextTick()
    search.value?.focus()
  }
}
</script>

<template>
  <main class="workspace">
    <ConversationSidebar :session="session" />
    <section class="chat">
      <header class="chat-header">
        <button class="sidebar-toggle" aria-controls="chat-sidebar" :aria-expanded="showSidebar" @click="showSidebar = !showSidebar">会话</button>
        <div class="chat-heading">
          <h1>{{ roomName(room) }}</h1>
          <small v-if="room?.kind !== 'dm'"><i class="status-dot"></i>{{ users.length }} 位用户在线</small>
          <small v-else>私人会话</small>
        </div>
        <button class="icon-button" aria-label="搜索当前会话" title="搜索当前会话" aria-controls="chat-history-search"
          :aria-expanded="showSearch" @click="toggleSearch"><ChatIcon name="search" /></button>
        <span class="connection"><i class="status-dot"></i>{{ demo ? '本地演示' : (mock ? '模拟联调 · ' : '') + '已连接' + (latency === null ? '' : ' · ' + latency + ' ms') }}</span>
      </header>
      <HistorySearch v-show="showSearch" ref="search" :session="session" />
      <MessageList :session="session" />
      <AgentTasks :session="session" />
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
      <MessageComposer :session="session" />
    </section>
  </main>
</template>
