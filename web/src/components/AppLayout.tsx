import { useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Button, Drawer, Dropdown, Form, Input, Layout, Menu, Modal, Typography } from 'antd';
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
  MenuOutlined,
} from '@ant-design/icons';
import { changePassword, logout, type Me } from '../api';
import UserAvatar from './UserAvatar';

const { Sider, Content } = Layout;

/**
 * 全局导航骨架：
 * - PC（≥768px）：可折叠 Sider + 顶部用户菜单
 * - 移动端（<768px，基线 375×667）：顶部栏 + 抽屉导航
 * 登出走后端 /logout 清除登录 cookie。
 */
export default function AppLayout({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  /* ---- 移动端检测（matchMedia 实时响应） ---- */
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia('(max-width: 767px)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const fn = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener('change', fn);
    return () => mq.removeEventListener('change', fn);
  }, []);
  const [navOpen, setNavOpen] = useState(false);
  /* 对话页沉浸模式：/chat 隐藏全局 Sider（一层布局，导航收进会话头部按钮） */
  const immersive = location.pathname.startsWith('/chat');
  useEffect(() => {
    const open = () => setNavOpen(true);
    window.addEventListener('dm-open-nav', open as EventListener);
    return () => window.removeEventListener('dm-open-nav', open as EventListener);
  }, []);

  /* ---- 修改密码 Modal（用户菜单直达） ---- */
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

  // ---- 用户菜单：设置 / 改密 / 登出 ----
  const userMenuItems: MenuProps['items'] = [
    { key: 'profile', icon: <UserOutlined />, label: `当前账号：${me.user}`, disabled: true },
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
      await logout();
      onLogout();
    } catch (e: unknown) {
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
      await logout();
      onLogout();
    }
  };

  /* ---- 用户头像下拉（PC 侧栏顶部 / 移动顶栏共用） ---- */
  const userDropdown = (
    <Dropdown
      menu={{ items: userMenuItems, onClick: handleUserMenu }}
      placement="bottomLeft"
      trigger={['click']}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
        <span style={{ display: 'inline-flex', cursor: 'pointer' }}>
            <UserAvatar avatar={me.avatar} name={me.user} size={34} />
          </span>
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
      </div>
    </Dropdown>
  );

  /* ---- 修改密码 Modal ---- */
  const pwdModal = (
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
  );

  /* ================= 移动端布局 ================= */
  if (isMobile) {
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <div className="dm-mobile-topbar">
          <Button
            type="text"
            icon={<MenuOutlined />}
            onClick={() => setNavOpen(true)}
            style={{ color: '#fff' }}
          />
          <span style={{ color: '#fff', fontWeight: 600, flex: 1 }}>DocMind</span>
          <Dropdown
            menu={{ items: userMenuItems, onClick: handleUserMenu }}
            placement="bottomRight"
            trigger={['click']}
          >
            <span style={{ display: 'inline-flex', cursor: 'pointer' }}>
            <UserAvatar avatar={me.avatar} name={me.user} size={30} />
          </span>
          </Dropdown>
        </div>
        <Drawer
          placement="left"
          open={navOpen}
          onClose={() => setNavOpen(false)}
          width={280}
          closable={false}
          styles={{ body: { padding: 0, background: '#001529' } }}
        >
          <div style={{ background: '#001529', minHeight: '100%' }}>
            <div
              style={{
                padding: 16,
                color: '#fff',
                fontWeight: 600,
                borderBottom: '1px solid rgba(255,255,255,0.08)',
              }}
            >
              DocMind
            </div>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
              {userDropdown}
            </div>
            <Menu
              theme="dark"
              mode="inline"
              selectedKeys={[selectedKey]}
              defaultOpenKeys={openKeys}
              items={menuItems}
              onClick={(e) => {
                handleMenuClick(e);
                setNavOpen(false);
              }}
              style={{ borderRight: 0 }}
            />
          </div>
        </Drawer>
        <Layout>
          <Content style={{ overflow: 'auto', height: 'calc(100vh - 48px)' }}>
            <Outlet />
          </Content>
        </Layout>
        {pwdModal}
      </Layout>
    );
  }

  /* ================= PC 布局 ================= */
  if (immersive) {
    /* 对话页：全宽沉浸。全局导航内嵌在 Chat 侧栏（buildNavItems 共享），
       ☰ 按钮在侧栏头部切换菜单显隐——会话列表与菜单同处一层抽屉 */
    return (
      <Layout style={{ minHeight: '100vh' }}>
        <Content style={{ overflow: 'hidden', height: '100vh' }}>
          <Outlet />
        </Content>
        {pwdModal}
      </Layout>
    );
  }
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

        <div
          style={{
            padding: collapsed ? '12px 0' : '12px 16px',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
            display: 'flex',
            justifyContent: 'center',
          }}
        >
          {collapsed ? (
            <Dropdown menu={{ items: userMenuItems, onClick: handleUserMenu }} trigger={['click']}>
              <span style={{ display: 'inline-flex', cursor: 'pointer' }}>
            <UserAvatar avatar={me.avatar} name={me.user} size={28} />
          </span>
            </Dropdown>
          ) : (
            userDropdown
          )}
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
        {/* 内容区：不加全局 padding/容器，由各页面自管宽度 */}
        <Content
          style={{
            overflow: 'auto',
            height: '100vh',
          }}
        >
          <Outlet />
        </Content>
      </Layout>
      {pwdModal}
    </Layout>
  );
}
