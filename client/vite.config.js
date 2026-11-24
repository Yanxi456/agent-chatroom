import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxy = { '/ws': { target: 'ws://127.0.0.1:8766', ws: true } }
export default defineConfig({ plugins: [vue()], server: { proxy }, preview: { proxy } })
