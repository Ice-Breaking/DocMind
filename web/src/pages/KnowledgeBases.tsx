import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  type ContentHit,
  type KbDoc,
  type KnowledgeBase,
} from '../api';
import { DocumentPreviewModal } from '../components/DocumentPreviewModal';

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
  const qc = useQueryClient();

  /* ---- 新建知识库 Modal ---- */
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<{ name: string; description: string }>();

  /* ---- 知识库详情 Drawer（文档 + 入库任务双 Tab） ---- */
  const [activeKb, setActiveKb] = useState<KnowledgeBase | null>(null);
  const [drawerTab, setDrawerTab] = useState('docs');
  // 内容搜索为一次性动作（raw fetch 不走缓存），loading 用本地 state
  const [contentSearching, setContentSearching] = useState(false);
  const [contentResults, setContentResults] = useState<{ kw: string; results: ContentHit[] } | null>(null);

  /* ---- 文档预览 Modal ---- */
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewFilename, setPreviewFilename] = useState('');

  /* ---- 知识库列表：本页主数据，失败展示错误并支持重试 ---- */
  const kbsQ = useQuery<KnowledgeBase[], Error>({
    queryKey: ['kbs'],
    queryFn: () => fetchKbs(),
  });
  const kbs = kbsQ.data ?? [];
  const loading = kbsQ.isPending;
  const error = kbsQ.isError ? (kbsQ.error?.message || '加载失败') : null;

  /* ---- Drawer 内文档列表 / 入库任务 ---- */
  const kbId = activeKb?.id ?? '';
  const docsQ = useQuery<KbDoc[], Error>({
    queryKey: ['kbDocs', kbId],
    queryFn: () => fetchKbDocs(kbId),
    enabled: !!activeKb,
  });
  // 任务列表：有进行中任务时每 3 秒轮询，全部结束后自动停止
  const tasksQ = useQuery<IngestTask[], Error>({
    queryKey: ['ingestTasks', kbId],
    queryFn: () => fetchIngestTasks(kbId),
    enabled: !!activeKb,
    retry: false,
    refetchInterval: (q) =>
      q.state.data?.some((t) => t.status === 'running' || t.status === 'pending') ? 3000 : false,
  });

  const docs = docsQ.data ?? [];
  const tasks = tasksQ.data ?? [];

  // 文档列表加载失败提示（对齐旧 loadDocs 的 toast；任务列表失败保持静默）
  useEffect(() => {
    if (docsQ.isError) msgApi.error(docsQ.error?.message || '文档列表加载失败');
  }, [docsQ.isError, docsQ.error, msgApi]);

  // 轮询从「有任务在跑」转为「全部结束」时，刷新文档统计与文档列表（对齐旧轮询收尾逻辑）
  const hadRunningRef = useRef(false);
  useEffect(() => {
    const list = tasksQ.data;
    if (!list?.length) return;
    const running = list.some((t) => t.status === 'running' || t.status === 'pending');
    if (running) {
      hadRunningRef.current = true;
    } else if (hadRunningRef.current) {
      hadRunningRef.current = false;
      qc.invalidateQueries({ queryKey: ['kbs'] });
      qc.invalidateQueries({ queryKey: ['kbDocs', kbId] });
    }
  }, [tasksQ.data, qc, kbId]);

  /** 打开抽屉即触发两条 query 拉取 */
  const openKb = (kb: KnowledgeBase, tab = 'docs') => {
    setContentResults(null);
    setActiveKb(kb);
    setDrawerTab(tab);
  };

  const closeDrawer = () => {
    setActiveKb(null);
  };

  /* ---- 上传文档（Upload 手动请求） ---- */

  const uploadMut = useMutation({
    mutationFn: ({ kbId: id, file }: { kbId: string; file: File }) => uploadKbDoc(id, file),
    onSuccess: (_d, v) => {
      msgApi.success(`已上传 ${v.file.name}，重建索引后生效`);
      qc.invalidateQueries({ queryKey: ['kbs'] });
      qc.invalidateQueries({ queryKey: ['kbDocs', v.kbId] });
      qc.invalidateQueries({ queryKey: ['ingestTasks', v.kbId] });
    },
    onError: (e: Error, v) => {
      msgApi.error(`${v.file.name} 上传失败：${e.message || '上传失败'}`);
    },
  });

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: true,
    showUploadList: false,
    accept: '.pdf,.md,.txt,.docx,.csv,.json',
    customRequest: ({ file, onSuccess, onError }) => {
      if (!activeKb) return;
      uploadMut.mutate(
        { kbId: activeKb.id, file: file as File },
        {
          onSuccess: () => onSuccess?.({}, new XMLHttpRequest()),
          onError: (err) => onError?.(err),
        },
      );
    },
  };

  /* ---- 删除文档 ---- */

  const deleteDocMut = useMutation({
    mutationFn: (name: string) => deleteKbDoc(kbId, name),
    onSuccess: (_d, name) => {
      msgApi.success(`已删除 ${name}，重建索引后从检索移除`);
      qc.invalidateQueries({ queryKey: ['kbs'] });
      qc.invalidateQueries({ queryKey: ['kbDocs', kbId] });
      qc.invalidateQueries({ queryKey: ['ingestTasks', kbId] });
    },
    onError: (e: Error) => msgApi.error(e.message || '删除失败'),
  });

  /* ---- 重建索引（异步任务） ---- */

  const reindexMut = useMutation({
    mutationFn: (kb: KnowledgeBase) => reindexKb(kb.id),
    onSuccess: (r, kb) => {
      msgApi.success(`索引重建已启动（任务 #${r.task_id ?? '-'}），可在入库任务中查看进度`);
      // 打开该库抽屉的任务 Tab 并轮询进度（invalidate 后 refetchInterval 自动接管）
      setActiveKb(kb);
      setDrawerTab('tasks');
      qc.invalidateQueries({ queryKey: ['ingestTasks', kb.id] });
    },
    onError: (e: Error) => msgApi.error(e.message || '重建索引失败'),
  });

  /* ---- 创建 / 删除知识库 ---- */

  const createMut = useMutation({
    mutationFn: (values: { name: string; description: string }) =>
      createKb({ name: values.name, description: values.description || '' }),
    onSuccess: () => {
      msgApi.success('知识库已创建');
      setCreateOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['kbs'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '创建失败'),
  });

  const deleteKbMut = useMutation({
    mutationFn: (kb: KnowledgeBase) => deleteKb(kb.id),
    onSuccess: () => {
      msgApi.success('知识库已删除');
      qc.invalidateQueries({ queryKey: ['kbs'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '删除失败'),
  });

  const handleCreate = async () => {
    let values: { name: string; description: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    createMut.mutate(values);
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
            icon={<SyncOutlined spin={reindexMut.isPending && reindexMut.variables?.id === kb.id} />}
            loading={reindexMut.isPending && reindexMut.variables?.id === kb.id}
            onClick={() => reindexMut.mutate(kb)}
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
              onConfirm={() => deleteKbMut.mutate(kb)}
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
          <Button style={{ marginLeft: 12 }} onClick={() => kbsQ.refetch()}>重试</Button>
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
        confirmLoading={createMut.isPending}
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
                        onClick={() => docsQ.refetch()}
                      >
                        刷新
                      </Button>
                    </div>

                    {/* 内容搜索：哪份文档提到 XX（基于索引切片，不逐个点开） */}
                    <Input.Search
                      placeholder="搜索文档内容（如：部署端口）"
                      allowClear
                      loading={contentSearching}
                      enterButton
                      onSearch={async (v: string) => {
                        const kw = v.trim();
                        setContentResults(null);
                        if (kw.length < 2) return;
                        setContentSearching(true);
                        try {
                          const r = await fetch(
                            `/api/kbs/${activeKb.id}/docs/search?q=${encodeURIComponent(kw)}`,
                          );
                          const d = await r.json();
                          if (!r.ok) throw new Error(d.detail || '搜索失败');
                          setContentResults({ kw, results: d as ContentHit[] });
                        } catch (e) {
                          msgApi.error(e instanceof Error ? e.message : '搜索失败');
                        } finally {
                          setContentSearching(false);
                        }
                      }}
                    />
                    {contentResults && (
                      <div style={{ background: 'rgba(99,102,241,0.05)', borderRadius: 8, padding: 12 }}>
                        <Text strong>
                          内容「{contentResults.kw}」命中 {contentResults.results.length} 个文档
                        </Text>
                        {contentResults.results.length === 0 ? (
                          <div style={{ marginTop: 6 }}><Text type="secondary">无命中</Text></div>
                        ) : (
                          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                            {contentResults.results.map((h) => (
                              <li key={h.name} style={{ marginBottom: 6 }}>
                                <Text strong>{h.name}</Text>
                                <Text type="secondary">（{h.count} 处）</Text>
                                {h.snippets.map((s, i) => (
                                  <div key={i} style={{ fontSize: 12, color: '#666' }}>{s}</div>
                                ))}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}

                    {docsQ.isFetching ? (
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
                                onConfirm={() => deleteDocMut.mutate(d.name)}
                              >
                                <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                              </Popconfirm>,
                            ]}
                          >
                            <List.Item.Meta
                              avatar={<FileTextOutlined style={{ fontSize: 18, color: '#1677ff' }} />}
                              title={
                                <a
                                  onClick={() => {
                                    setPreviewFilename(d.name);
                                    setPreviewVisible(true);
                                  }}
                                  style={{ cursor: 'pointer', color: '#1677ff', fontSize: 13 }}
                                >
                                  {d.name}
                                </a>
                              }
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
                        onClick={() => tasksQ.refetch()}
                      >
                        刷新
                      </Button>
                    </div>
                    {tasksQ.isFetching && tasks.length === 0 ? (
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
                          loading={reindexMut.isPending && reindexMut.variables?.id === activeKb.id}
                          onClick={() => reindexMut.mutate(activeKb)}
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

      {/* 文档预览 Modal */}
      {activeKb && (
        <DocumentPreviewModal
          visible={previewVisible}
          onClose={() => setPreviewVisible(false)}
          kbId={activeKb.id}
          filename={previewFilename}
        />
      )}
    </div>
  );
}
