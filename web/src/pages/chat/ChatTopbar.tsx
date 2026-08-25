import { Button, Select } from 'antd';
import { DownloadOutlined, MenuOutlined } from '@ant-design/icons';
import { titleFromContent } from './utils';
import type { Session } from '../../api';

interface ChatTopbarProps {
  sessions: Session[];
  activeSid: string;
  activeSidReady: boolean;
  assistantId: string;
  assistantOptions: { value: string; label: string }[];
  onAssistantChange: (id: string) => void;
  onExport: () => void;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

/** 顶栏：侧栏开关 + 当前会话标题 + 助手切换 + Markdown 导出 */
export default function ChatTopbar({
  sessions, activeSid, activeSidReady, assistantId, assistantOptions,
  onAssistantChange, onExport, sidebarCollapsed, onToggleSidebar,
}: ChatTopbarProps) {
  const title = titleFromContent(
    sessions.find((x) => x.id === activeSid)?.title || '') || '新对话';
  return (
    <div className="dm-chat-topbar">
      <button
        className="dm-topbar-menu"
        onClick={onToggleSidebar}
        title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
      >
        <MenuOutlined />
      </button>
      <span className="dm-topbar-title">{title}</span>
      <div className="dm-topbar-actions">
        <Select
          size="small"
          variant="borderless"
          value={assistantId}
          options={assistantOptions}
          onChange={onAssistantChange}
          style={{ width: 140 }}
        />
        <Button size="small" type="text" icon={<DownloadOutlined />}
          title="导出当前会话为 Markdown" disabled={!activeSidReady}
          onClick={onExport}
        />
      </div>
    </div>
  );
}
