import { Button, Image } from 'antd';
import {
  DislikeFilled,
  DislikeOutlined,
  LikeFilled,
  LikeOutlined,
  PauseOutlined,
  ReloadOutlined,
  RobotOutlined,
  SoundOutlined,
} from '@ant-design/icons';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import type { ThoughtChainItem } from '@ant-design/x/es/thought-chain/Item';
import ThoughtChain from '@ant-design/x/es/thought-chain';
import MarkdownContent from '../../components/MarkdownContent';
import UserAvatar from '../../components/UserAvatar';
import type { Assistant, Me } from '../../api';
import type { SpeechState } from './useSpeech';
import { computeAssistantSeq, extractWarnCapsules, splitImagesFromText } from './utils';

export interface BubbleRolesDeps {
  /** 当前登录用户（用户气泡头像） */
  me: Me;
  /** 当前选中助手（助手气泡头像，未配置时回退机器人图标） */
  currentAssistant?: Assistant;
  /** 是否正在流式输出（决定正文渲染为纯文本还是 markdown、footer 是否显示重新生成） */
  streaming: boolean;
  /** 流式思考步骤（仅流式中的最后一条助手气泡展示） */
  thinkingSteps: ThoughtChainItem[];
  /** TTS 播报状态（useSpeech 返回） */
  speech: SpeechState | null;
  /** 消息镜像 ref（seq 推导 / 时间戳读取，避免回调过期闭包） */
  messagesRef: { readonly current: BubbleDataType[] };
  feedbackMapRef: { readonly current: Record<string, 'up' | 'down'> };
  failedMapRef: { readonly current: Record<string, string> };
  /** 引用溯源定位（MarkdownContent 引用链接点击） */
  onLocate: (filename: string, page?: string) => void;
  /** 失败气泡内联重试 */
  onRetry: (key: string) => void;
  /** 基于上一条问题重新生成 */
  onRegenerate: () => void;
  /** 点赞/点踩 */
  onFeedback: (seq: number, rating: 'up' | 'down') => void;
  /** TTS 播报 */
  onSpeak: (content: string, key: string) => void;
}

