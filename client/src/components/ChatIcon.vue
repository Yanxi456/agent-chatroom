<script setup>
defineProps({ name: { type: String, required: true } })

const paths = {
  chat: 'M4 4h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H10l-5 4v-4H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z',
  logout: 'M12 3h8v18h-8M3 12h12m-5-5 5 5-5 5',
  search: 'M16 16l5 5M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z',
  smile: 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM7 14c2 4 8 4 10 0M8.5 9h.01m6.99 0h.01',
  sparkle: 'M12 2c0 6-4 10-10 10 6 0 10 4 10 10 0-6 4-10 10-10-6 0-10-4-10-10Z',
  todos: 'M4 6h2m4 0h10M4 12h2m4 0h10M4 18h2m4 0h10',
  send: 'm3 4 18 8-18 8 4-8-4-8Zm4 8h14',
  robot: 'M12 3v3M5 7h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Zm3 5h.01m7.99 0h.01M9 16h6',
}
</script>

<template>
  <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path :d="paths[name]" />
  </svg>
</template>
