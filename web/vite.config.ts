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
    // 内网穿透（cloudflared trycloudflare 等）时 Host 非 localhost，
    // vite 5.4+ 默认拦截外部 Host（CVE-2025-30208 防护）；此处按需放开
    allowedHosts: true,
    proxy: {
      // 注意结尾斜杠：避免前缀匹配误伤 SPA 路由 /api-keys
      '/api/': sseSafe,
      '/open/': BACKEND,
      // /login 仅代理 POST（登录提交）；GET 走 SPA 的登录页
      '/login': {
        target: BACKEND,
        bypass: (req: any) => (req.method === 'GET' ? '/index.html' : null),
      },
      '/logout': BACKEND,
      '/files': BACKEND,
    },
  },
});
