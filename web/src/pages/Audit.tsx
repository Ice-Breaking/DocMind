import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  App,
  Button,
  Card,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { exportAuditCsv, fetchAudit, type AuditEvent } from '../api';

const { Text } = Typography;

const ACTION_LABEL: Record<string, string> = {
  login: '登录',
  'kb.create': '创建知识库',
  'kb.delete': '删除知识库',
  'kb.reindex': '重建索引',
  'doc.upload': '上传文档',
  'doc.delete': '删除文档',
  'assistant.create': '创建助手',
  'assistant.delete': '删除助手',
  'apikey.create': '创建密钥',
  'apikey.revoke': '吊销密钥',
  'apikey.rotate': '轮换密钥',
  'model.create': '添加模型',
  'model.activate': '启用模型',
  'model.delete': '删除模型',
  'badcase.update': 'Badcase 处理',
  'backup.create': '创建备份',
  'alert.evaluate': '告警评估',
  'alert.ack': '确认告警',
  'alert.resolve': '解决告警',
};

const SENSITIVE_ACTIONS = new Set([
  'kb.delete', 'doc.delete', 'assistant.delete',
  'apikey.revoke', 'apikey.rotate', 'model.delete',
]);

/**
 * 审计中心：全量治理事件检索与 CSV 导出（企业合规留痕）。
 */
export default function Audit() {
  const { message: msgApi } = App.useApp();

  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [days, setDays] = useState(30);

  // 事件流水：过滤参数进 queryKey，变化自动重拉；失败保留旧数据仅弹 toast
  const {
    data: events = [],
    isPending: loading,
    error,
    refetch,
  } = useQuery<AuditEvent[], Error>({
    queryKey: ['audit', { actor, action, days }],
    queryFn: () => fetchAudit({ actor, action, days }),
  });

  /* 加载失败提示：与旧实现一致走全局 message */
  useEffect(() => {
    if (error) msgApi.error(error.message || '加载失败');
  }, [error, msgApi]);

  // CSV 导出：一次性动作，不涉及缓存失效
  const exportMut = useMutation({
    mutationFn: () => exportAuditCsv({ actor, action, days }),
    onSuccess: () => msgApi.success('CSV 已导出'),
    onError: (e: Error) => msgApi.error(e.message || '导出失败'),
  });

  const columns: ColumnsType<AuditEvent> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    { title: '操作人', dataIndex: 'actor', key: 'actor', width: 140 },
    {
      title: '事件',
      dataIndex: 'action',
      key: 'action',
      width: 140,
      render: (v: string) => (
        <Tag color={SENSITIVE_ACTIONS.has(v) ? 'red' : 'blue'}>
          {ACTION_LABEL[v] || v}
        </Tag>
      ),
    },
    { title: '对象', dataIndex: 'target', key: 'target', width: 220, ellipsis: true },
    {
      title: '详情',
      dataIndex: 'detail',
      key: 'detail',
      ellipsis: true,
      render: (v: string) => v || <Text type="secondary">—</Text>,
    },
  ];

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
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>审计中心</h1>
          <Text type="secondary">登录、知识库、密钥、模型等治理操作的全量留痕</Text>
        </div>
        <Button
          icon={<DownloadOutlined />}
          loading={exportMut.isPending}
          onClick={() => exportMut.mutate()}
        >
          导出 CSV
        </Button>
      </div>

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          value={days}
          onChange={setDays}
          style={{ width: 120 }}
          options={[
            { value: 1, label: '最近 1 天' },
            { value: 7, label: '最近 7 天' },
            { value: 30, label: '最近 30 天' },
            { value: 0, label: '全部' },
          ]}
        />
        <Select
          value={action}
          onChange={setAction}
          style={{ width: 160 }}
          options={[
            { value: '', label: '全部事件' },
            ...Object.entries(ACTION_LABEL).map(([v, l]) => ({ value: v, label: l })),
          ]}
        />
        <Input.Search
          placeholder="操作人"
          allowClear
          style={{ width: 180 }}
          value={actor}
          onChange={(e) => setActor(e.target.value)}
          onSearch={setActor}
        />
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
      </Space>

      <Table scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={events}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
        size="small"
      />

      <Card size="small" style={{ marginTop: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          合规说明：审计记录写入本地数据库，随一键备份归档；敏感操作（删除/吊销类）以红色标注。
        </Text>
      </Card>
    </div>
  );
}
