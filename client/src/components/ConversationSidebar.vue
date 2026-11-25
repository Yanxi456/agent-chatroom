<script setup>
import { computed } from 'vue'
import ChatAvatar from './ChatAvatar.vue'
import ChatIcon from './ChatIcon.vue'

const { session } = defineProps({ session: { type: Object, required: true } })
const {
  user, users, conversations, active, unread, showSidebar, roomName, latestMessages,
  demo, peer, logout, selectRoom, openDm,
} = session

const onlinePeers = computed(() => users.value.filter(entry => entry.user_id !== user.value.user_id))
</script>

<template>
  <aside id="chat-sidebar" class="sidebar" :class="{ expanded: showSidebar }">
    <div class="brand"><span class="brand-mark"><ChatIcon name="chat" /></span>智能聊天室</div>
    <div class="account">
      <ChatAvatar :name="user.username" online />
      <span class="row-copy"><strong>{{ user.username }}</strong><small>在线</small></span>
      <button class="icon-button" aria-label="退出登录" title="退出登录" @click="logout"><ChatIcon name="logout" /></button>
    </div>
    <section class="sidebar-section" aria-labelledby="conversations-heading">
      <h2 id="conversations-heading">会话列表</h2>
      <button v-for="conversation in conversations" :key="conversation.conversation_id" class="row"
        :class="{ selected: conversation.conversation_id === active }"
        :aria-current="conversation.conversation_id === active ? 'true' : undefined"
        @click="selectRoom(conversation.conversation_id)">
        <ChatAvatar :name="roomName(conversation)" :group="conversation.kind === 'group'" />
        <span class="row-copy">
          <strong>{{ roomName(conversation) }}</strong>
          <small>{{ latestMessages[conversation.conversation_id]?.text ?? (conversation.kind === 'dm' ? '私人会话' : '群聊') }}</small>
        </span>
        <span v-if="unread[conversation.conversation_id]" class="unread"
          :aria-label="unread[conversation.conversation_id] + ' 条未读消息'">{{ Math.min(unread[conversation.conversation_id], 99) }}{{ unread[conversation.conversation_id] > 99 ? '+' : '' }}</span>
      </button>
    </section>
    <section class="sidebar-section" aria-labelledby="online-heading">
      <h2 id="online-heading">在线用户 · {{ onlinePeers.length }}</h2>
      <button v-for="online in onlinePeers" :key="online.user_id"
        class="row online-row" @click="openDm(online.username)">
        <ChatAvatar :name="online.username" online />
        <span class="row-copy">{{ online.username }}</span>
      </button>
      <form v-if="!demo" class="inline dm-form" @submit.prevent="openDm(peer)">
        <input v-model="peer" aria-label="私聊用户名" placeholder="输入用户名">
        <button :disabled="!peer.trim()">私聊</button>
      </form>
    </section>
  </aside>
</template>
