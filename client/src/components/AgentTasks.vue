<script setup>
const { session } = defineProps({ session: { type: Object, required: true } })
const {
  currentTasks, mock, stateText,
} = session
</script>

<template>
  <details v-if="currentTasks.length" class="tasks">
    <summary>{{ mock ? '模拟 Agent' : 'Agent' }} 任务（{{ currentTasks.length }}）</summary>
    <div v-for="task in currentTasks" :key="task.task_id">
      <strong>{{ stateText[task.state] ?? '已受理' }}</strong> {{ task.error?.message }}
      <div v-for="tool in task.tools" :key="tool.call_id">
        {{ tool.name }} · {{ stateText[tool.state] }}
        {{ tool.result_count != null ? ' · ' + tool.result_count + ' 条' : '' }} {{ tool.error?.message }}
      </div>
    </div>
  </details>
</template>
