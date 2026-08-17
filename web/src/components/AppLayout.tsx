import { useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Avatar, Dropdown, Form, Input, Layout, Menu, Modal, Typography } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  MessageOutlined,
  RobotOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  SettingOutlined,
  LineChartOutlined,
  WarningOutlined,
  FileSearchOutlined,
  ExperimentOutlined,
  SafetyCertificateOutlined,
  KeyOutlined,
  ApiOutlined,
  AuditOutlined,
  AlertOutlined,
  CloudUploadOutlined,
  ToolOutlined,
  LogoutOutlined,
  AppstoreOutlined,
  ReadOutlined,
  ControlOutlined,
  TeamOutlined,
  UserOutlined,
  DownOutlined,
  LockOutlined,
} from '@ant-design/icons';
import { changePassword, logout, type Me } from '../api';

const { Sider, Content } = Layout;

/**
 * 全局侧边导航骨架：可折叠 Sider + Outlet 内容区。
 * 顶部为用户菜单（个人设置 / 修改密码 / 退出登录），常驻可见，
 * 登出走后端 /logout 清除登录 cookie，避免"退出后又自动登录"。
 */
export default function AppLayout({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  /* ---- 修改密码 Modal（用户菜单直达，与个人设置页内容解耦） ---- */
  const [pwdOpen, setPwdOpen] = useState(false);
  const [pwdLoading, setPwdLoading] = useState(false);
  const [pwdForm] = Form.useForm<{ oldPassword: string; newPassword: string; confirmPassword: string }>();

  // 一二级菜单：一级为可折叠分组（SubMenu），二级为页面入口
  const menuItems = useMemo<MenuProps['items']>(() => {
    const items: NonNullable<MenuProps['items']> = [
      {
        key: 'g-work',
        icon: <AppstoreOutlined />,
        label: '工作台',
        children: [
          { key: '/dashboard', icon: <DashboardOutlined />, label: '总览' },
          { key: '/chat', icon: <MessageOutlined />, label: '对话' },
        ],
      },
      {
        key: 'g-kb',
        icon: <ReadOutlined />,
        label: '知识空间',
        children: [
          { key: '/assistants', icon: <RobotOutlined />, label: '助手管理' },
          { key: '/kbs', icon: <DatabaseOutlined />, label: '知识库管理' },
          { key: '/sessions', icon: <HistoryOutlined />, label: '会话历史' },
        ],
      },
    ];
    if (me.is_admin) {
      items.push(
        {
          key: 'g-quality',
          icon: <ExperimentOutlined />,
          label: '检索质量',
          children: [
            { key: '/retrieval-lab', icon: <FileSearchOutlined />, label: '调优实验室' },
            { key: '/eval', icon: <SafetyCertificateOutlined />, label: '评测与质量' },
            { key: '/traces', icon: <HistoryOutlined />, label: '检索日志' },
          ],
        },
        {
          key: 'g-ops',
          icon: <LineChartOutlined />,
          label: '运营监控',
          children: [
            { key: '/usage', icon: <LineChartOutlined />, label: '用量与成本' },
            { key: '/alerts', icon: <AlertOutlined />, label: '告警与 SLA' },
            { key: '/badcases', icon: <WarningOutlined />, label: 'Badcase 管理' },
          ],
        },
        {
          key: 'g-gov',
          icon: <ControlOutlined />,
          label: '系统治理',
          children: [
            { key: '/users', icon: <TeamOutlined />, label: '用户管理' },
            { key: '/queries', icon: <FileSearchOutlined />, label: '提问记录' },
            { key: '/api-keys', icon: <KeyOutlined />, label: 'API Key' },
            { key: '/models', icon: <ApiOutlined />, label: '模型管理' },
            { key: '/audit', icon: <AuditOutlined />, label: '审计中心' },
            { key: '/backups', icon: <CloudUploadOutlined />, label: '备份与恢复' },
            { key: '/admin', icon: <ToolOutlined />, label: '会话审计' },
          ],
        },
      );
    }
    items.push({ key: '/settings', icon: <SettingOutlined />, label: '设置' });
    return items;
  }, [me.is_admin]);

  // 默认展开所有一级分组
  const openKeys = useMemo(
    () => (menuItems ?? [])
      .filter((it) => it && 'children' in it)
      .map((it) => it?.key as string),
    [menuItems],
  );

  // 展平分组菜单取所有叶子 key，用于高亮当前路由
  const allKeys = useMemo(() => {
    const keys: string[] = [];
    for (const item of menuItems ?? []) {
      if (item && 'children' in item && Array.isArray((item as any).children)) {
        for (const child of (item as any).children) keys.push(child.key as string);
      } else if (item?.key) {
        keys.push(item.key as string);
      }
    }
    return keys;
  }, [menuItems]);

  const selectedKey =
    allKeys.find(key => location.pathname === key || location.pathname.startsWith(key + '/')) ??
    '/dashboard';

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    navigate(key);
  };

  // ---- 顶部用户菜单：设置 / 改密 / 登出 ----
  const userMenuItems: MenuProps['items'] = [
    { key: 'profile', icon: <UserOutlined />, label: `当前账号：${me.user}` , disabled: true },
    { type: 'divider' },
    { key: 'settings', icon: <SettingOutlined />, label: '个人设置' },
    { key: 'password', icon: <LockOutlined />, label: '修改密码' },
    { type: 'divider' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ];

  const handleChangePassword = async () => {
    let values: { oldPassword: string; newPassword: string; confirmPassword: string };
    try {
      values = await pwdForm.validateFields();
    } catch {
      return;
    }
    setPwdLoading(true);
    try {
      await changePassword(values.oldPassword, values.newPassword);
      // 改密后服务端会话可能失效，统一回登录页重新登录
      await logout();
      onLogout();
    } catch (e: unknown) {
      // eslint-disable-next-line no-console
      console.warn(e);
      // 错误提示由 antd 静态 message 兜底不便，这里用表单内提示
      pwdForm.setFields([{ name: 'oldPassword', errors: [e instanceof Error ? e.message : '修改失败'] }]);
    } finally {
      setPwdLoading(false);
    }
  };

  const handleUserMenu: MenuProps['onClick'] = async ({ key }) => {
    if (key === 'settings') {
      navigate('/settings');
      return;
    }
    if (key === 'password') {
      pwdForm.resetFields();
      setPwdOpen(true);
      return;
    }
    if (key === 'logout') {
      // 先清服务端登录 cookie，再清前端状态；否则会被 cookie 自动"登回去"
      await logout();
      onLogout();
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme="dark"
        style={{
          display: 'flex',
          flexDirection: 'column',
          position: 'sticky',
          top: 0,
          height: '100vh',
          overflow: 'auto',
        }}
      >
        <div
          style={{
            padding: collapsed ? '16px 8px' : '16px',
            textAlign: 'center',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
          }}
        >
          <Typography.Text strong style={{ color: '#fff', fontSize: collapsed ? 14 : 16 }}>
            {collapsed ? 'DM' : 'DocMind'}
          </Typography.Text>
        </div>

        {/* 用户菜单：常驻顶部，登出不再需要翻到菜单底部 */}
        <div
          style={{
            padding: collapsed ? '12px 0' : '12px 16px',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            justifyContent: 'center',
          }}
        >
          <Dropdown
            menu={{ items: userMenuItems, onClick: handleUserMenu }}
            placement="bottomLeft"
            trigger={['click']}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                cursor: 'pointer',
                width: collapsed ? 'auto' : '100%',
              }}
            >
              <Avatar size={collapsed ? 28 : 34} style={{ backgroundColor: '#6366f1', flex: 'none' }}>
                {me.user.slice(0, 1).toUpperCase()}
              </Avatar>
              {!collapsed && (
                <>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        color: '#fff',
                        fontSize: 13,
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {me.user}
                    </div>
                    <div style={{ color: 'rgba(255,255,255,0.45)', fontSize: 11 }}>
                      {me.is_admin ? '管理员' : '普通用户'}
                    </div>
                  </div>
                  <DownOutlined style={{ color: 'rgba(255,255,255,0.45)', fontSize: 10 }} />
                </>
              )}
            </div>
          </Dropdown>
        </div>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          defaultOpenKeys={openKeys}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ flex: 1, borderRight: 0 }}
        />
      </Sider>
      <Layout>
        {/* 内容区：不加全局 padding/容器，由各页面自管宽度；
            对话页等需要精确占满视口的页面依赖这里的 100vh */}
        <Content
          style={{
            overflow: 'auto',
            height: '100vh',
          }}
        >
          <Outlet />
        </Content>
      </Layout>

      {/* ---- 修改密码 Modal ---- */}
      <Modal
        title="修改密码"
        open={pwdOpen}
        onOk={handleChangePassword}
        onCancel={() => setPwdOpen(false)}
        confirmLoading={pwdLoading}
        okText="确认修改"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={pwdForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="oldPassword"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '新密码至少 8 个字符' },
            ]}
          >
            <Input.Password autoComplete="new-password" placeholder="至少 8 位" />
          </Form.Item>
          <Form.Item
            name="confirmPassword"
            label="确认新密码"
            dependencies={['newPassword']}
            rules={[
              { required: true, message: '请再次输入新密码' },
              ({ getFieldValue }) => ({
                validator(_, value: string) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('两次输入的新密码不一致'));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          修改成功后将退出登录，请使用新密码重新登录。
        </Typography.Text>
      </Modal>
    </Layout>
  );
}
