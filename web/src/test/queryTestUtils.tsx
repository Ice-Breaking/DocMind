import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * 测试专用 QueryClient：关闭重试，避免用例等待退避；
 * 其余默认值与生产一致（staleTime 0 等）。
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

/** 用测试 QueryClient 包裹被测 UI（可注入自定义 client 以预置缓存/断言） */
export function withQueryClient(ui: ReactNode, client: QueryClient = createTestQueryClient()) {
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}
