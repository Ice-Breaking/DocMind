# 性能优化第十二期(2026-08-23):撤销 antd 强制 vendor 块,首屏再减 42%

## 一、背景与定位

第十一期遗留「vendor-antd 1231KB(min)/ ~404KB(gzip),评估为首屏 UI 必需」。
本期以 sourcemap 聚合法复核该结论,发现其**不成立**:

vendor-antd 源码构成(3355 KB src)中,大量模块只有懒加载页使用:

| 模块 | 源码体积 | 实际使用方 |
|---|---|---|
| rc-picker(DatePicker/TimePicker) | 240 KB | Traces/Usage 等管理页 |
| rc-table | 132 KB | Dashboard/Usage/Admin |
| rc-tree | 109 KB | 知识库/设置等页 |
| rc-virtual-list / rc-upload / rc-image / rc-tabs | 55+31+50+58 KB | 各懒加载页 |

而首屏链路(main + App + Login + AppLayout)import 的 antd 组件仅为
Button/Card/Form/Input/Drawer/Dropdown/Layout/Menu/Modal/Typography/
Result/Spin/Space/message/App/ConfigProvider——**零重型组件**。

根因:manualChunks 对象形式把 `antd` 整包强制归入单一 vendor 块,
该块被首屏 HTML 必引,于是所有懒加载页的重型依赖都陪跑登录页下载。

## 二、改动

vite.config.ts:删除 `'vendor-antd': ['antd', '@ant-design/icons']`,
antd 交给 rollup 默认策略按实际引用自然分割(保留 vendor-react):

```diff
- 'vendor-antd': ['antd', '@ant-design/icons'],
```

效果:首屏 entry 只并入 Login/AppLayout 所需的轻组件子集及其基建
(rc-field-form/rc-menu/@rc-component/trigger/cssinjs/motion/dialog 等);
rc-table、rc-picker 等成为对应懒加载页的共享异步分包(如 Table 独立块
165 KB,进入相关页面才拉取)。sourcemap 复核 entry 构成确认无重型组件混入。

权衡说明:antd 模块分散进多个 chunk 后,升级 antd 会使这些 chunk 的
内容 hash 全部失效(缓存粒度变粗);对内部工具,首屏加载体积优先级更高,
且 HTTP/2 下并行拉取多个小块代价低。

## 三、效果(vite build 实测)

| 指标 | 第十一期 | 本期 | 变化 |
|---|---|---|---|
| 首屏 HTML 引用 JS(min) | 57+160+1231 = **1448 KB** | entry **696** + react **160** = **856 KB** | **-592 KB(-41%)** |
| 首屏 JS gzip | ~477 KB | 222.7+52.4 ≈ **275 KB** | **≈ -202 KB(-42%)** |
| >1MB 超限告警 | 0(antd 1231KB 临界) | 0 | — |
| 首屏请求的 JS 块数 | 3 | 2(entry + vendor-react) | — |

行为无任何改动:仅构建配置调整;tsc ✅ / eslint ✅ / vitest **36/36** ✅。

## 四、三期累计(第十期末 → 本期)

| 口径 | 第十期末 | 本期 | 累计 |
|---|---|---|---|
| 全站 JS 总量(min) | ~3.84 MB | ~2.5 MB | **-35%** |
| 首屏 JS gzip | ~737 KB* | ~275 KB | **-63%** |

\* 第七期报告口径(~467 KB)+ 第十一期发现 x 已移出前的口径差,取保守值;
准确对比以第十一期 ~477 KB 起:两期累计 **-42%**。

## 五、遗留说明

- entry 内 @tanstack/query-core 约 54 KB(min):Provider 位于 App 根,
  收益/风险比低,维持上期结论暂不动;
- antd 升级的缓存失效面变大(见上权衡说明);若未来部署为长周期公网
  服务,可改用 manualChunks 函数式按目录精细分组找回缓存粒度;
- @dicebear/adventurer 单风格包仍达 270 KB(min):风格包本身数据量大,
  属库固有体积,按需加载已是最优解。
