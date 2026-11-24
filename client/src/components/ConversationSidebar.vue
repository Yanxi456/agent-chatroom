<script setup>
const { session } = defineProps({ session: { type: Object, required: true } })
const {
  user, users, conversations, active, unread, showSidebar, roomName, demo, peer, logout, selectRoom, openDm,
} = session
</script>

<template>
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
</template>
