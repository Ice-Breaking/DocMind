import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Button,
  Card,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { CheckCircleOutlined, StopOutlined, WarningOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { fetchBadcases, updateBadcase, type Badcase } from '../api';

const { Text } = Typography;

/**
 * Badcase 管理（独立页）：差评反馈的流转处理。
 * 支持按状态筛选 + 关键词搜索（匹配问题 / 用户 / 回答节选）。
 */
export default function Badcases() {
  const { message: msgApi } = App.useApp();

  const [badcases, setBadcases] = useState<Badcase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [keyword, setKeyword] = useState('');

  const [modalOpen, setModalOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<{ fid: number; status: string; note: string } | null>(null);
  const [noteText, setNoteText] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setBadcases(await fetchBadcases());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openModal = (fid: number, status: string, existingNote: string) => {
    setPendingAction({ fid, status, note: existingNote });
    setNoteText(existingNote || '');
    setModalOpen(true);
  };

  const handleConfirm = async () => {
    if (!pendingAction) return;
    try {
      await updateBadcase(pendingAction.fid, pendingAction.status, noteText);
      msgApi.success('状态已更新');
      setModalOpen(false);
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '更新失败');
    }
  };

  /* ---- 筛选 ---- */
  const kw = keyword.trim().toLowerCase();
  const filtered = badcases.filter((b) => {
    if (statusFilter !== 'all' && b.status !== statusFilter) return false;
    if (!kw) return true;
    return (
      b.question.toLowerCase().includes(kw) ||
      b.user.toLowerCase().includes(kw) ||
      (b.answer_excerpt || '').toLowerCase().includes(kw) ||
      (b.session_title || '').toLowerCase().includes(kw)
    );
  });

  const pendingCount = badcases.filter((b) => b.status === 'pending').length;

  const columns: ColumnsType<Badcase> = [
    {
      title: '时间',
      dataIndex: 'created',
      key: 'created',
      width: 160,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    { title: '用户', dataIndex: 'user', key: 'user', width: 100 },
    {
      title: '问题',
      dataIndex: 'question',
      key: 'question',
      ellipsis: true,
    },
    {
      title: '回答节选',
      dataIndex: 'answer_excerpt',
      key: 'answer_excerpt',
      ellipsis: true,
    },
    {
      title: '会话',
      dataIndex: 'session_title',
      key: 'session_title',
      ellipsis: true,
      width: 140,
    },
    {
      title: '备注',
      dataIndex: 'note',
      key: 'note',
      ellipsis: true,
      width: 140,
      render: (v: string) => v || <Text type="secondary">—</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => {
        const map: Record<string, { color: string; label: string }> = {
          pending: { color: 'orange', label: '待处理' },
          resolved: { color: 'green', label: '已解决' },
          ignored: { color: 'default', label: '已忽略' },
        };
        const cfg = map[s] || { color: 'default', label: s };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: any, record: Badcase) => {
        if (record.status === 'pending') {
          return (
            <Space>
              <Button size="small" type="primary" icon={<CheckCircleOutlined />} onClick={() => openModal(record.id, 'resolved', record.note)}>已解决</Button>
              <Button size="small" icon={<StopOutlined />} onClick={() => openModal(record.id, 'ignored', record.note)}>忽略</Button>
            </Space>
          );
        }
        return (
          <Space>
            <Button size="small" onClick={() => openModal(record.id, 'pending', record.note)}>重开</Button>
            <Button size="small" onClick={() => openModal(record.id, record.status, record.note)}>备注</Button>
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Badcase 管理</h1>
          <Text type="secondary">用户点踩的问答反馈，处理后用于改进检索与提示词</Text>
        </div>
        <Card size="small" style={{ minWidth: 140 }}>
          <Statistic
            title="待处理"
            value={pendingCount}
            valueStyle={pendingCount > 0 ? { color: '#cf1322', fontSize: 20 } : { fontSize: 20 }}
            prefix={<WarningOutlined />}
          />
        </Card>
      </div>

      {/* ---- 筛选栏 ---- */}
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          value={statusFilter}
          onChange={setStatusFilter}
          style={{ width: 140 }}
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'pending', label: '待处理' },
            { value: 'resolved', label: '已解决' },
            { value: 'ignored', label: '已忽略' },
          ]}
        />
        <Input.Search
          placeholder="搜索问题 / 用户 / 回答内容"
          allowClear
          style={{ width: 280 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={setKeyword}
        />
      </Space>

      {loading ? (
        <Spin style={{ display: 'block', margin: '80px auto' }} size="large" />
      ) : error ? (
        <Card>
          <Text type="danger">加载失败：{error}</Text>
          <Button style={{ marginLeft: 12 }} onClick={load}>重试</Button>
        </Card>
      ) : (
        <Table
          columns={columns}
          dataSource={filtered}
          rowKey="id"
          pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
          size="small"
        />
      )}

      {/* ---- 状态变更备注 Modal ---- */}
      <Modal
        title="处理备注（可选）"
        open={modalOpen}
        onOk={handleConfirm}
        onCancel={() => setModalOpen(false)}
        okText="确认"
        cancelText="取消"
      >
        <Input.TextArea
          rows={3}
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
          placeholder="记录处理结论，例如：知识库缺少该文档，已补充并重建索引"
          maxLength={500}
          showCount
        />
      </Modal>
    </div>
  );
}
