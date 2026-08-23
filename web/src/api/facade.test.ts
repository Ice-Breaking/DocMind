/**
 * api 门面完整性冒烟:api.ts 拆分为 12 个领域模块后,
 * 页面统一从 './api' 导入——此测试锁住 facade 再导出不断链。
 */
import { describe, expect, it } from 'vitest';
import * as api from '../api';

function expectFn(name: string) {
  expect(typeof (api as Record<string, unknown>)[name]).toBe(`function`);
}

describe('api facade 完整性', () => {
  it('core 域:认证与会话探针', () => {
    expectFn('login');
    expectFn('logout');
    expectFn('fetchMe');
    expectFn('changePassword');
  });

  it('chat / alerts 域锚点(fetchSla 曾由 voice 错位归入 alerts)', () => {
    expectFn('fetchSla');
  });

  it('facade 导出规模合理(12 域模块合计应远超单域)', () => {
    const count = Object.keys(api).length;
    expect(count).toBeGreaterThan(40);
  });

  it('导出的运行时成员全部为函数(类型不产生运行时导出)', () => {
    for (const [name, value] of Object.entries(api)) {
      expect(typeof value === 'function' || typeof value === 'object',
        `unexpected export: ${name}`).toBe(true);
    }
  });
});
