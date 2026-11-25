<script setup>
import { ref } from 'vue'
import ChatIcon from './ChatIcon.vue'

const { session } = defineProps({ session: { type: Object, required: true } })
const {
  keyword, reviewing, loading, history, loadHistory,
} = session
const input = ref(null)
defineExpose({ focus: () => input.value?.focus() })
</script>

<template>
  <form id="chat-history-search" class="search inline" role="search" @submit.prevent="loadHistory()">
    <label class="search-field">
      <ChatIcon name="search" />
      <input ref="input" v-model="keyword" :disabled="reviewing" aria-label="搜索消息" placeholder="搜索当前会话">
    </label>
    <button :disabled="loading || reviewing">搜索</button>
    <button v-if="history?.keyword" type="button" :disabled="reviewing" @click="keyword = ''; loadHistory()">退出搜索</button>
  </form>
</template>
