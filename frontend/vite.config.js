import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端固定运行在 http://localhost:8000
const BACKEND = 'http://localhost:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    open: false,
    // SSE 长连接：关闭代理层超时，避免流式回答被中断
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        proxyTimeout: 0,
        timeout: 0,
      },
      // 后端 StaticFiles 挂载点（Mermaid 生成的示意图等）
      '/static': {
        target: BACKEND,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 2048,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router'],
          element: ['element-plus'],
          echarts: ['echarts'],
          mermaid: ['mermaid'],
        },
      },
    },
  },
})
