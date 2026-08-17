import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ExperimentOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  debugRetrieval,
  fetchKbs,
  fetchStageStats,
  type KnowledgeBase,
  type RetrievalDebugResult,
  type StageStat,
} from '../api';

const { Text, Paragraph } = Typography;

/**
 * 检索调优实验室：输入问题 → 查看召回明细（分数/来源/排名）、
 * 检索路线与各阶段耗时，定位「召回不准」发生在哪一级。
 */
export default function RetrievalLab() {
  const { message: msgApi } = App.useApp();

  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [question, setQuestion] = useState('');
  const [kbId, setKbId] = useState('default');
  const [topK, setTopK] = useState(4);
  const [rerank, setRerank] = useState(true);

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RetrievalDebugResult | null>(null);

  const [stageStats, setStageStats] = useState<StageStat[]>([]);

  useEffect(() => {
    fetchKbs().then(setKbs).catch(() => undefined);
    fetchStageStats()
      .then((r) => setStageStats(r.stages))
      .catch(() => undefined);
  }, []);

  const handleDebug = useCallback(async () => {
    const q = question.trim();
    if (!q) {
      msgApi.warning('请输入要调试的问题');
      return;
    }
    setLoading(true);
    try {
      setResult(await debugRetrieval({ question: q, kb_id: kbId, top_k: topK, rerank }));
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '检索调试失败');
    } finally {
      setLoading(false);
    }
  }, [question, kbId, topK, rerank, msgApi]);

  const stageColumns: ColumnsType<StageStat> = [
    { title: '阶段', dataIndex: 'stage', key: 'stage', render: (v: string) => <Text code>{v}</Text> },
    { title: '样本数', dataIndex: 'count', key: 'count', width: 90 },
    { title: '平均耗时', dataIndex: 'avg_ms', key: 'avg_ms', width: 110, render: (v: number) => `${v} ms` },
    { title: 'P95 耗时', dataIndex: 'p95_ms', key: 'p95_ms', width: 110, render: (v: number) => `${v} ms` },
  ];

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>检索调优实验室</h1>
        <Text type="secondary">输入问题查看召回结果、分数与检索路线，定位召回质量问题</Text>
      </div>

      {/* ---- 链路阶段耗时（基于 trace 聚合） ---- */}
      <Card title="链路阶段耗时（最近 5000 条 trace 聚合）" size="small" style={{ marginBottom: 16 }}>
        {stageStats.length === 0 ? (
          <Text type="secondary">暂无阶段埋点数据（发生检索后自动积累）</Text>
        ) : (
          <Table
            size="small"
            columns={stageColumns}
            dataSource={stageStats}
            rowKey="stage"
            pagination={false}
          />
        )}
      </Card>

      {/* ---- 调试表单 ---- */}
      <Card style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Input.Search
            placeholder="输入要调试的问题，例如：DocMind 的端口是多少？"
            enterButton={<><SearchOutlined /> 调试检索</>}
            size="large"
            loading={loading}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onSearch={handleDebug}
          />
          <Space wrap>
            <span>知识库：</span>
            <Select
              value={kbId}
              onChange={setKbId}
              style={{ width: 200 }}
              options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))}
            />
            <span>Top K：</span>
            <InputNumber min={1} max={20} value={topK} onChange={(v) => setTopK(v || 4)} />
            <span>Rerank 精排：</span>
            <Switch checked={rerank} onChange={setRerank} />
          </Space>
        </Space>
      </Card>

      {/* ---- 调试结果 ---- */}
      {loading && <Spin style={{ display: 'block', margin: '60px auto' }} size="large" />}

      {!loading && result && (
        <>
          <Card size="small" style={{ marginBottom: 12 }}>
            <Space>
              <ExperimentOutlined />
              <Text strong>检索路线：</Text>
              <Tag color="blue">{result.route}</Tag>
              <Text type="secondary">
                {result.hits.length > 0
                  ? `命中 ${result.hits.length} 条`
                  : '未命中任何证据（将触发拒答/通识标注）'}
              </Text>
            </Space>
          </Card>

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={8}>
              <Card title="各阶段耗时" size="small">
                <Table
                  size="small"
                  pagination={false}
                  rowKey="stage"
                  dataSource={result.stages.map((s) => ({
                    ...s,
                    count: s.count,
                  }))}
                  columns={[
                    { title: '阶段', dataIndex: 'stage', key: 'stage' },
                    {
                      title: '耗时',
                      dataIndex: 'duration_ms',
                      key: 'duration_ms',
                      width: 90,
                      render: (v: number) => `${v} ms`,
                    },
                    { title: '条数', dataIndex: 'count', key: 'count', width: 60 },
                  ]}
                />
              </Card>
            </Col>
            <Col xs={24} lg={16}>
              <Card title="召回结果（按最终分数排序）" size="small">
                {result.hits.length === 0 ? (
                  <Empty description="无召回结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                ) : (
                  <Space direction="vertical" style={{ width: '100%' }} size={10}>
                    {result.hits.map((h) => (
                      <Card
                        key={h.rank}
                        size="small"
                        style={{ background: h.rank === 1 ? '#f0f5ff' : undefined }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <Tag color={h.rank === 1 ? 'blue' : 'default'}>#{h.rank}</Tag>
                          <Tag>{h.source}{h.page ? ` · 第${h.page}页` : ''}</Tag>
                          <Text strong style={{ color: '#6366f1' }}>
                            分数 {h.score}
                          </Text>
                        </div>
                        <Paragraph
                          style={{ marginBottom: 0, fontSize: 12.5 }}
                          ellipsis={{ rows: 3, expandable: 'collapsible' }}
                        >
                          {h.text}
                        </Paragraph>
                      </Card>
                    ))}
                  </Space>
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}
