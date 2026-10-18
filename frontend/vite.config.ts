import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8120'
  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: 5178,
      strictPort: true,
      proxy: apiTarget
        ? {
            '^/api/': { target: apiTarget, changeOrigin: true },
            '/health': { target: apiTarget, changeOrigin: true },
            '/onebot': { target: apiTarget, changeOrigin: true, ws: true },
            '/ws': { target: apiTarget, changeOrigin: true, ws: true },
          }
        : undefined,
    },
    preview: {
      host: '0.0.0.0',
      port: 5178,
      strictPort: true,
    },
  }
})
