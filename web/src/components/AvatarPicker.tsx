import { useState } from 'react';
import { Button, Segmented } from 'antd';
import { SyncOutlined } from '@ant-design/icons';
import UserAvatar, { DB_STYLES, DB_STYLE_LABELS } from './UserAvatar';

/** emoji 兜底分组：男 / 女 / 小孩 / 萌宠（基础码点，全平台稳渲染） */
const EMOJI_GROUPS: { label: string; items: string[] }[] = [
  { label: '男性', items: ['👨', '👦', '🧑', '👴', '🧔', '👴', '👲', '🕵️'] },
  { label: '女性', items: ['👩', '👧', '👵', '👸', '🧕', '🤶', '💃', '🙆'] },
  { label: '小孩', items: ['🧒', '👶', '👦', '👧', '🙋', '🤷'] },
  { label: '萌宠', items: ['🐱', '🐶', '🦊', '🐼', '🐳', '🦄', '🐯', '🐰'] },
];

const SEEDS = Array.from({ length: 12 }, (_, i) => `dm${i + 1}`);

/**
 * 头像选择器：DiceBear 自托管（4 种人物风格 × 多种子）+ emoji 兜底分组。
 * 移动端：网格均布、Tab 横向可滚、按钮紧凑。
 * value 为 avatar token：db:{style}:{seed} 或 emoji。
 */
export default function AvatarPicker({
  value,
  onChange,
  username,
}: {
  value?: string;
  onChange?: (v: string) => void;
  username: string;
}) {
  const val = value || '';
  const pick = (v: string) => onChange?.(v);
  const [tab, setTab] = useState<string>('personas');
  const isDb = tab in DB_STYLES;

  const randomSeed = () => Math.random().toString(36).slice(2, 8);

  return (
    <div className="dm-avatar-picker">
      {/* 实时预览 */}
      <div className="dm-avatar-preview">
        <UserAvatar avatar={val} name={username} size={56} />
        <span style={{ color: '#8a94a6', fontSize: 12 }}>当前头像预览</span>
      </div>

      {/* 风格切换：窄屏横向滚动 */}
      <div className="dm-avatar-tabs">
        <Segmented
          size="small"
          value={tab}
          onChange={(v) => setTab(String(v))}
          options={[
            ...DB_STYLE_LABELS.map((s) => ({ value: s.id, label: s.label })),
            { value: 'emoji', label: '表情符号' },
          ]}
        />
      </div>

      {isDb ? (
        <>
          <div className="dm-avatar-grid">
            <button
              className="dm-avatar-cell"
              title={`专属：${username}`}
              onClick={() => pick(`db:${tab}:${username}`)}
            >
              <UserAvatar avatar={`db:${tab}:${username}`} name={username} size={44} />
            </button>
            {SEEDS.map((seed) => (
              <button
                key={seed}
                className="dm-avatar-cell"
                onClick={() => pick(`db:${tab}:${seed}`)}
              >
                <UserAvatar avatar={`db:${tab}:${seed}`} name={seed} size={44} />
              </button>
            ))}
          </div>
          <div className="dm-avatar-actions">
            <Button
              size="small"
              icon={<SyncOutlined />}
              onClick={() => pick(`db:${tab}:${randomSeed()}`)}
            >
              随机换一个
            </Button>
          </div>
        </>
      ) : (
        <div>
          {EMOJI_GROUPS.map((g) => (
            <div key={g.label}>
              <div className="dm-avatar-group-label">{g.label}</div>
              <div className="dm-avatar-grid">
                {g.items.map((e, i) => (
                  <button
                    key={`${e}-${i}`}
                    className="dm-avatar-cell"
                    onClick={() => pick(e)}
                  >
                    <UserAvatar avatar={e} size={40} />
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
