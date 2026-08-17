import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  DatePicker,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { fetchKbs, fetchTraces, type TraceItem } from '../api';

const { Text } = Typography;
const { RangePicker } = DatePicker;

const KIND_LABEL: Record<string, { color: string; label: string }> = {
  generation: { color: 'blue', label: 'LLM 生成' },
  retrieval: { color: 'purple', label: '检索' },
  span: { color: 'default', label: '工具/其他' },
};

/** 输入内容预览：消息数组取最后一条用户内容，其余直接字符串化截断 */
function inputPreview(item: TraceItem): string {
  const input = item.input;
  if (Array.isArray(input)) {
    for (let i = input.length - 1; i >= 0; i--) {
      const m = input[i];
      if (m && typeof m === 'object' && m.role === 'user') {
        return String(m.content || '').slice(0, 60);
      }
    }
    return JSON.stringify(input).slice(0, 60);
  }
  if (typeof input === 'string') return input.slice(0, 60);
  return input ? JSON.stringify(input).slice(0, 60) : '—';
}

/**
 * 检索日志：trace_log.jsonl 的分页检索。
 * 支持类型 / 状态 / 关键词 / 时间范围过滤（服务端过滤）。
 */
export default function Traces() {
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [kb, setKb] = useState('');
  const [kbOptions, setKbOptions] = useState<{ value: string; label: string }[]>([]);
  const [q, setQ] = useState('');
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [items, setItems] = useState<TraceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchTraces({
        page,
        page_size: pageSize,
        kind,
        status,
        kb,
        q,
        start: range?.[0]?.format('YYYY-MM-DD') ?? '',
        end: range?.[1]?.format('YYYY-MM-DD') ?? '',
      });
      setItems(r.items);
      setTotal(r.total);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, kind, status, kb, q, range]);

  useEffect(() => { load(); }, [load]);

  // KB 过滤选项：默认库 collection 名为 knowledge，其余为 kb_{id}
  useEffect(() => {
    fetchKbs()
      .then((kbs) => setKbOptions([
        { value: 'knowledge', label: '默认知识库' },
        ...kbs.filter((k) => k.id !== 'default').map((k) => ({ value: `kb_${k.id}`, label: k.name })),
      ]))
      .catch(() => undefined);
  }, []);

  const columns: ColumnsType<TraceItem> = [
    { title: '时间', dataIndex: 'ts', key: 'ts', width: 160 },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 150,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '类型',
      dataIndex: 'kind',
      key: 'kind',
      width: 100,
      render: (v: string) => {
        const cfg = KIND_LABEL[v] || { color: 'default', label: v };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v: string) =>
        v === 'error' ? <Tag color="red">失败</Tag> : <Tag color="green">成功</Tag>,
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 90,
      render: (v?: number) => (typeof v === 'number' ? `${v} ms` : '—'),
    },
    { title: '模型', dataIndex: 'model', key: 'model', width: 120, render: (v?: string) => v || '—' },
    {
      title: 'Token（入/出）',
      key: 'tokens',
      width: 120,
      render: (_: any, r: TraceItem) =>
        r.usage ? `${r.usage.input} / ${r.usage.output}` : '—',
    },
    {
      title: '输入预览',
      key: 'input',
      ellipsis: true,
      render: (_: any, r: TraceItem) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{inputPreview(r)}</Text>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>检索日志</h1>
        <Text type="secondary">LLM / 检索 / 工具调用的 trace 记录（最近 5000 条）</Text>
      </div>

      {/* ---- 过滤栏 ---- */}
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          value={kind}
          onChange={(v) => { setKind(v); setPage(1); }}
          style={{ width: 130 }}
          options={[
            { value: '', label: '全部类型' },
            { value: 'generation', label: 'LLM 生成' },
            { value: 'retrieval', label: '检索' },
            { value: 'span', label: '工具/其他' },
          ]}
        />
        <Select
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          style={{ width: 120 }}
          options={[
            { value: '', label: '全部状态' },
            { value: 'ok', label: '成功' },
            { value: 'error', label: '失败' },
          ]}
        />
        <Select
          value={kb}
          onChange={(v) => { setKb(v); setPage(1); }}
          style={{ width: 150 }}
          options={[{ value: '', label: '全部知识库' }, ...kbOptions]}
        />
        <RangePicker
          value={range as any}
          onChange={(v) => { setRange(v as any); setPage(1); }}
          disabledDate={(d) => d.isAfter(dayjs())}
        />
        <Input.Search
          placeholder="关键词（名称 / 模型）"
          allowClear
          style={{ width: 220 }}
          defaultValue={q}
          onSearch={(v) => { setQ(v); setPage(1); }}
        />
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>

      {error && (
        <Card style={{ marginBottom: 16 }}>
          <Text type="danger">加载失败：{error}</Text>
          <Button style={{ marginLeft: 12 }} onClick={load}>重试</Button>
        </Card>
      )}

      <Table
        columns={columns}
        dataSource={items}
        rowKey={(r) => r.id || `${r.ts}-${r.name}`}
        loading={loading}
        size="small"
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
        expandable={{
          expandedRowRender: (r) => (
            <pre style={{ margin: 0, fontSize: 12, maxHeight: 320, overflow: 'auto' }}>
              {JSON.stringify(r, null, 2)}
            </pre>
          ),
        }}
      />
    </div>
  );
}
