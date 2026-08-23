import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App,
  Button,
  Card,
  Col,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  EyeOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  ackAlert,
  evaluateAlerts,
  fetchAlerts,
  fetchSla,
  resolveAlert,
  type AlertItem,
  type SlaData,
} from '../api';

const { Text } = Typography;

const TYPE_LABEL: Record<string, { color: string; label: string }> = {
  quality: { color: 'orange', label: '质量' },
  cost: { color: 'gold', label: '成本' },
  error: { color: 'red', label: '稳定性' },
  ingest: { color: 'purple', label: '入库' },
};

const STATUS_CFG: Record<string, { color: string; label: string }> = {
  open: { color: 'error', label: '待处理' },
  acknowledged: { color: 'processing', label: '已确认' },
  resolved: { color: 'success', label: '已解决' },
};

/**
 * 告警与 SLA：规则引擎周期评估（质量/成本/稳定性/入库）+ 服务质量统计。
 */
export default function Alerts() {
  const { message: msgApi } = App.useApp();
  const queryClient = useQueryClient();

  // 告警列表与 SLA 统计：两条独立 query（并行拉取、各自缓存）；
  // 失败保留上次数据仅弹 toast（对齐旧 load() 语义）
  const {
    data: alerts = [],
    isPending: loading,
    error,
  } = useQuery<AlertItem[], Error>({ queryKey: ['alerts'], queryFn: () => fetchAlerts() });
  const { data: sla = null } = useQuery<SlaData, Error>({
    queryKey: ['sla', 7],
    queryFn: () => fetchSla(7),
  });

  useEffect(() => {
    if (error) msgApi.error(error.message || '加载失败');
  }, [error, msgApi]);

  /* 操作类 mutation：成功 toast + 失效相关 query 自动重拉 */
  const evaluateMut = useMutation({
    mutationFn: evaluateAlerts,
    onSuccess: (r) => {
      msgApi.success(r.created.length > 0
        ? `评估完成，新增 ${r.created.length} 条告警`
        : '评估完成，无新增告警');
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      queryClient.invalidateQueries({ queryKey: ['sla'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '评估失败'),
  });

  const ackMut = useMutation({
    mutationFn: (a: AlertItem) => ackAlert(a.id),
    onSuccess: () => {
      msgApi.success('已确认');
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '操作失败'),
  });

  const resolveMut = useMutation({
    mutationFn: (a: AlertItem) => resolveAlert(a.id),
    onSuccess: () => {
      msgApi.success('已标记解决');
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      queryClient.invalidateQueries({ queryKey: ['sla'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '操作失败'),
  });

  const columns: ColumnsType<AlertItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    {
      title: '类别',
      dataIndex: 'type',
      key: 'type',
      width: 90,
      render: (v: string) => {
        const cfg = TYPE_LABEL[v] || { color: 'default', label: v };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      width: 80,
      render: (v: string) =>
        v === 'critical' ? <Tag color="red">严重</Tag> : <Tag color="orange">警告</Tag>,
    },
    { title: '内容', dataIndex: 'message', key: 'message', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v: string) => {
        const cfg = STATUS_CFG[v] || { color: 'default', label: v };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 170,
      render: (_: any, a: AlertItem) => (
        <Space>
          {a.status === 'open' && (
            <Button size="small" icon={<EyeOutlined />} onClick={() => ackMut.mutate(a)}>确认</Button>
          )}
          {a.status !== 'resolved' && (
            <Popconfirm
              title="确认该告警已处理完毕？"
              okText="解决"
              cancelText="取消"
              onConfirm={() => resolveMut.mutate(a)}
            >
              <Button size="small" type="primary" ghost icon={<CheckCircleOutlined />}>解决</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const openCount = alerts.filter((a) => a.status === 'open').length;
  const availabilityPct = sla ? (sla.availability * 100).toFixed(2) : '—';

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>告警与 SLA</h1>
          <Text type="secondary">
            规则引擎每 10 分钟自动评估（Badcase 积压 / 成本 / 链路错误 / 入库失败）
          </Text>
        </div>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          loading={evaluateMut.isPending}
          onClick={() => evaluateMut.mutate()}
        >
          立即评估
        </Button>
      </div>

      {/* ---- SLA 卡片 ---- */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="可用率（近 7 天）"
              value={availabilityPct}
              suffix="%"
              prefix={<SafetyCertificateOutlined />}
              valueStyle={{
                color: sla && sla.availability >= 0.99 ? '#389e0d'
                  : sla && sla.availability >= 0.95 ? '#d48806' : '#cf1322',
              }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {sla ? `${sla.ok} / ${sla.total} 次 LLM 调用成功` : ''}
            </Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="P50 延迟" value={sla?.p50_ms ?? 0} suffix="ms" prefix={<ThunderboltOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="P95 延迟" value={sla?.p95_ms ?? 0} suffix="ms" prefix={<ThunderboltOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待处理告警"
              value={openCount}
              prefix={<AlertOutlined />}
              valueStyle={openCount > 0 ? { color: '#cf1322' } : undefined}
            />
          </Card>
        </Col>
      </Row>

      {/* ---- SLA 每日趋势 ---- */}
      {sla && sla.daily.length > 0 && (
        <Card title="近 7 天 SLA 趋势" size="small" style={{ marginBottom: 16 }}>
          <Table scroll={{ x: "max-content" }}
            size="small"
            pagination={false}
            rowKey="date"
            dataSource={sla.daily}
            columns={[
              { title: '日期', dataIndex: 'date', key: 'date', width: 120 },
              { title: '调用数', dataIndex: 'total', key: 'total', width: 100 },
              {
                title: '可用率',
                dataIndex: 'availability',
                key: 'availability',
                width: 120,
                render: (v: number) => (
                  <Text strong style={{ color: v >= 0.99 ? '#389e0d' : v >= 0.95 ? '#d48806' : '#cf1322' }}>
                    {(v * 100).toFixed(1)}%
                  </Text>
                ),
              },
              { title: 'P95 延迟', dataIndex: 'p95_ms', key: 'p95_ms', width: 120, render: (v: number) => `${v} ms` },
            ]}
          />
        </Card>
      )}

      {/* ---- 告警列表 ---- */}
      <Table scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={alerts}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 条` }}
        size="small"
        locale={{ emptyText: '暂无告警记录' }}
      />
    </div>
  );
}
