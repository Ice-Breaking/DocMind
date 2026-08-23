import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  CheckOutlined,
  CrownOutlined,
  DeleteOutlined,
  KeyOutlined,
  PlusOutlined,
  StopOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import UserAvatar from '../components/UserAvatar';
import {
  createUser,
  deleteUser,
  fetchAvatarReviews,
  fetchUsers,
  resetUserPassword,
  reviewAvatar,
  setUserAdmin,
  type AdminUser,
  type Me,
  type PendingAvatarReview,
} from '../api';

const { Text } = Typography;

/**
 * 用户管理（仅管理员）：新增账号、重置密码、授予/收回管理员、删除账号、
 * 头像上传人工审核队列。安全约束由后端保证。
 */
export default function Users({ me }: { me: Me }) {
  const { message: msgApi, modal: modalApi } = App.useApp();
  const queryClient = useQueryClient();

  /* ---- 新建用户 Modal ---- */
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<{ username: string; password: string; is_admin: boolean }>();

  /* ---- 重置密码 Modal ---- */
  const [pwdTarget, setPwdTarget] = useState<AdminUser | null>(null);
  const [pwdText, setPwdText] = useState('');

  /* ---- 初始密码一次性展示 Modal ---- */
  const [credModal, setCredModal] = useState<{ username: string; password: string } | null>(null);

  // 用户清单 + 头像审核队列：聚合 loading，任一失败弹 toast（保留旧数据）
  const usersQ = useQuery<AdminUser[], Error>({
    queryKey: ['users'],
    queryFn: () => fetchUsers(),
  });
  const reviewsQ = useQuery<PendingAvatarReview[], Error>({
    queryKey: ['avatarReviews'],
    queryFn: () => fetchAvatarReviews(),
  });

  const users = usersQ.data ?? [];
  const reviews = reviewsQ.data ?? [];
  const loading = usersQ.isPending || reviewsQ.isPending;

  useEffect(() => {
    if (usersQ.error) msgApi.error(usersQ.error.message || '加载失败');
  }, [usersQ.error, msgApi]);

  /* ---- 头像审核 ---- */
  const reviewMut = useMutation({
    mutationFn: (v: { username: string; action: 'approve' | 'reject' }) =>
      reviewAvatar(v.username, v.action),
    onSuccess: (_d, v) => {
      msgApi.success(v.action === 'approve' ? `已通过 ${v.username} 的头像` : `已驳回 ${v.username} 的头像`);
      queryClient.invalidateQueries({ queryKey: ['avatarReviews'] });
      queryClient.invalidateQueries({ queryKey: ['users'] }); // pending_avatar 标记随之更新
    },
    onError: (e: Error) => msgApi.error(e.message || '操作失败'),
  });

  /* ---- 新建：成功后弹初始密码一次性展示 ---- */
  const createMut = useMutation({
    mutationFn: (values: { username: string; password: string; is_admin: boolean }) =>
      createUser({
        username: values.username,
        password: values.password,
        is_admin: values.is_admin ?? false,
      }),
    onSuccess: (_d, values) => {
      msgApi.success(`用户 ${values.username} 已创建，首次登录须修改密码`);
      setCredModal({ username: values.username, password: values.password });
      setCreateOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '创建失败'),
  });

  const handleCreate = async () => {
    let values: { username: string; password: string; is_admin: boolean };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    createMut.mutate(values);
  };

  /* ---- 重置密码：不改变列表字段，无需失效缓存（对齐旧行为） ---- */
  const resetPwdMut = useMutation({
    mutationFn: (v: { username: string; password: string }) =>
      resetUserPassword(v.username, v.password),
    onSuccess: (_d, v) => {
      msgApi.success(`${v.username} 密码已重置，下次登录须修改`);
      setPwdTarget(null);
      setPwdText('');
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '重置失败'),
  });

  const handleResetPwd = async () => {
    if (!pwdTarget) return;
    if (pwdText.length < 8) {
      msgApi.warning('新密码至少 8 个字符');
      return;
    }
    resetPwdMut.mutate({ username: pwdTarget.username, password: pwdText });
  };

  /* ---- 管理员开关 / 删除 ---- */
  const toggleAdminMut = useMutation({
    mutationFn: (v: { u: AdminUser; grant: boolean }) =>
      setUserAdmin(v.u.username, v.grant),
    onSuccess: (_d, v) => {
      msgApi.success(v.grant ? `${v.u.username} 已授予管理员` : `${v.u.username} 已收回管理员`);
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '操作失败'),
  });

  const deleteMut = useMutation({
    mutationFn: (u: AdminUser) => deleteUser(u.username),
    onSuccess: (r, u) => {
      modalApi.success({
        title: `${u.username} 已删除`,
        content: `级联清理：会话 ${r.deleted?.sessions ?? 0} 个 / 消息 ${r.deleted?.messages ?? 0} 条`,
      });
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '删除失败'),
  });

  const columns: ColumnsType<AdminUser> = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      width: 200,
      render: (v: string, u: any) => (
        <Space>
          <UserAvatar avatar={u.avatar} name={v} size={28} />
          <Text strong>{v}</Text>
          {v === me.user && <Tag>当前登录</Tag>}
        </Space>
      ),
    },
    {
      title: '角色',
      dataIndex: 'is_admin',
      key: 'is_admin',
      width: 150,
      render: (v: number, u) => (
        <Space>
          {v === 1
            ? <Tag color="gold" icon={<CrownOutlined />}>管理员</Tag>
            : <Tag>普通用户</Tag>}
          {u.username !== me.user && (
            <Switch
              size="small"
              checked={v === 1}
              onChange={(checked) => toggleAdminMut.mutate({ u, grant: checked })}
            />
          )}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 130,
      render: (_: any, u: any) => (
        <Space direction="vertical" size={2}>
          {u.must_change_pwd === 1 ? <Tag color="orange">待改密</Tag> : <Tag color="green">正常</Tag>}
          {u.pending_avatar ? <Tag color="blue">头像审核中</Tag> : null}
        </Space>
      ),
    },
    { title: '会话数', dataIndex: 'sessions', key: 'sessions', width: 80 },
    { title: '消息数', dataIndex: 'messages', key: 'messages', width: 80 },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (ts: number) => (ts ? new Date(ts * 1000).toLocaleString() : '—'),
    },
    {
      title: '操作',
      key: 'action',
      width: 190,
      render: (_: any, u) => (
        <Space>
          <Button
            size="small"
            icon={<KeyOutlined />}
            onClick={() => { setPwdTarget(u); setPwdText(''); }}
          >
            重置密码
          </Button>
          {u.username !== me.user && (
            <Popconfirm
              title={`删除用户「${u.username}」？`}
              description="将级联删除其全部会话与消息，不可恢复。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => deleteMut.mutate(u)}
            >
              <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          )}
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
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>用户管理</h1>
          <Text type="secondary">新增账号、重置密码、授予管理员、删除账号、头像审核（均写入审计日志）</Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => { form.resetFields(); setCreateOpen(true); }}
        >
          新增用户
        </Button>
      </div>

      {/* ---- 头像审核队列 ---- */}
      {reviews.length > 0 && (
        <Card size="small" title={`头像审核（${reviews.length} 条待处理）`} style={{ marginBottom: 16 }}>
          <List
            dataSource={reviews}
            renderItem={(r) => (
              <List.Item
                actions={[
                  <Button
                    key="ok"
                    size="small"
                    type="primary"
                    icon={<CheckOutlined />}
                    onClick={() => reviewMut.mutate({ username: r.username, action: 'approve' })}
                  >
                    通过
                  </Button>,
                  <Button
                    key="no"
                    size="small"
                    danger
                    icon={<StopOutlined />}
                    onClick={() => reviewMut.mutate({ username: r.username, action: 'reject' })}
                  >
                    驳回
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  avatar={<UserAvatar avatar={`file:${r.pending_avatar}`} name={r.username} size={44} />}
                  title={r.username}
                  description={`上传于 ${new Date(r.pending_avatar_at * 1000).toLocaleString()} · 审核通过前展示旧头像`}
                />
              </List.Item>
            )}
          />
        </Card>
      )}

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="密码以 PBKDF2 加盐哈希存储，任何人（含管理员）无法查看明文；忘记密码用「重置密码」。自定义头像需人工审核通过后生效。"
      />

      <Table
        columns={columns}
        dataSource={users}
        rowKey="username"
        loading={loading}
        pagination={false}
        size="middle"
        scroll={{ x: 'max-content' }}
      />

      {/* ---- 新建用户 Modal ---- */}
      <Modal
        title="新增用户"
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
            name="username"
            label="用户名"
            rules={[
              { required: true, message: '请输入用户名' },
              { pattern: /^[\w.@-]{2,64}$/, message: '仅支持字母/数字/._@-，长度 2-64' },
            ]}
          >
            <Input placeholder="例如：zhangsan 或 zhangsan@company.com" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: '请输入初始密码' },
              { min: 8, message: '至少 8 个字符' },
            ]}
          >
            <Input.Password placeholder="用户首次登录将被强制修改" />
          </Form.Item>
          <Form.Item name="is_admin" label="管理员" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* ---- 初始密码一次性展示 ---- */}
      <Modal
        title="账号创建成功"
        open={!!credModal}
        onCancel={() => setCredModal(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setCredModal(null)}>
            我已保存
          </Button>,
        ]}
        closable={false}
        maskClosable={false}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="初始密码仅本次展示，请妥善保存或线下告知用户；之后无法找回，只能重置。"
        />
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            用户名：
            <Typography.Text code copyable>{credModal?.username}</Typography.Text>
          </div>
          <div>
            初始密码：
            <Typography.Text code copyable>{credModal?.password}</Typography.Text>
          </div>
        </Space>
      </Modal>

      {/* ---- 重置密码 Modal ---- */}
      <Modal
        title={`重置密码 — ${pwdTarget?.username || ''}`}
        open={!!pwdTarget}
        onOk={handleResetPwd}
        onCancel={() => setPwdTarget(null)}
        confirmLoading={resetPwdMut.isPending}
        okText="重置"
        cancelText="取消"
      >
        <Input.Password
          placeholder="新密码（至少 8 位），用户下次登录须修改"
          value={pwdText}
          onChange={(e) => setPwdText(e.target.value)}
        />
      </Modal>
    </div>
  );
}
