<script setup>
defineProps({
  demo: Boolean,
  mock: Boolean,
  canOpenDemo: Boolean,
  registering: Boolean,
  reconnecting: Boolean,
  busy: Boolean,
  notice: { type: String, required: true },
})
const username = defineModel('username', { type: String, required: true })
const password = defineModel('password', { type: String, required: true })
const emit = defineEmits(['authenticate', 'toggle-registering'])
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="brand">智能聊天室</div>
      <p v-if="demo" class="muted">本地演示 · 可用 demo 登录，请填写任意非空演示密码。</p>
      <p v-if="mock" class="muted">本地模拟联调 · 请先注册测试账号。聊天数据和 Agent 事件均为模拟数据。</p>
      <h1>{{ registering ? '创建账号' : reconnecting ? '重新登录' : '登录' }}</h1>
      <form @submit.prevent="emit('authenticate')">
        <label>用户名<input v-model="username" required autocomplete="username" pattern="[A-Za-z0-9_]{3,32}"></label>
        <label>密码<input v-model="password" required type="password" :autocomplete="registering ? 'new-password' : 'current-password'"></label>
        <button class="primary" :disabled="busy">{{ busy ? '处理中…' : registering ? '注册' : '登录' }}</button>
      </form>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="!demo && canOpenDemo"><a href="?demo=1">进入本地演示</a></p>
      <button type="button" class="text-button" :disabled="busy" @click="emit('toggle-registering')">
        {{ registering ? '返回登录' : '创建账号' }}
      </button>
    </section>
  </main>
</template>
