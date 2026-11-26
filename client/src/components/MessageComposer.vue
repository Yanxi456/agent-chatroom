<script setup>
import ChatIcon from './ChatIcon.vue'

const { session } = defineProps({ session: { type: Object, required: true } })
const {
  drafts, active, uncertain, sending, demo, send, onEnter, insertEmoji, setComposer,
} = session
</script>

<template>
  <form class="composer" @submit.prevent="send()">
    <div class="composer-box">
      <textarea :ref="setComposer" v-model="drafts[active]" :disabled="!!uncertain[active]" aria-label="消息"
        :placeholder="demo ? '输入消息' : '输入消息，或使用 @Agent 发起任务'" rows="2" @keydown.enter="onEnter"></textarea>
      <div class="actions">
        <button class="icon-button" type="button" :disabled="!!uncertain[active]" aria-label="插入笑脸表情" title="插入笑脸表情" @click="insertEmoji"><ChatIcon name="smile" /></button>
        <button v-if="!demo" class="agent-action" type="button" :disabled="sending || !!uncertain[active]" @click="send('summary')"><ChatIcon name="sparkle" />智能总结</button>
        <button v-if="!demo" class="agent-action" type="button" :disabled="sending || !!uncertain[active]" @click="send('todos')"><ChatIcon name="todos" />提取待办</button>
        <button class="primary send" :disabled="sending || !!uncertain[active] || !drafts[active]?.trim()">{{ sending ? '发送中' : '发送' }}<ChatIcon name="send" /></button>
      </div>
    </div>
  </form>
</template>
