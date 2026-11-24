<script setup>
const { session } = defineProps({ session: { type: Object, required: true } })
const {
  drafts, active, uncertain, sending, demo, send, onEnter, insertEmoji, setComposer,
} = session
</script>

<template>
  <form class="composer" @submit.prevent="send()">
    <textarea :ref="setComposer" v-model="drafts[active]" :disabled="!!uncertain[active]" aria-label="消息" placeholder="输入消息，或使用 @Agent 发起任务" rows="2" @keydown.enter="onEnter"></textarea>
    <div class="actions">
      <button type="button" :disabled="!!uncertain[active]" aria-label="插入笑脸表情" @click="insertEmoji">🙂</button>
      <button v-if="!demo" type="button" :disabled="sending || !!uncertain[active]" @click="send('summary')">总结</button>
      <button v-if="!demo" type="button" :disabled="sending || !!uncertain[active]" @click="send('todos')">提取待办</button>
      <button class="primary send" :disabled="sending || !!uncertain[active] || !drafts[active]?.trim()">发送</button>
    </div>
  </form>
</template>
