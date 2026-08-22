/** 全局导航菜单：AppLayout(PC Sider)与 Chat 侧栏(沉浸模式)共用。
 * 抽出为共享模块,保证两处菜单项与权限可见性永远一致。 */
import type { ReactNode } from 'react';
import {
  AlertOutlined, ApiOutlined, AppstoreOutlined, AuditOutlined,
  CloudUploadOutlined, ControlOutlined, DashboardOutlined,
  DatabaseOutlined, ExperimentOutlined, FileSearchOutlined,
  HistoryOutlined, KeyOutlined, LineChartOutlined, MessageOutlined,
  ReadOutlined, RobotOutlined, SafetyCertificateOutlined,
  SettingOutlined, TeamOutlined, ToolOutlined, WarningOutlined,
} from '@ant-design/icons';

export interface NavItem {
  key: string;
  icon?: ReactNode;
  label: string;
  children?: NavItem[];
}

export function buildNavItems(isAdmin: boolean): NavItem[] {
  const items: NavItem[] = [
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
  if (isAdmin) {
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
}

/** 展平分组取所有叶子 key(路由高亮用) */
export function flattenNavKeys(items: NavItem[]): string[] {
  const keys: string[] = [];
  for (const it of items) {
    if (it.children) keys.push(...flattenNavKeys(it.children));
    else keys.push(it.key);
  }
  return keys;
}
