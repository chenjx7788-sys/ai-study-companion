import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    chunkSizeWarningLimit: 1000,   // element-plus 整包 ~940KB，自用工具可接受
    rollupOptions: {
      output: {
        // 大依赖独立分包：框架/UI/渲染引擎分离，避免单 chunk 超 500KB 警告
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-element': ['element-plus', '@element-plus/icons-vue'],
          'vendor-pdf': ['pdfjs-dist'],
          'vendor-md': ['markdown-it'],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',   // 显式绑 IPv4，避免默认绑定 ::1 导致浏览器 localhost 打不开
    port: 5173,
    proxy: {
      '/api': {
        // 目标端口可覆盖：默认 8000；打包版 exe 常占用 8000，此时用 ASC_PORT=8010
        // 跑源码版后端，并让 dev server 代理到同一端口（start.bat 会自动带上）。
        target: process.env.ASC_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})
