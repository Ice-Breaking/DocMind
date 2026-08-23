import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  ApiOutlined, CopyOutlined,
  KeyOutlined,
  PlusOutlined,
  StopOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  createApiKey,
  fetchApiKeys,
  fetchKbs,
  revokeApiKey,
  rotateApiKey,
  type ApiKey,
  type KnowledgeBase,
} from '../api';

const { Text, Paragraph } = Typography;

function formatTime(ts: number | null): string {
  return ts ? new Date(ts * 1000).toLocaleString() : '—';
}

/**
 * API Key 管理：供企业现有系统集成开放检索 API（POST /open/v1/retrieve）。
 * 明文仅创建/轮换时展示一次，之后只能看到前缀。
 */
export default function ApiKeys() {
  const { message: msgApi } = App.useApp();

  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ name: string; scope_kb_ids: string[]; expires_days?: number }>();

  /** 创建/轮换成功后的一次性明文展示 */
  const [secretModal, setSecretModal] = useState<{ name: string; key: string } | null>(null);
  const [keyTest, setKeyTest] = useState<{ status: 'idle' | 'loading' | 'ok' | 'fail'; msg?: string }>({ status: 'idle' });

  /** 创建成功(明文在手)时试调用一次开放检索,提前发现 scope/网络问题 */
  const testKey = async () => {
    if (!secretModal?.key) return;
    setKeyTest({ status: 'loading' });
    try {
      const r = await fetch('/open/v1/retrieve', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${secretModal.key}`,
                   'Content-Type': 'application/json' },
        body: JSON.stringify({ question: 'connectivity test', top_k: 1 }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.status === 403) throw new Error('密钥 scope 配置受限,检索被拒绝');
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      setKeyTest({ status: 'ok', msg: `连通正常,召回 ${d.count ?? 0} 条` });
    } catch (e) {
      setKeyTest({ status: 'fail', msg: e instanceof Error ? e.message : '测试失败' });
    }
  };

  const load = useCallback(async () => {
    try {
      const [ks, kbList] = await Promise.all([fetchApiKeys(), fetchKbs()]);
      setKeys(ks);
      setKbs(kbList);
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [msgApi]);

  useEffect(() => { load(); }, [load]);

  const kbName = (id: string) => kbs.find((k) => k.id === id)?.name || id;

  const handleCreate = async () => {
    let values: { name: string; scope_kb_ids: string[]; expires_days?: number };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      const k = await createApiKey({
        name: values.name,
        scope_kb_ids: values.scope_kb_ids || [],
        expires_days: values.expires_days,
      });
      setCreateOpen(false);
      form.resetFields();
      setSecretModal({ name: k.name, key: k.key || '' }); setKeyTest({ status: 'idle' });
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (k: ApiKey) => {
    try {
      await revokeApiKey(k.id);
      msgApi.success('密钥已吊销，立即失效');
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '吊销失败');
    }
  };

  const handleRotate = async (k: ApiKey) => {
    try {
      const nk = await rotateApiKey(k.id);
      msgApi.success('旧密钥已吊销，新密钥已签发');
      setSecretModal({ name: nk.name, key: nk.key || '' }); setKeyTest({ status: 'idle' });
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '轮换失败');
    }
  };

  const copyKey = async () => {
    if (!secretModal) return;
    try {
      await navigator.clipboard.writeText(secretModal.key);
      msgApi.success('已复制到剪贴板');
    } catch {
      msgApi.warning('复制失败，请手动选择复制');
    }
  };

  const columns: ColumnsType<ApiKey> = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: '密钥前缀',
      dataIndex: 'prefix',
      key: 'prefix',
      width: 140,
      render: (v: string) => <Text code>{v}…</Text>,
    },
    {
      title: '授权知识库',
      dataIndex: 'scope_kb_ids',
      key: 'scope',
      render: (ids: string[]) =>
        ids.length === 0
          ? <Tag color="blue">全部知识库</Tag>
          : ids.map((id) => <Tag key={id}>{kbName(id)}</Tag>),
    },
    {
      title: '状态',
      key: 'active',
      width: 90,
      render: (_: any, k: ApiKey) =>
        k.active ? <Tag color="green">有效</Tag> : <Tag color="red">已失效</Tag>,
    },
    {
      title: '过期时间',
      dataIndex: 'expires_at',
      key: 'expires_at',
      width: 160,
      render: (ts: number | null) => (ts ? formatTime(ts) : '永不过期'),
    },
    {
      title: '最近使用',
      dataIndex: 'last_used_at',
      key: 'last_used_at',
      width: 160,
      render: (ts: number | null) => formatTime(ts),
    },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160, render: formatTime },
    {
      title: '操作',
      key: 'action',
      width: 190,
      render: (_: any, k: ApiKey) => (
        <Space>
          <Popconfirm
            title="轮换密钥"
            description="旧密钥将立即吊销，集成方需更新为新密钥。继续？"
            okText="轮换"
            cancelText="取消"
            onConfirm={() => handleRotate(k)}
          >
            <Button size="small" icon={<SyncOutlined />}>轮换</Button>
          </Popconfirm>
          {k.active && (
            <Popconfirm
              title="吊销密钥"
              description="吊销后使用该密钥的集成将立即失败，不可恢复。"
              okText="吊销"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => handleRevoke(k)}
            >
              <Button size="small" danger icon={<StopOutlined />}>吊销</Button>
            </Popconfirm>
          )}
        </Space>
      ),
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
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>API Key 管理</h1>
          <Text type="secondary">
            为企业系统集成开放检索 API：<Text code>POST /open/v1/retrieve</Text>（Bearer 鉴权）
          </Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => { form.resetFields(); setCreateOpen(true); }}
        >
          创建密钥
        </Button>
      </div>

      <Table scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={keys}
        rowKey="id"
        loading={loading}
        pagination={false}
        size="middle"
      />

      {/* ---- 创建 Modal ---- */}
      <Modal
        title="创建 API Key"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：OA 系统集成" maxLength={50} />
          </Form.Item>
          <Form.Item
            name="scope_kb_ids"
            label="授权知识库"
            tooltip="不选表示授权全部知识库"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="默认全部知识库"
              options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))}
            />
          </Form.Item>
          <Form.Item name="expires_days" label="有效期">
            <Select
              placeholder="永不过期"
              allowClear
              options={[
                { value: 30, label: '30 天' },
                { value: 90, label: '90 天' },
                { value: 365, label: '1 年' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* ---- 明文一次性展示 Modal ---- */}
      <Modal
        title={<Space><KeyOutlined />密钥已签发 — {secretModal?.name}</Space>}
        open={!!secretModal}
        onCancel={() => setSecretModal(null)}
        footer={[
          <Button key="test" icon={<ApiOutlined />} loading={keyTest.status === 'loading'}
            onClick={testKey}>测试连通</Button>,
          <Button key="copy" type="primary" icon={<CopyOutlined />} onClick={copyKey}>复制密钥</Button>,
          <Button key="close" onClick={() => setSecretModal(null)}>我已保存</Button>,
        ]}
        closable={false}
        maskClosable={false}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="密钥明文只显示这一次，请立即复制保存。关闭后将无法再次查看，只能轮换重新签发。"
        />
        <Paragraph
          code
          copyable={false}
          style={{ wordBreak: 'break-all', padding: 8, background: '#f5f5f5', borderRadius: 6 }}
        >
          {secretModal?.key}
        </Paragraph>
        {keyTest.status === 'ok' && (
          <Alert type="success" showIcon style={{ marginTop: 4 }}
                 message={keyTest.msg} />
        )}
        {keyTest.status === 'fail' && (
          <Alert type="error" showIcon style={{ marginTop: 4 }}
                 message={keyTest.msg} />
        )}
      </Modal>
    </div>
  );
}
