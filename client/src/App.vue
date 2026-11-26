<script setup>
import { onUnmounted, watch } from 'vue'
import { useChatSession } from './composables/useChatSession.js'
import LoginPage from './views/LoginPage.vue'
import ChatPage from './views/ChatPage.vue'

const {
  authenticated, username, password, demo, mock, registering,
  user, busy, notice, authenticate, toggleRegistering, chatSession,
} = useChatSession()

function syncRoute() {
  const route = authenticated.value ? '#/chat' : '#/login'
  if (location.hash !== route) window.history.replaceState(window.history.state, '', route)
}

watch(authenticated, syncRoute, { immediate: true, flush: 'sync' })
window.addEventListener('hashchange', syncRoute)
onUnmounted(() => window.removeEventListener('hashchange', syncRoute))
</script>

<template>
  <ChatPage v-if="authenticated" :session="chatSession" />
  <LoginPage
    v-else
    v-model:username="username"
    v-model:password="password"
    :demo="demo"
    :mock="mock"
    :registering="registering"
    :reconnecting="!!user"
    :busy="busy"
    :notice="notice"
    @authenticate="authenticate"
    @toggle-registering="toggleRegistering"
  />
</template>
