import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import {
  createAssistant,
  deleteAssistant,
  fetchAssistants,
  fetchKbs,
  updateAssistant,
  type Assistant,
  type KnowledgeBase,
  type Me,
} from '../api';
import AvatarPicker from '../components/AvatarPicker';
import UserAvatar from '../components/UserAvatar';

const { Text, Paragraph } = Typography;

/* ------------------------------------------------------------------ */
/*  头像预设：emoji 直接渲染，颜色 token 作为 Avatar 背景色              */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/*  表单值类型                                                          */
/* ------------------------------------------------------------------ */

interface AssistantFormValues {
  name: string;
  avatar: string;
  system_prompt: string;
  kb_ids: string[];
  temperature?: number;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Assistants({ me }: { me: Me }) {
  const { message: msgApi } = App.useApp();

  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Assistant | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<AssistantFormValues>();

  const kbNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const kb of kbs) m.set(kb.id, kb.name);
    return m;
  }, [kbs]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, kbList] = await Promise.all([fetchAssistants(), fetchKbs()]);
      setAssistants(list);
      setKbs(kbList);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  /* ---- Modal open helpers ---- */

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ avatar: '🤖', kb_ids: [], system_prompt: '' });
    setModalOpen(true);
  };

  const openEdit = (a: Assistant) => {
    setEditing(a);
    form.resetFields();
    const temperature = typeof a.model_config?.temperature === 'number'
      ? (a.model_config.temperature as number)
      : undefined;
    form.setFieldsValue({
      name: a.name,
      avatar: a.avatar || '🤖',
      system_prompt: a.system_prompt,
      kb_ids: a.kb_ids,
      temperature,
    });
    setModalOpen(true);
  };

  /* ---- Submit ---- */

  const handleSubmit = async () => {
    let values: AssistantFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      const modelConfig: Record<string, unknown> = editing
        ? { ...editing.model_config }
        : {};
      if (typeof values.temperature === 'number') {
        modelConfig.temperature = values.temperature;
      }

      if (editing) {
        await updateAssistant(editing.id, {
          name: editing.id === 'default' ? editing.name : values.name,
          avatar: values.avatar,
          system_prompt: values.system_prompt || '',
          kb_ids: values.kb_ids || [],
          model_config: modelConfig,
        });
        msgApi.success('助手已更新');
      } else {
        await createAssistant({
          name: values.name,
          avatar: values.avatar,
          system_prompt: values.system_prompt || '',
          kb_ids: values.kb_ids || [],
          model_config: modelConfig,
        });
        msgApi.success('助手已创建');
      }
      setModalOpen(false);
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  /* ---- Delete ---- */

  const handleDelete = async (a: Assistant) => {
    try {
      await deleteAssistant(a.id);
      msgApi.success('助手已删除');
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  /* ---- Render ---- */

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
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>助手管理</h1>
          <Text type="secondary">当前账号：{me.user}</Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建助手
        </Button>
      </div>

      {loading ? (
        <Spin style={{ display: 'block', margin: '80px auto' }} size="large" />
      ) : error ? (
        <Card>
          <Text type="danger">加载失败：{error}</Text>
          <Button style={{ marginLeft: 12 }} onClick={load}>重试</Button>
        </Card>
      ) : assistants.length === 0 ? (
        <Empty description="暂无助手，点击右上角新建" />
      ) : (
        <Row gutter={[16, 16]}>
          {assistants.map((a) => {
            const isDefault = a.id === 'default';
            return (
              <Col xs={24} sm={12} lg={8} key={a.id}>
                <Card
                  hoverable
                  actions={[
                    <Button
                      key="edit"
                      type="link"
                      icon={<EditOutlined />}
                      onClick={() => openEdit(a)}
                    >
                      编辑
                    </Button>,
                    isDefault ? (
                      <Text key="del" type="secondary" style={{ fontSize: 12 }}>
                        默认助手不可删除
                      </Text>
                    ) : (
                      <Popconfirm
                        key="del"
                        title="删除助手"
                        description={`确定删除「${a.name}」吗？相关会话将不再可用。`}
                        okText="删除"
                        okButtonProps={{ danger: true }}
                        cancelText="取消"
                        onConfirm={() => handleDelete(a)}
                      >
                        <Button type="link" danger icon={<DeleteOutlined />}>
                          删除
                        </Button>
                      </Popconfirm>
                    ),
                  ]}
                >
                  <Space align="start" style={{ width: '100%' }}>
                    <UserAvatar avatar={a.avatar} name={a.name} size={48} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Space>
                        <Text strong style={{ fontSize: 16 }}>{a.name}</Text>
                        {isDefault && <Tag color="blue">默认</Tag>}
                      </Space>
                      <div style={{ marginTop: 6 }}>
                        {a.kb_ids.length === 0 ? (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            未绑定知识库
                          </Text>
                        ) : (
                          a.kb_ids.map((id) => (
                            <Tag key={id} icon={<DatabaseOutlined />} style={{ marginBottom: 4 }}>
                              {kbNameMap.get(id) || id}
                            </Tag>
                          ))
                        )}
                      </div>
                      <Paragraph
                        type="secondary"
                        ellipsis={{ rows: 2 }}
                        style={{ marginTop: 8, marginBottom: 0, fontSize: 13 }}
                      >
                        {a.system_prompt || '（暂无系统提示词）'}
                      </Paragraph>
                    </div>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      {/* ---- Create / Edit Modal ---- */}
      <Modal
        title={editing ? `编辑助手 — ${editing.name}` : '新建助手'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入助手名称' }]}
          >
            <Input
              placeholder="例如：文档问答助手"
              disabled={editing?.id === 'default'}
              maxLength={50}
            />
          </Form.Item>

          <Form.Item
            name="avatar"
            label="头像"
            rules={[{ required: true, message: '请选择头像' }]}
          >
            <AvatarPicker username={editing?.name || 'assistant'} />
          </Form.Item>

          <Form.Item name="system_prompt" label="系统提示词">
            <Input.TextArea
              rows={4}
              placeholder="描述助手的角色与回答风格，例如：你是一名专业的文档问答助手…"
              maxLength={2000}
              showCount
            />
          </Form.Item>

          <Form.Item name="kb_ids" label="绑定知识库">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择要绑定的知识库"
              options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))}
              notFoundContent="暂无知识库"
            />
          </Form.Item>

          <Form.Item
            name="temperature"
            label="Temperature（可选）"
            tooltip="取值 0-2，越低越严谨，越高越发散"
          >
            <InputNumber min={0} max={2} step={0.1} style={{ width: 140 }} placeholder="默认" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
