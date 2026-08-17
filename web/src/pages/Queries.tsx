import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Button,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { fetchAdminQueries, type UserQuery } from '../api';

const { Text } = Typography;

/**
 * 提问记录（仅管理员）：全量用户的检索提问流水，
 * 支持按用户 / 关键词 / 时间过滤，用于行为审计与问题挖掘。
 */
export default function Queries() {
  const { message: msgApi } = App.useApp();

  const [rows, setRows] = useState<UserQuery[]>([]);
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState('');
  const [q, setQ] = useState('');
  const [days, setDays] = useState(7);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchAdminQueries({ user, q, days, limit: 1000 }));
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [user, q, days, msgApi]);

  useEffect(() => { load(); }, [load]);

  const columns: ColumnsType<UserQuery> = [
    {
      title: '提问时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    {
      title: '用户',
      dataIndex: 'user',
      key: 'user',
      width: 150,
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '会话',
      dataIndex: 'session_title',
      key: 'session_title',
      width: 200,
      ellipsis: true,
      render: (v: string) => v || <Text type="secondary">（未命名会话）</Text>,
    },
    {
      title: '提问内容',
      dataIndex: 'question',
      key: 'question',
      render: (v: string) => (
        <Text style={{ whiteSpace: 'pre-wrap' }}>{v}</Text>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>提问记录</h1>
        <Text type="secondary">全部用户的检索提问流水，用于行为审计与高频问题挖掘</Text>
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
        <Input
          placeholder="按用户名过滤"
          allowClear
          style={{ width: 180 }}
          value={user}
          onChange={(e) => setUser(e.target.value)}
        />
        <Input.Search
          placeholder="按提问关键词搜索"
          allowClear
          style={{ width: 260 }}
          enterButton={<><SearchOutlined /> 搜索</>}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onSearch={setQ}
        />
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>

      <Table
        columns={columns}
        dataSource={rows}
        rowKey={(r) => `${r.session_id}-${r.created_at}-${r.question.slice(0, 12)}`}
        loading={loading}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条提问` }}
        size="small"
        locale={{ emptyText: '暂无提问记录' }}
      />
    </div>
  );
}
