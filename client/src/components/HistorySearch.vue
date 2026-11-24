<script setup>
const { session } = defineProps({ session: { type: Object, required: true } })
const {
  keyword, reviewing, loading, history, loadHistory,
} = session
</script>

<template>
  <form class="search inline" @submit.prevent="loadHistory()">
    <input v-model="keyword" :disabled="reviewing" aria-label="搜索消息" placeholder="搜索当前会话"><button :disabled="loading || reviewing">搜索</button>
    <button v-if="history?.keyword" type="button" :disabled="reviewing" @click="keyword = ''; loadHistory()">退出搜索</button>
  </form>
</template>