const fmtHM = (ts: number) => {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/**
 * Bubble.List 角色配置：助手正文（警示胶囊 + 思考链 + 流式/markdown 双态）、
 * 助手 footer（时间戳 / 重新生成 / 播报 / 点赞点踩 / 失败重试）与用户气泡
 * （图片缩略图提取 + 时间戳）。自 Chat.tsx 原地迁出，结构与闭包语义保持一致：
 * 每次渲染重建 roles，内部经 ref 读最新消息，无过期闭包。
 *
 * @param deps 见 BubbleRolesDeps；动作回调由宿主注入
 * @returns Bubble.List 的 roles 配置
 */
export function useBubbleRoles(deps: BubbleRolesDeps) {
  const {
    me, currentAssistant, streaming, thinkingSteps, speech,
    messagesRef, feedbackMapRef, failedMapRef,
    onLocate, onRetry, onRegenerate, onFeedback, onSpeak,
  } = deps;

  /* ---- render helper: AI message content ---- */
  const renderAssistantContent = (content: string, isCurrentlyStreaming: boolean) => {
    // 去说明书感：OOD 标注 / 通识来源 从方括号纯文本提取为警示胶囊
    const { text, capsules } = extractWarnCapsules(content);
    return (
      <div>
        {capsules.map((c, i) => (
          <div key={i} className={`dm-capsule ${c.cls}`}>{c.label}</div>
        ))}
        {isCurrentlyStreaming && thinkingSteps.length > 0 && (
          <div className="dm-thought-chain">
            <ThoughtChain
              items={thinkingSteps}
              collapsible
              size="small"
            />
          </div>
        )}
        {isCurrentlyStreaming ? (
          <div style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
        ) : (
          <MarkdownContent content={text} onLocate={onLocate} />
        )}
      </div>
    );
  };

  /* ---- render helper: feedback footer ---- */
  const renderAssistantFooter = (_content: string, info: { key?: string | number }) => {
    const curMsgs = messagesRef.current;
    const seq = computeAssistantSeq(curMsgs, info.key ?? '');
    if (seq == null) return null;
    const seqKey = String(seq);
    const fb = feedbackMapRef.current[seqKey];
    // 失败气泡：内联重试按钮（失败在哪，按钮在哪）
    const failedQ = failedMapRef.current[String(info.key)];
    if (failedQ) {
      return (
        <div className="dm-feedback">
          <Button
            size="small"
            type="primary"
            ghost
            icon={<ReloadOutlined />}
            onClick={() => onRetry(String(info.key))}
          >
            重试
          </Button>
        </div>
      );
    }
    const isLastMsg = curMsgs.findIndex((m) => m.key === info.key) === curMsgs.length - 1 && !streaming;
    const lastIdx = curMsgs.findIndex((m) => m.key === info.key);
    const ts = (curMsgs[lastIdx] as any)?.ts as number | undefined;
    const tsText = ts ? fmtHM(ts) : null;
    return (
      <div className="dm-feedback">
        {tsText && <span style={{ fontSize: 11, color: '#9a9a9a', marginRight: 4 }}>{tsText}</span>}
        {isLastMsg && (
          <Button
            type="text"
            size="small"
            title="重新生成（基于上一条问题重问）"
            icon={<ReloadOutlined />}
            onClick={onRegenerate}
          />
        )}
        <Button
          type="text"
          size="small"
          title="播报"
          icon={
            speech?.key === String(info.key) && speech.status === 'playing' ? (
              <PauseOutlined />
            ) : (
              <SoundOutlined />
            )
          }
          loading={speech?.key === String(info.key) && speech.status === 'loading'}
          onClick={() => onSpeak(_content, String(info.key))}
        />
        <Button
          type="text"
          size="small"
          icon={fb === 'up' ? <LikeFilled style={{ color: '#6366f1' }} /> : <LikeOutlined />}
          onClick={() => onFeedback(seq, 'up')}
        />
        <Button
          type="text"
          size="small"
          icon={fb === 'down' ? <DislikeFilled style={{ color: '#6366f1' }} /> : <DislikeOutlined />}
          onClick={() => onFeedback(seq, 'down')}
        />
      </div>
    );
  };
  /* ---- bubble roles ---- */
  const bubbleRoles = {
    user: {
      placement: 'end' as const,
      avatar: {
        icon: <UserAvatar avatar={me.avatar} name={me.user} size={28} />,
        style: { background: 'transparent' },
      },
      variant: 'filled' as const,
      messageRender: (content: string) => {
        // 图片消息：提取 markdown 图片（当轮为 dataUrl、历史为 /files/uploads 短链），
        // 以缩略图展示 + 点击预览；避免 base64 长 URL 以文本形式露出
        const { imgs, text } = splitImagesFromText(content);
        return (
          <div>
            {imgs.length > 0 && (
              <div
                style={{
                  display: 'flex', gap: 6, flexWrap: 'wrap',
                  marginBottom: text ? 6 : 0, justifyContent: 'flex-end',
                }}
              >
                {imgs.map((u, i) => (
                  <Image
                    key={i}
                    src={u}
                    alt="图片"
                    width={imgs.length > 1 ? 150 : 200}
                    height={imgs.length > 1 ? 150 : 200}
                    style={{ borderRadius: 10, objectFit: 'cover' }}
                    preview={{ mask: '预览' }}
                  />
                ))}
              </div>
            )}
            {text && <div style={{ whiteSpace: 'pre-wrap' }}>{text}</div>}
          </div>
        );
      },
      footer: (_c: string, info: { key?: string | number }) => {
        const m = messagesRef.current.find((x) => x.key === info.key);
        if (!(m as any)?.ts) return null;
        return (
          <div style={{ fontSize: 11, color: '#9a9a9a', textAlign: 'right' }}>
            {fmtHM((m as any).ts)}
          </div>
        );
      },
    },
    assistant: {
      placement: 'start' as const,
      avatar: currentAssistant?.avatar
        ? {
            icon: (
              <UserAvatar
                avatar={currentAssistant.avatar}
                name={currentAssistant.name}
                size={28}
              />
            ),
            style: { background: 'transparent' },
          }
        : { icon: <RobotOutlined />, style: { background: '#6366f1', color: '#fff' } },
      variant: 'filled' as const,
      messageRender: (content: string, _type?: any, info?: { key?: string | number }) => {
        const curMsgs = messagesRef.current;
        const isLast = !!info?.key && curMsgs.length > 0 && curMsgs[curMsgs.length - 1].key === info.key;
        return renderAssistantContent(content, isLast && streaming);
      },
      footer: (_content: string, info: { key?: string | number }) =>
        renderAssistantFooter(_content, info),
    },
  };

  return { bubbleRoles };
}

export type BubbleRoles = ReturnType<typeof useBubbleRoles>['bubbleRoles'];
