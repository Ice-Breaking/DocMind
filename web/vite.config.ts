import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发期代理到后端 Gradio/FastAPI（7860）：登录 cookie 同源流转，无跨域问题
const BACKEND = 'http://127.0.0.1:7860';

// SSE 防挂配置：客户端中途断开（刷新/关页）时销毁上游请求，
// 否则 vite 5 的 http-proxy 会把整个 dev server 拖挂
const sseSafe = {
  target: BACKEND,
  configure: (proxy: any) => {
    proxy.on('proxyReq', (proxyReq: any, _req: any, res: any) => {
      // 仅当客户端中途断开（响应未写完）才销毁上游请求；
      // 注意不能监听 req 的 close——请求体接收完就会触发，会误杀正常请求
      res.on('close', () => {
        if (!res.writableEnded) proxyReq.destroy();
      });
    });
    proxy.on('error', (err: any) => console.warn('[proxy]', err.message));
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': sseSafe,
      '/login': BACKEND,
      '/logout': BACKEND,
      '/files': BACKEND,
    },
  },
});
