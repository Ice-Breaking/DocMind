import { describe, expect, it } from 'vitest';
import { computeScaledSize, MAX_IMAGE_BYTES, MAX_IMGS } from './imageCompress';

describe('computeScaledSize', () => {
  it('超大图等比缩小到最长边 2048', () => {
    expect(computeScaledSize(4096, 3072)).toEqual({ width: 2048, height: 1536 });
    expect(computeScaledSize(3072, 4096)).toEqual({ width: 1536, height: 2048 });
  });

  it('小图不放大', () => {
    expect(computeScaledSize(800, 600)).toEqual({ width: 800, height: 600 });
  });

  it('正方形与自定义上限', () => {
    expect(computeScaledSize(1000, 1000, 500)).toEqual({ width: 500, height: 500 });
  });

  it('极端比例不产生 0 尺寸', () => {
    const r = computeScaledSize(10000, 1, 100);
    expect(r.width).toBe(100);
    expect(r.height).toBeGreaterThanOrEqual(1);
  });
});

describe('常量与后端约束对齐', () => {
  it('图片体积/数量上限与后端一致', () => {
    expect(MAX_IMAGE_BYTES).toBe(8 * 1024 * 1024);
    expect(MAX_IMGS).toBe(5);
  });
});
