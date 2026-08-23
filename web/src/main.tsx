import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import ErrorBoundary from './ErrorBoundary';
import './styles.css';

// 字号档位：首屏前应用，避免闪烁（小/标准/大，存 localStorage）
document.documentElement.dataset.fontscale =
  localStorage.getItem('dm_fontscale') || 'md';

// TanStack Query 全局客户端。默认值刻意保守以对齐既有手写 fetch 行为：
// - staleTime 0 + refetchOnMount 默认：每次进入页面重新请求（同旧 useEffect 模式）；
// - refetchOnWindowFocus 关闭：旧实现无此行为；
// - retry 关闭：旧实现失败即展示错误态。
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: false,
    },
  },
});

// 主题与 Gradio 版一致：indigo 主色（#6366f1）
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: { colorPrimary: '#6366f1', borderRadius: 10 },
        }}
      >
        <AntApp>
          <BrowserRouter>
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          </BrowserRouter>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);

