/// <reference types="vitest" />
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
  // Vitest 单测:jsdom 环境渲染 antd 组件,setup 补齐缺失浏览器 API
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
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
  build: {
    // 代码分割优化:页面级 React.lazy(App.tsx)+ vendor 分包;
    // dicebear 头像库经 avatarGen.ts dynamic import 单独分包、首屏不加载
    rollupOptions: {
      output: {
        manualChunks: {
          // 核心框架单独打包
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          // UI 库单独打包。注意 @ant-design/x 刻意不在此列:它仅被 Chat /
          // SessionHistory 两个懒加载页使用,强制并入 vendor-antd 会让首屏
          // (登录页)白下载;移除后随 lazy 页自动共享分包,进入相关页面才拉取
          'vendor-antd': ['antd', '@ant-design/icons'],
          // 注:react-markdown 不在此声明——仅懒加载页面使用,
          // 强制手动分块会被提升进首屏 HTML,反而增大初始加载体积
        },
      },
    },
    // 提高 chunk 大小警告阈值(优化后仍可能超标,但可接受)
    chunkSizeWarningLimit: 1000,
  },
});
