import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Alert,
  Button,
  Card,
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
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  activateModel,
  createModel,
  deleteModel,
  fetchModels,
  testModel,
  updateModel,
  type ModelConfig,
} from '../api';

const { Text } = Typography;

const KIND_CFG: Record<string, { label: string; color: string; hint: string }> = {
  llm: { label: 'LLM 对话模型', color: 'blue', hint: '切换后新问答立即生效' },
  embedding: { label: 'Embedding 向量模型', color: 'purple', hint: '切换后仅影响新增切片，存量知识库需全量重建索引（向量维度可能不同）' },
  rerank: { label: 'Rerank 精排模型', color: 'cyan', hint: '切换后检索精排立即生效' },
};

interface FormValues {
  name: string;
  kind: string;
  base_url: string;
  api_key: string;
  model_name: string;
}

/**
 * 模型管理：LLM / Embedding / Rerank 按类型在线配置，
 * 支持连通性测试与生效切换（优先于 .env 配置）。
 */
export default function Models() {
  const { message: msgApi } = App.useApp();

  const [models, setModels] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<number | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<FormValues>();

  const load = useCallback(async () => {
    try {
      setModels(await fetchModels());
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
    form.setFieldsValue({ kind: 'llm', base_url: '', api_key: '' });
    setModalOpen(true);
  };

  const openEdit = (m: ModelConfig) => {
    setEditing(m);
    form.resetFields();
    form.setFieldsValue({
      name: m.name,
      kind: m.kind,
      base_url: m.base_url,
      api_key: '',
      model_name: m.model_name,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await updateModel(editing.id, {
          name: values.name,
          base_url: values.base_url,
          api_key: values.api_key || undefined,   // 留空 = 不改动
          model_name: values.model_name,
        });
        msgApi.success('模型已更新');
      } else {
        await createModel(values);
        msgApi.success('模型已添加，点击「设为生效」启用');
      }
      setModalOpen(false);
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (m: ModelConfig) => {
    setTesting(m.id);
    try {
      const r = await testModel(m.id);
      if (r.ok) {
        msgApi.success(`连通正常（${r.latency_ms} ms）：${r.detail}`);
      } else {
        msgApi.error(`连通失败：${r.detail}`);
      }
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '测试失败');
    } finally {
      setTesting(null);
    }
  };

  const handleActivate = async (m: ModelConfig) => {
    try {
      await activateModel(m.id);
      msgApi.success(`「${m.name}」已设为生效（${KIND_CFG[m.kind]?.label}）`);
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '操作失败');
    }
  };

  const handleDelete = async (m: ModelConfig) => {
    try {
      await deleteModel(m.id);
      msgApi.success('模型已删除');
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const columns: ColumnsType<ModelConfig> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (v: string, m) => (
        <Space>
          <Text strong>{v}</Text>
          {m.is_active === 1 && <Tag color="green" icon={<CheckCircleOutlined />}>生效中</Tag>}
        </Space>
      ),
    },
    { title: '模型标识', dataIndex: 'model_name', key: 'model_name', width: 200, render: (v: string) => <Text code>{v}</Text> },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      key: 'base_url',
      ellipsis: true,
      render: (v: string) => v || <Text type="secondary">默认（百炼）</Text>,
    },
    { title: 'API Key', dataIndex: 'api_key_masked', key: 'api_key_masked', width: 110, render: (v: string) => v || <Text type="secondary">默认</Text> },
    {
      title: '操作',
      key: 'action',
      width: 320,
      render: (_: any, m) => (
        <Space>
          <Button
            size="small"
            icon={<ThunderboltOutlined />}
            loading={testing === m.id}
            onClick={() => handleTest(m)}
          >
            测试
          </Button>
          {m.is_active !== 1 && (
            <Button size="small" type="primary" ghost onClick={() => handleActivate(m)}>设为生效</Button>
          )}
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(m)}>编辑</Button>
          <Popconfirm
            title={`删除模型「${m.name}」？`}
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => handleDelete(m)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>模型管理</h1>
          <Text type="secondary">按类型配置 LLM / Embedding / Rerank，优先于环境变量生效</Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加模型</Button>
      </div>

      {Object.entries(KIND_CFG).map(([kind, cfg]) => {
        const rows = models.filter((m) => m.kind === kind);
        return (
          <Card
            key={kind}
            title={<Space><Tag color={cfg.color}>{cfg.label}</Tag><Text type="secondary" style={{ fontSize: 12 }}>{cfg.hint}</Text></Space>}
            size="small"
            style={{ marginBottom: 16 }}
          >
            <Table scroll={{ x: "max-content" }}
              columns={columns}
              dataSource={rows}
              rowKey="id"
              loading={loading}
              pagination={false}
              size="small"
              locale={{ emptyText: '未配置，使用环境变量默认值' }}
            />
          </Card>
        );
      })}

      {/* ---- 添加 / 编辑 Modal ---- */}
      <Modal
        title={editing ? `编辑模型 — ${editing.name}` : '添加模型'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        {editing?.kind === 'embedding' && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="更换 Embedding 模型后，存量知识库必须全量重建索引，否则新旧向量维度不一致会导致检索异常。"
          />
        )}
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：百炼 qwen-plus" maxLength={50} />
          </Form.Item>
          <Form.Item name="kind" label="类型" rules={[{ required: true }]}>
            <Select
              disabled={!!editing}
              options={Object.entries(KIND_CFG).map(([k, c]) => ({ value: k, label: c.label }))}
            />
          </Form.Item>
          <Form.Item name="model_name" label="模型标识" rules={[{ required: true, message: '请输入模型标识' }]}>
            <Input placeholder="例如：qwen-plus / text-embedding-v3 / gte-rerank-v2" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL（留空用默认百炼地址）">
            <Input placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label={editing ? 'API Key（留空保持不变）' : 'API Key（留空用环境变量）'}
          >
            <Input.Password placeholder="sk-..." />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
