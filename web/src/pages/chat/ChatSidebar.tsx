import { Button, Input, Menu } from 'antd';
import {
  DeleteOutlined,
  DownOutlined,
  MenuOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import Conversations from '@ant-design/x/es/conversations';
import type { Conversation } from '@ant-design/x/es/conversations/interface';
import type { MenuProps } from 'antd';
import { buildNavItems, flattenNavKeys } from '../../nav';
import { fmtSessionTime, groupSessions, titleFromContent } from './utils';
import type { Session } from '../../api';

/** 稳定空数组：查询未就绪时保持派生列表身份稳定（供 useMemo deps） */
const EMPTY_SESSIONS: Session[] = [];

interface ChatSidebarProps {
  sessions: Session[];
  activeSid: string;
  isAdmin: boolean;
  navOpen: boolean;
  onToggleNav: () => void;
  sidebarOpen: boolean;
  sidebarCollapsed: boolean;
  convSearch: string;
  onConvSearchChange: (v: string) => void;
  onSwitchSession: (sid: string) => void;
  onDeleteSession: (sid: string) => void;
  onNewChat: () => void;
  onNavigate: (path: string) => void;
  pathname: string;
}

/** 左侧栏：导航菜单 + 会话搜索 + 按时间分组会话列表（移动端为抽屉，样式见 styles.css） */
export default function ChatSidebar({
  sessions, activeSid, isAdmin, navOpen, onToggleNav,
  sidebarOpen, sidebarCollapsed, convSearch, onConvSearchChange,
  onSwitchSession, onDeleteSession, onNewChat, onNavigate, pathname,
}: ChatSidebarProps) {
  const navItems = buildNavItems(isAdmin);
  const navLeafKeys = flattenNavKeys(navItems);
  const navSelected = navLeafKeys.includes(pathname) ? pathname : '';

  const kw = convSearch.trim().toLowerCase();
  const toConv = (s: Session): Conversation => ({
    key: s.id,
    label: (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 600, fontSize: 13.5, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {titleFromContent(s.title) || s.id.slice(0, 16)}
          </span>
          <span style={{ fontSize: 11, color: '#9a9a9a', flexShrink: 0 }}>
            {fmtSessionTime(s.updated_at || 0)}
          </span>
        </div>
        {s.last_msg && (
          <span style={{ fontSize: 12, color: '#9a9a9a', overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {titleFromContent(s.last_msg)}
          </span>
        )}
      </div>
    ),
  });

  const convMenu = (conv: Conversation): MenuProps => ({
    items: [
      { label: '删除', key: 'delete', icon: <DeleteOutlined />, danger: true },
    ],
    onClick: (info: any) => {
      if (info.key === 'delete') onDeleteSession(conv.key);
    },
  });

  const sessionGroups = groupSessions(sessions ?? EMPTY_SESSIONS);
  const convItems: Conversation[] = (sessions ?? [])
    .filter((s) => !kw || titleFromContent(s.title).toLowerCase().includes(kw))
    .map(toConv);

  return (
    <div className={`dm-chat-sidebar${sidebarOpen ? ' dm-open' : ''}${sidebarCollapsed ? ' dm-collapsed' : ''}`}>
      <div className="dm-chat-sidebar-header">
        <div
          className="dm-nav-head"
          role="button"
          title={navOpen ? '收起菜单' : '展开菜单'}
          onClick={onToggleNav}
        >
          <MenuOutlined className="dm-nav-toggle-icon" />
          <span className="dm-nav-brand">DocMind</span>
          <span className={`dm-nav-arrow${navOpen ? ' open' : ''}`}>
            {navOpen ? <DownOutlined /> : <RightOutlined />}
          </span>
        </div>
        {navOpen && (
          <Menu
            mode="inline"
            theme="light"
            className="dm-side-nav"
            selectedKeys={navSelected ? [navSelected] : []}
            defaultOpenKeys={navItems.filter(i => i.children).map(i => i.key)}
            items={navItems as any}
            onClick={(e) => onNavigate(e.key)}
          />
        )}
        <Button
          className="dm-mobile-newchat"
          type="primary" block size="small" icon={<PlusOutlined />}
          onClick={onNewChat}
        >
          新对话
        </Button>
        <Input
          allowClear
          size="small"
          prefix={<SearchOutlined style={{ color: '#999' }} />}
          placeholder="搜索对话"
          value={convSearch}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => onConvSearchChange(e.target.value)}
          style={{ marginTop: navOpen ? 8 : 6 }}
        />
      </div>
      <div className="dm-chat-sidebar-list">
        {kw ? (
          <Conversations
            items={convItems}
            activeKey={activeSid}
            onActiveChange={(key) => onSwitchSession(key)}
            menu={convMenu}
          />
        ) : (
          sessionGroups.map((g) => (
            <div key={g.label}>
              <div className="dm-conv-group-label">{g.label}</div>
              <Conversations
                items={g.items.map(toConv)}
                activeKey={activeSid}
                onActiveChange={(key) => onSwitchSession(key)}
                menu={convMenu}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
