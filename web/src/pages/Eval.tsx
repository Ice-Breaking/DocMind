import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import {
  CaretRightOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  LikeOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  createEvalDataset,
  deleteEvalDataset,
  fetchEvalDatasets,
  fetchEvalRun,
  fetchEvalRuns,
  fetchKbs,
  fetchQuality,
  runEval,
  updateEvalDataset,
  type EvalDataset,
  type EvalRun,
  type KnowledgeBase,
  type QualityData,
} from '../api';

const { Text } = Typography;

const MODE_LABEL: Record<string, string> = {
  dense: '纯向量',
  rrf: '混合 RRF',
  rerank: '混合 + Rerank',
};

const STATUS_CFG: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '排队中' },
  running: { color: 'processing', label: '运行中' },
  done: { color: 'success', label: '完成' },
  error: { color: 'error', label: '失败' },
};

/* ------------------------------------------------------------------ */
/*  Tab 1: 评测集管理                                                  */
/* ------------------------------------------------------------------ */

function DatasetsTab({ kbs }: { kbs: KnowledgeBase[] }) {
  const { message: msgApi } = App.useApp();
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<EvalDataset | null>(null);
  const [saving, setSaving] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [runTarget, setRunTarget] = useState<EvalDataset | null>(null);
  const [runMode, setRunMode] = useState('rerank');
  const [form] = Form.useForm<{ name: string; kb_id: string; items_json: string }>();

  const load = useCallback(async () => {
    try {
      setDatasets(await fetchEvalDatasets());
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [msgApi]);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ kb_id: 'default', items_json: '[\n  {"question": "", "expected": ""}\n]' });
    setModalOpen(true);
  };

  const openEdit = (ds: EvalDataset) => {
    setEditing(ds);
    form.resetFields();
    form.setFieldsValue({
      name: ds.name,
      kb_id: ds.kb_id,
      items_json: JSON.stringify(ds.items, null, 2),
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    let values: { name: string; kb_id: string; items_json: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    let items: unknown;
    try {
      items = JSON.parse(values.items_json);
      if (!Array.isArray(items)) throw new Error('items 必须是数组');
      for (const it of items as any[]) {
        if (!it || typeof it.question !== 'string' || typeof it.expected !== 'string') {
          throw new Error('每条样本需包含 question 与 expected 字符串字段');
        }
      }
    } catch (e: unknown) {
      msgApi.error(`样本 JSON 非法：${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await updateEvalDataset(editing.id, {
          name: values.name,
          kb_id: values.kb_id,
          items: items as any,
        });
        msgApi.success('评测集已更新');
      } else {
        await createEvalDataset({ name: values.name, kb_id: values.kb_id, items: items as any });
        msgApi.success('评测集已创建');
      }
      setModalOpen(false);
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (ds: EvalDataset) => {
    try {
      await deleteEvalDataset(ds.id);
      msgApi.success('评测集已删除');
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const handleRun = async () => {
    if (!runTarget) return;
    try {
      await runEval(runTarget.id, { mode: runMode, top_k: 4 });
      msgApi.success('评测已启动，请到「运行记录」查看进度');
      setRunOpen(false);
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '启动失败');
    }
  };

  const kbName = (id: string) => kbs.find((k) => k.id === id)?.name || id;

  const columns: ColumnsType<EvalDataset> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 180 },
    {
      title: '目标知识库',
      dataIndex: 'kb_id',
      key: 'kb_id',
      width: 150,
      render: (v: string) => <Tag>{kbName(v)}</Tag>,
    },
    {
      title: '样本数',
      key: 'count',
      width: 80,
      render: (_: any, r: EvalDataset) => r.items.length,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_: any, r: EvalDataset) => (
        <Space>
          <Button
            size="small"
            type="primary"
            icon={<CaretRightOutlined />}
            onClick={() => { setRunTarget(r); setRunMode('rerank'); setRunOpen(true); }}
          >
            运行评测
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>编辑</Button>
          <Popconfirm
            title={`删除评测集「${r.name}」？运行记录将一并删除`}
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => handleDelete(r)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建评测集</Button>
      </div>
      <Table scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={datasets}
        rowKey="id"
        loading={loading}
        pagination={false}
        size="small"
      />

      <Modal
        title={editing ? `编辑评测集 — ${editing.name}` : '新建评测集'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={680}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：产品文档回归集" maxLength={50} />
          </Form.Item>
          <Form.Item name="kb_id" label="目标知识库">
            <Select options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))} />
          </Form.Item>
          <Form.Item
            name="items_json"
            label="样本（JSON 数组：question = 问题，expected = 应命中的文档名）"
            rules={[{ required: true, message: '请输入样本' }]}
          >
            <Input.TextArea
              rows={10}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
              placeholder='[{"question": "什么是 RAG？", "expected": "AI大模型知识问答.md"}]'
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`运行评测 — ${runTarget?.name || ''}`}
        open={runOpen}
        onOk={handleRun}
        onCancel={() => setRunOpen(false)}
        okText="开始运行"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text type="secondary">
            将逐条检索 {runTarget?.items.length || 0} 个样本并计算 Recall@4 / MRR，
            启用 Rerank 时约需数十秒，运行期间可离开本页。
          </Text>
          <Select
            value={runMode}
            onChange={setRunMode}
            style={{ width: 200 }}
            options={[
              { value: 'rerank', label: '混合 + Rerank（推荐）' },
              { value: 'rrf', label: '混合 RRF（无精排）' },
              { value: 'dense', label: '纯向量' },
            ]}
          />
        </Space>
      </Modal>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 2: 运行记录                                                    */
/* ------------------------------------------------------------------ */

function RunsTab() {
  const { message: msgApi } = App.useApp();
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<number | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [ds, rs] = await Promise.all([fetchEvalDatasets(), fetchEvalRuns()]);
      setDatasets(ds);
      setRuns(rs);
    } catch (e: unknown) {
      if (!silent) msgApi.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      if (!silent) setLoading(false);
    }
  }, [msgApi]);

  useEffect(() => {
    load();
    // 有 pending/running 任务时每 4 秒静默刷新
    timerRef.current = window.setInterval(() => {
      setRuns((cur) => {
        if (cur.some((r) => r.status === 'pending' || r.status === 'running')) {
          load(true);
        }
        return cur;
      });
    }, 4000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [load]);

  const dsName = (id: number) => datasets.find((d) => d.id === id)?.name || `#${id}`;

  const columns: ColumnsType<EvalRun> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    {
      title: '评测集',
      dataIndex: 'dataset_id',
      key: 'dataset_id',
      width: 160,
      render: (v: number) => dsName(v),
    },
    {
      title: '检索路线',
      dataIndex: 'mode',
      key: 'mode',
      width: 130,
      render: (v: string) => <Tag>{MODE_LABEL[v] || v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => {
        const cfg = STATUS_CFG[v] || { color: 'default', label: v };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: 'Recall@4',
      dataIndex: 'recall',
      key: 'recall',
      width: 100,
      render: (v: number, r: EvalRun) =>
        r.status === 'done' ? (
          <Text strong style={{ color: v >= 0.8 ? '#389e0d' : v >= 0.6 ? '#d48806' : '#cf1322' }}>
            {(v * 100).toFixed(1)}%
          </Text>
        ) : '—',
    },
    {
      title: 'MRR',
      dataIndex: 'mrr',
      key: 'mrr',
      width: 80,
      render: (v: number, r: EvalRun) => (r.status === 'done' ? v.toFixed(3) : '—'),
    },
    {
      title: '命中/总数',
      key: 'hitrate',
      width: 100,
      render: (_: any, r: EvalRun) => (r.status === 'done' ? `${r.hits} / ${r.total}` : '—'),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 90,
      render: (v: number, r: EvalRun) => (r.status === 'done' ? `${(v / 1000).toFixed(1)} s` : '—'),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={() => load()}>刷新</Button>
        <Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
          运行中的任务每 4 秒自动刷新；展开行查看未命中明细
        </Text>
      </div>
      <Table scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={runs}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 15 }}
        size="small"
        expandable={{
          expandedRowRender: (r: EvalRun) => <RunDetails runId={r.id} status={r.status} />,
          rowExpandable: (r: EvalRun) => r.status === 'done' || r.status === 'error',
        }}
      />
    </>
  );
}

function RunDetails({ runId, status }: { runId: number; status: string }) {
  const [run, setRun] = useState<EvalRun | null>(null);

  useEffect(() => {
    if (status !== 'done' && status !== 'error') return;
    fetchEvalRun(runId).then(setRun).catch(() => undefined);
  }, [runId, status]);

  if (status === 'error') return <Text type="danger">运行失败，请查看后端日志</Text>;
  if (!run) return <Spin size="small" />;

  const misses = (run.details || []).filter((d) => !d.hit_rank);
  if (!misses.length) {
    return <Text type="success">🎉 全部样本命中</Text>;
  }
  return (
    <Table scroll={{ x: "max-content" }}
      size="small"
      pagination={false}
      rowKey="question"
      dataSource={misses}
      columns={[
        { title: '未命中问题', dataIndex: 'question', key: 'question', ellipsis: true },
        { title: '期望文档', dataIndex: 'expected', key: 'expected', width: 200 },
        { title: '实际 Top1', dataIndex: 'top1', key: 'top1', width: 200, render: (v: string) => v || '—' },
        {
          title: 'Top1 分数',
          dataIndex: 'top1_score',
          key: 'top1_score',
          width: 100,
          render: (v: number | null) => (v === null ? '—' : v),
        },
      ]}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 3: 质量监控                                                    */
/* ------------------------------------------------------------------ */

function QualityTab() {
  const [data, setData] = useState<QualityData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchQuality(30)
      .then(setData)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin style={{ display: 'block', margin: '60px auto' }} size="large" />;
  if (!data) return <Text type="danger">质量数据加载失败</Text>;

  const totalFb = data.feedback.up + data.feedback.down;
  const goodRate = totalFb > 0 ? (data.feedback.up / totalFb) * 100 : null;

  return (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="好评率（累计）"
              value={goodRate ?? 0}
              precision={1}
              suffix={goodRate === null ? '—' : '%'}
              prefix={<LikeOutlined />}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              👍 {data.feedback.up} / 👎 {data.feedback.down}
            </Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待处理 Badcase"
              value={data.feedback.badcase_pending}
              valueStyle={data.feedback.badcase_pending > 0 ? { color: '#cf1322' } : undefined}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="证据拒答（近 30 天）"
              value={data.refusals}
              prefix={<StopOutlined />}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>拒答是防幻觉的有效拦截</Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="语义缓存"
              value={data.cache.entries}
              suffix="条目"
              prefix={<ThunderboltOutlined />}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>累计命中 {data.cache.total_hits} 次</Text>
          </Card>
        </Col>
      </Row>

      <Card title="评测 Recall 趋势（按天取各路线最高值）" style={{ marginTop: 16 }}>
        {data.eval_trend.length === 0 ? (
          <Text type="secondary">暂无评测运行记录，先到「评测集」页运行一次评测</Text>
        ) : (
          <Table scroll={{ x: "max-content" }}
            size="small"
            pagination={false}
            rowKey={(r) => `${r.date}-${r.mode}`}
            dataSource={data.eval_trend}
            columns={[
              { title: '日期', dataIndex: 'date', key: 'date', width: 120 },
              {
                title: '检索路线',
                dataIndex: 'mode',
                key: 'mode',
                width: 150,
                render: (v: string) => <Tag>{MODE_LABEL[v] || v}</Tag>,
              },
              {
                title: 'Recall@4',
                dataIndex: 'recall',
                key: 'recall',
                render: (v: number) => (
                  <Space style={{ width: '100%' }}>
                    <div style={{ width: 240, height: 10, background: '#f0f0f0', borderRadius: 5 }}>
                      <div
                        style={{
                          width: `${Math.max(2, v * 100)}%`,
                          height: '100%',
                          borderRadius: 5,
                          background: v >= 0.8 ? '#52c41a' : v >= 0.6 ? '#faad14' : '#ff4d4f',
                        }}
                      />
                    </div>
                    <Text strong>{(v * 100).toFixed(1)}%</Text>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>

      <div style={{ marginTop: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          <CheckCircleOutlined /> 质量闭环：线上差评 → Badcase 处理 → 评测集回归 → 检索调优
        </Text>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Eval() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);

  useEffect(() => {
    fetchKbs().then(setKbs).catch(() => undefined);
  }, []);

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>评测与质量</h1>
        <Text type="secondary">离线评测检索质量，聚合线上反馈信号，形成质量闭环</Text>
      </div>
      <Tabs
        defaultActiveKey="datasets"
        items={[
          { key: 'datasets', label: '评测集', children: <DatasetsTab kbs={kbs} /> },
          { key: 'runs', label: '运行记录', children: <RunsTab /> },
          { key: 'quality', label: '质量监控', children: <QualityTab /> },
        ]}
      />
    </div>
  );
}
