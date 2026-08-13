import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端 API 服务地址（开发/预览环境通过下面的 proxy 反代 /api）。
const API_TARGET = 'http://127.0.0.1:8023'

// SSE（问答流式接口 /qa/ask）必须关闭代理缓冲，否则浏览器收不到逐 token 流。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function configureSseProxy(proxy: any) {
  proxy.on('proxyRes', (proxyRes: any) => {
    proxyRes.headers['Cache-Control'] = 'no-cache, no-transform'
    proxyRes.headers['X-Accel-Buffering'] = 'no'
    proxyRes.headers['Connection'] = 'keep-alive'
  })
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 开发环境把 /api 反代到本地后端，使前端默认的 /api/v1 相对路径可用，
    // 不必再设置 VITE_API_BASE_URL。生产由 nginx 反代 /api（见 nginx.conf）。
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        configure: configureSseProxy,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        configure: configureSseProxy,
      },
    },
  },
})
