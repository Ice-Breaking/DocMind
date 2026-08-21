import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Col,
  Divider,
  Progress,
  Radio,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  DatabaseOutlined,
  DollarOutlined,
  FieldTimeOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  fetchAdminOverview,
  fetchAdminUsage,
  fetchTopQueries,
  type AdminOverview,
  type TopQuery,
  type UsageByModel,
  type UsageDetail,
} from '../api';

const { Text } = Typography;

/* ------------------------------------------------------------------ */
/*  趋势条形图：纯 CSS 实现（无图表库依赖），悬停显示 token 与成本        */
/* ------------------------------------------------------------------ */

function TrendBars({ daily }: { daily: UsageDetail['daily'] }) {
  if (!daily.length) {
    return <Text type="secondary">暂无用量数据</Text>;
  }
  const maxTokens = Math.max(1, ...daily.map((d) => d.input_tokens + d.output_tokens));
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', height: 180 }}>
        {daily.map((d) => {
          const total = d.input_tokens + d.output_tokens;
          const ratio = total / maxTokens;
          const inputH = total > 0 ? (d.input_tokens / total) * ratio * 100 : 0;
          const outputH = total > 0 ? (d.output_tokens / total) * ratio * 100 : 0;
          return (
            <div
              key={d.date}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'flex-end',
                height: '100%',
              }}
              title={`${d.date}\n输入 ${d.input_tokens.toLocaleString()} / 输出 ${d.output_tokens.toLocaleString()}\n成本 ¥${d.cost}`}
            >
              <div
                style={{
                  height: `${outputH}%`,
                  background: '#95de64',
                  borderRadius: '3px 3px 0 0',
                  minHeight: d.output_tokens ? 2 : 0,
                }}
              />
              <div
                style={{
                  height: `${inputH}%`,
                  background: '#1677ff',
                  minHeight: d.input_tokens ? 2 : 0,
                }}
              />
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
        {daily.map((d) => (
          <div key={d.date} style={{ flex: 1, textAlign: 'center', fontSize: 10, color: '#999' }}>
            {d.date.slice(5)}
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10 }}>
        <Space size={4}>
          <span style={{ display: 'inline-block', width: 10, height: 10, background: '#1677ff', borderRadius: 2 }} />
          <Text type="secondary" style={{ fontSize: 12 }}>输入 Token</Text>
          <span style={{ display: 'inline-block', width: 10, height: 10, background: '#95de64', borderRadius: 2, marginLeft: 8 }} />
          <Text type="secondary" style={{ fontSize: 12 }}>输出 Token</Text>
          <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>（悬停柱体查看成本）</Text>
        </Space>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Usage() {
  const [days, setDays] = useState<number>(30);
  const [usage, setUsage] = useState<UsageDetail | null>(null);
  const [topQueries, setTopQueries] = useState<TopQuery[]>([]);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (d: number) => {
    setLoading(true);
    setError(null);
    try {
      const [u, tq, ov] = await Promise.all([
        fetchAdminUsage(d),
        fetchTopQueries(d, 10),
        fetchAdminOverview(),
      ]);
      setUsage(u);
      setTopQueries(tq.items);
      setOverview(ov);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(days); }, [days, load]);

  if (loading) return <Spin style={{ display: 'block', margin: '120px auto' }} size="large" />;
  if (error || !usage) {
    return (
      <div style={{ padding: '24px 32px' }}>
        <Text type="danger">加载失败：{error || '未知错误'}</Text>
      </div>
    );
  }

  const { summary, by_model, daily } = usage;
  const costPerK = summary.total_calls > 0
    ? (summary.total_cost / summary.total_calls) * 1000
    : 0;
  const totalTokens = summary.total_input_tokens + summary.total_output_tokens;
  const cache = overview?.cache;
  const cacheLookups = cache
    ? (cache as any).lookups || ((cache as any).misses || 0) + cache.total_hits
    : 0;
  const cacheHitRate = cache && cacheLookups > 0 ? (cache.total_hits / cacheLookups) * 100 : null;

  const modelColumns: ColumnsType<UsageByModel> = [
    { title: '模型', dataIndex: 'model', key: 'model', render: (v: string) => <Text code>{v}</Text> },
    { title: '调用数', dataIndex: 'calls', key: 'calls', width: 80, render: (v: number) => v.toLocaleString() },
    {
      title: 'Token（入/出）',
      key: 'tokens',
      width: 140,
      render: (_: any, r: UsageByModel) =>
        `${r.input_tokens.toLocaleString()} / ${r.output_tokens.toLocaleString()}`,
    },
    {
      title: '成本 (¥)',
      dataIndex: 'cost',
      key: 'cost',
      width: 100,
      render: (v: number) => <Text strong style={{ color: '#cf1322' }}>{v.toFixed(4)}</Text>,
      defaultSortOrder: 'descend',
      sorter: (a, b) => a.cost - b.cost,
    },
  ];

  const topColumns: ColumnsType<TopQuery> = [
    { title: '#', key: 'rank', width: 44, render: (_: any, __: any, i: number) => i + 1 },
    { title: 'Query', dataIndex: 'query', key: 'query', ellipsis: true },
    { title: '次数', dataIndex: 'calls', key: 'calls', width: 64 },
    {
      title: '成本 (¥)',
      dataIndex: 'cost',
      key: 'cost',
      width: 96,
      render: (v: number) => <Text strong style={{ color: '#cf1322' }}>{v.toFixed(4)}</Text>,
    },
  ];

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      {/* ---- 页头：标题 + 时间范围 ---- */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>用量与成本</h1>
          <Text type="secondary">LLM 调用量、Token 消耗与成本分析（基于 trace 日志聚合）</Text>
        </div>
        <Radio.Group value={days} onChange={(e) => setDays(e.target.value)}>
          <Radio.Button value={7}>近 7 天</Radio.Button>
          <Radio.Button value={30}>近 30 天</Radio.Button>
        </Radio.Group>
      </div>

      {/* ---- 第一行：4 个核心 KPI ---- */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card style={{ height: '100%' }}>
            <Statistic title="LLM 调用数" value={summary.total_calls} prefix={<ApiOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="Token 总量"
              value={totalTokens}
              prefix={<FieldTimeOutlined />}
              suffix={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  入 {summary.total_input_tokens.toLocaleString()} / 出 {summary.total_output_tokens.toLocaleString()}
                </Text>
              }
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="总成本"
              value={summary.total_cost}
              precision={4}
              prefix={<DollarOutlined />}
              suffix="¥"
              valueStyle={{ color: '#cf1322' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="每千次调用成本"
              value={costPerK}
              precision={4}
              prefix={<DollarOutlined />}
              suffix="¥"
            />
          </Card>
        </Col>
      </Row>

      {/* ---- 第二行：趋势（宽）+ 缓存质量（窄） ---- */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={16}>
          <Card title={`Token 用量趋势（近 ${days} 天）`} size="small" style={{ height: '100%' }}>
            <TrendBars daily={daily} />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="语义缓存" size="small" style={{ height: '100%' }}>
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Progress
                type="dashboard"
                percent={cacheHitRate === null ? 0 : Math.round(cacheHitRate)}
                format={(p) => (cacheHitRate === null ? '—' : `${p}%`)}
                strokeColor="#6366f1"
              />
              <div style={{ marginTop: 4 }}>
                <Text type="secondary">缓存命中率</Text>
              </div>
            </div>
            <Divider style={{ margin: '12px 0' }} />
            <Row gutter={16}>
              <Col span={12}>
                <Statistic
                  title="缓存条目"
                  value={cache?.entries ?? 0}
                  prefix={<DatabaseOutlined />}
                  valueStyle={{ fontSize: 20 }}
                />
              </Col>
              <Col span={12}>
                <Statistic
                  title="累计命中"
                  value={cache?.total_hits ?? 0}
                  prefix={<ThunderboltOutlined />}
                  valueStyle={{ fontSize: 20 }}
                />
              </Col>
            </Row>
            <Divider style={{ margin: '12px 0' }} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              命中即跳过整条 Agent 链路，是降低成本与延迟的首要手段。
            </Text>
          </Card>
        </Col>
      </Row>

      {/* ---- 第三行：模型成本 + 高成本 Query ---- */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="按模型成本统计" size="small" style={{ height: '100%' }}>
            <Table scroll={{ x: "max-content" }}
              size="small"
              columns={modelColumns}
              dataSource={by_model}
              rowKey="model"
              pagination={false}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title="高成本 Query Top 10"
            size="small"
            extra={<Tag color="red">优化重点</Tag>}
            style={{ height: '100%' }}
          >
            <Table scroll={{ x: "max-content" }}
              size="small"
              columns={topColumns}
              dataSource={topQueries}
              rowKey="query"
              pagination={false}
              locale={{ emptyText: '暂无带 Token 用量的调用记录' }}
            />
          </Card>
        </Col>
      </Row>

      <div style={{ marginTop: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          成本按模型单价估算（qwen/gpt 系列内置价目表，其余按默认价）；语义缓存统计为累计值，与时间范围无关。
        </Text>
      </div>
    </div>
  );
}
