/**
 * Vitest 全局 setup:jsdom 缺失的浏览器 API 补齐 + RTL 自动清理。
 */
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// antd 响应式组件(Grid/Drawer 等)依赖 matchMedia,jsdom 未实现
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// 部分 antd 组件使用 ResizeObserver
if (!('ResizeObserver' in window)) {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver =
    ResizeObserverMock;
}

// RTL:每个用例后卸载组件,避免用例间 DOM 泄漏
afterEach(() => {
  cleanup();
});
