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

// Element.scrollTo:jsdom 未实现,@ant-design/x BubbleList 的 autoScroll 依赖
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = (() => {}) as typeof Element.prototype.scrollTo;
}

// HTMLMediaElement:jsdom 无媒体管线,TTS 播报组件挂载时需要方法存在
if (!HTMLMediaElement.prototype.play) {
  HTMLMediaElement.prototype.play = () => Promise.resolve();
}
if (!HTMLMediaElement.prototype.pause) {
  HTMLMediaElement.prototype.pause = () => {};
}

// RTL:每个用例后卸载组件,避免用例间 DOM 泄漏
afterEach(() => {
  cleanup();
});
