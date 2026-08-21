import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { UploadProps } from 'antd';
import {
  createKb,
  deleteKb,
  deleteKbDoc,
  fetchIngestTasks,
  fetchKbDocs,
  fetchKbs,
  reindexKb,
  uploadKbDoc,
  type IngestTask,
  type KbDoc,
  type KnowledgeBase,
} from '../api';

const { Text } = Typography;

/** 字节数 → 人类可读大小 */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 秒级时间戳 → 本地可读时间 */
function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

const TASK_STATUS_CFG: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '待生效' },
  running: { color: 'processing', label: '进行中' },
  done: { color: 'success', label: '完成' },
  error: { color: 'error', label: '失败' },
};

const TASK_MODE_LABEL: Record<string, string> = {
  upload: '上传',
  delete: '删除',
  reindex: '重建索引',
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function KnowledgeBases() {
  const { message: msgApi } = App.useApp();

  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /* ---- 新建知识库 Modal ---- */
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<{ name: string; description: string }>();

  /* ---- 知识库详情 Drawer（文档 + 入库任务双 Tab） ---- */
  const [activeKb, setActiveKb] = useState<KnowledgeBase | null>(null);
  const [drawerTab, setDrawerTab] = useState('docs');
  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [tasks, setTasks] = useState<IngestTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [reindexing, setReindexing] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setKbs(await fetchKbs());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  /* ---- 文档 / 任务列表 ---- */

  const loadDocs = useCallback(async (kbId: string) => {
    setDocsLoading(true);
    try {
      setDocs(await fetchKbDocs(kbId));
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '文档列表加载失败');
    } finally {
      setDocsLoading(false);
    }
  }, [msgApi]);

  const loadTasks = useCallback(async (kbId: string) => {
    setTasksLoading(true);
    try {
      setTasks(await fetchIngestTasks(kbId));
    } catch {
      // 任务列表加载失败不打扰用户
    } finally {
      setTasksLoading(false);
    }
  }, []);

  const openKb = async (kb: KnowledgeBase, tab = 'docs') => {
    setActiveKb(kb);
    setDrawerTab(tab);
    await Promise.all([loadDocs(kb.id), loadTasks(kb.id)]);
  };

  const closeDrawer = () => {
    setActiveKb(null);
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  /** 有进行中任务时每 3 秒轮询，全部结束后自动停止 */
  const startPolling = useCallback((kbId: string) => {
    if (pollRef.current) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const list = await fetchIngestTasks(kbId);
        setTasks(list);
        if (!list.some((t) => t.status === 'running')) {
          if (pollRef.current) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
          }
          load();   // 重建完成后刷新文档统计
        }
      } catch {
        // 轮询失败静默，下一轮再试
      }
    }, 3000);
  }, [load]);

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
  }, []);

  /* ---- 上传（Upload 手动请求） ---- */

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: true,
    showUploadList: false,
    accept: '.pdf,.md,.txt,.docx,.csv,.json',
    customRequest: async ({ file, onSuccess, onError }) => {
      if (!activeKb) return;
      try {
        await uploadKbDoc(activeKb.id, file as File);
        msgApi.success(`已上传 ${(file as File).name}，重建索引后生效`);
        onSuccess?.({}, new XMLHttpRequest());
        await Promise.all([loadDocs(activeKb.id), loadTasks(activeKb.id), load()]);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : '上传失败';
        msgApi.error(`${(file as File).name} 上传失败：${msg}`);
        onError?.(new Error(msg));
      }
    },
  };

  /* ---- 删除文档 ---- */

  const handleDeleteDoc = async (name: string) => {
    if (!activeKb) return;
    try {
      await deleteKbDoc(activeKb.id, name);
      msgApi.success(`已删除 ${name}，重建索引后从检索移除`);
      await Promise.all([loadDocs(activeKb.id), loadTasks(activeKb.id), load()]);
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  /* ---- 重建索引（异步任务） ---- */

  const handleReindex = async (kb: KnowledgeBase) => {
    setReindexing(kb.id);
    try {
      const r = await reindexKb(kb.id);
      msgApi.success(`索引重建已启动（任务 #${r.task_id ?? '-'}），可在入库任务中查看进度`);
      // 打开该库抽屉的任务 Tab 并轮询进度
      setActiveKb(kb);
      setDrawerTab('tasks');
      await loadTasks(kb.id);
      startPolling(kb.id);
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '重建索引失败');
    } finally {
      setReindexing(null);
    }
  };

  /* ---- 创建 / 删除知识库 ---- */

  const handleCreate = async () => {
    let values: { name: string; description: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      await createKb({ name: values.name, description: values.description || '' });
      msgApi.success('知识库已创建');
      setCreateOpen(false);
      form.resetFields();
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteKb = async (kb: KnowledgeBase) => {
    try {
      await deleteKb(kb.id);
      msgApi.success('知识库已删除');
      await load();
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  /* ---- 表格列 ---- */

  const columns: ColumnsType<KnowledgeBase> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      render: (name: string, kb) => (
        <Space>
          <DatabaseOutlined style={{ color: '#1677ff' }} />
          <Text strong>{name}</Text>
          {kb.id === 'default' && <Tag color="blue">默认</Tag>}
        </Space>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (v: string) => v || <Text type="secondary">（暂无描述）</Text>,
    },
    {
      title: '文档数',
      dataIndex: 'doc_count',
      key: 'doc_count',
      width: 90,
      render: (v?: number) => v ?? 0,
    },
    {
      title: '占用空间',
      dataIndex: 'doc_size',
      key: 'doc_size',
      width: 110,
      render: (v?: number) => formatSize(v ?? 0),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (ts: number) => formatTime(ts),
    },
    {
      title: '操作',
      key: 'action',
      width: 260,
      render: (_: any, kb) => (
        <Space>
          <Button size="small" icon={<FolderOpenOutlined />} onClick={() => openKb(kb)}>
            管理
          </Button>
          <Button
            size="small"
            icon={<SyncOutlined spin={reindexing === kb.id} />}
            loading={reindexing === kb.id}
            onClick={() => handleReindex(kb)}
          >
            重建索引
          </Button>
          {kb.id === 'default' ? (
            <Text type="secondary" style={{ fontSize: 12 }}>不可删除</Text>
          ) : (
            <Popconfirm
              title="删除知识库"
              description={`确定删除「${kb.name}」吗？文档与索引将一并删除。`}
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => handleDeleteKb(kb)}
            >
              <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  /* ---- 入库任务表格 ---- */

  const taskColumns: ColumnsType<IngestTask> = [
    { title: '#', dataIndex: 'id', key: 'id', width: 55 },
    {
      title: '类型',
      dataIndex: 'mode',
      key: 'mode',
      width: 90,
      render: (v: string) => TASK_MODE_LABEL[v] || v,
    },
    {
      title: '文件',
      dataIndex: 'filename',
      key: 'filename',
      ellipsis: true,
      render: (v: string) => (v === '*' ? <Text type="secondary">全部</Text> : v),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v: string) => {
        const cfg = TASK_STATUS_CFG[v] || { color: 'default', label: v };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '说明',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (v: string, t) =>
        t.status === 'error' ? <Text type="danger">{v}</Text> : (v || '—'),
    },
    {
      title: '时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 150,
      render: (ts: number) => formatTime(ts),
    },
  ];

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
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>知识库管理</h1>
          <Text type="secondary">上传文档构建知识库，供助手检索问答使用</Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => { form.resetFields(); setCreateOpen(true); }}
        >
          新建知识库
        </Button>
      </div>

      {loading ? (
        <Spin style={{ display: 'block', margin: '80px auto' }} size="large" />
      ) : error ? (
        <Card>
          <Text type="danger">加载失败：{error}</Text>
          <Button style={{ marginLeft: 12 }} onClick={load}>重试</Button>
        </Card>
      ) : (
        <Table scroll={{ x: "max-content" }}
          columns={columns}
          dataSource={kbs}
          rowKey="id"
          pagination={false}
          size="middle"
        />
      )}

      {/* ---- 新建知识库 Modal ---- */}
      <Modal
        title="新建知识库"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input placeholder="例如：产品文档库" maxLength={50} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="简要说明该知识库的用途（可选）" maxLength={200} />
          </Form.Item>
        </Form>
      </Modal>

      {/* ---- 知识库详情 Drawer ---- */}
      <Drawer
        title={
          <Space>
            <DatabaseOutlined />
            <span>{activeKb?.name} — 知识库详情</span>
          </Space>
        }
        placement="right"
        width={620}
        open={!!activeKb}
        onClose={closeDrawer}
      >
        {activeKb && (
          <Tabs
            activeKey={drawerTab}
            onChange={setDrawerTab}
            items={[
              {
                key: 'docs',
                label: `资料（${docs.length}）`,
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size="middle">
                    <Upload.Dragger {...uploadProps}>
                      <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                      <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
                      <p className="ant-upload-hint">
                        支持 pdf / md / txt / docx / csv / json，单文件不超过 50 MB；
                        上传后点击列表页「重建索引」使新文档生效
                      </p>
                    </Upload.Dragger>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text type="secondary">共 {docs.length} 个文档</Text>
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => loadDocs(activeKb.id)}
                      >
                        刷新
                      </Button>
                    </div>

                    {docsLoading ? (
                      <Spin style={{ display: 'block', margin: '40px auto' }} />
                    ) : docs.length === 0 ? (
                      <Empty description="暂无文档" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                      <List
                        dataSource={docs}
                        rowKey="name"
                        renderItem={(d: KbDoc) => (
                          <List.Item
                            actions={[
                              <Popconfirm
                                key="del"
                                title={`删除文档「${d.name}」？`}
                                description="删除后需重建索引才会从检索中移除"
                                okText="删除"
                                okButtonProps={{ danger: true }}
                                cancelText="取消"
                                onConfirm={() => handleDeleteDoc(d.name)}
                              >
                                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                              </Popconfirm>,
                            ]}
                          >
                            <List.Item.Meta
                              avatar={<FileTextOutlined style={{ fontSize: 18, color: '#1677ff' }} />}
                              title={<Text style={{ fontSize: 13 }}>{d.name}</Text>}
                              description={`${formatSize(d.size)} · 更新于 ${new Date(d.modified).toLocaleString()}`}
                            />
                          </List.Item>
                        )}
                      />
                    )}
                  </Space>
                ),
              },
              {
                key: 'tasks',
                label: `入库任务（${tasks.length}）`,
                children: (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                      <Text type="secondary">上传/删除/重建的执行记录；「待生效」需重建索引后进入检索</Text>
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => loadTasks(activeKb.id)}
                      >
                        刷新
                      </Button>
                    </div>
                    {tasksLoading && tasks.length === 0 ? (
                      <Spin style={{ display: 'block', margin: '40px auto' }} />
                    ) : tasks.length === 0 ? (
                      <Empty description="暂无任务记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                      <Table scroll={{ x: "max-content" }}
                        columns={taskColumns}
                        dataSource={tasks}
                        rowKey="id"
                        pagination={{ pageSize: 8, size: 'small' }}
                        size="small"
                      />
                    )}
                    {tasks.some((t) => t.status === 'error') && (
                      <div style={{ marginTop: 12 }}>
                        <Button
                          type="primary"
                          icon={<SyncOutlined />}
                          loading={reindexing === activeKb.id}
                          onClick={() => handleReindex(activeKb)}
                        >
                          重试：重新重建索引
                        </Button>
                      </div>
                    )}
                  </>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  );
}
