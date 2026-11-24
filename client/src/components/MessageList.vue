<script setup>
const { session } = defineProps({ session: { type: Object, required: true } })
const {
  currentMessages, user, time, reviewing, loading, history, loadHistory, setMessagePane,
} = session
</script>

<template>
  <div :ref="setMessagePane" class="messages">
    <button v-if="history?.next != null" :disabled="loading || reviewing" @click="loadHistory(true)">加载更早的消息</button>
    <p v-if="!currentMessages.length" class="muted">{{ loading ? '正在加载…' : history?.keyword ? '没有匹配的消息' : '暂无消息' }}</p>
    <article v-for="message in currentMessages" :key="message.message_id" :class="{ own: message.sender.user_id === user.user_id }">
      <small>{{ message.sender.username }} · {{ time(message.created_at_ms) }}</small>
      <div class="bubble">{{ message.text }}</div>
    </article>
  </div>
</template>
