import { computed, reactive } from 'vue'

export const stateText = {
  accepted: '已受理', running: '执行中', started: '执行中', succeeded: '完成',
  failed: '失败', interrupted: '已中断',
}

export function useAgentTasks(active) {
  const tasks = reactive(Object.create(null))
  const currentTasks = computed(() => Object.values(tasks)
    .filter(task => task.conversation_id === active.value))

  function handleAgentEvent(type, data) {
    if (type === 'agent.task' || type === 'agent.tool') {
      const id = data.task_id
      if (!tasks[id]) tasks[id] = { task_id: id, conversation_id: data.conversation_id, tools: {} }
      const task = tasks[id]
      if (type === 'agent.tool') task.tools[data.call_id] = data
      else {
        Object.assign(task, data)
        if (data.state === 'failed') interruptTools(task)
      }
    }
  }

  function interruptTools(task) {
    for (const tool of Object.values(task.tools)) {
      if (tool.state === 'started') tool.state = 'interrupted'
    }
  }

  function interruptTasks() {
    for (const task of Object.values(tasks)) {
      if (task.state === 'accepted' || task.state === 'running') task.state = 'interrupted'
      interruptTools(task)
    }
  }

  function clearTasks() {
    for (const id of Object.keys(tasks)) delete tasks[id]
  }

  function acceptTask(id, conversationId) {
    if (id && !tasks[id]) tasks[id] = { task_id: id, conversation_id: conversationId, state: 'accepted', tools: {} }
  }

  return { currentTasks, handleAgentEvent, interruptTasks, clearTasks, acceptTask }
}
