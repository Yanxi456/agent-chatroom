<script setup>
import ChatIcon from './ChatIcon.vue'

defineProps({ name: String, group: Boolean, agent: Boolean, online: Boolean })
</script>

<template>
  <span class="avatar" :class="{ 'avatar-group': group, 'avatar-agent': agent }" aria-hidden="true">
    <ChatIcon v-if="agent" name="robot" />
    <template v-else>{{ group ? '#' : (Array.from(name ?? '')[0]?.toUpperCase() || '?') }}</template>
    <i v-if="online" class="presence-dot"></i>
  </span>
</template>
