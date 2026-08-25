import { useRef } from 'react';
import { App, Image, Progress, Select } from 'antd';
import {
  AudioOutlined,
  CloseOutlined,
  PaperClipOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import Sender from '@ant-design/x/es/sender';
import {
  MAX_IMGS,
  MAX_IMAGE_BYTES,
  compressImageFile,
} from './imageCompress';

interface ImageAttach { dataUrl: string; base64: string }

interface ChatInputProps {
  senderValue: string;
  onSenderValueChange: (v: string) => void;
  onSubmit: (text: string) => void;
  onCancel: () => void;
  streaming: boolean;
  imageAttaches: ImageAttach[];
  onImageAttachesChange: (updater: (prev: ImageAttach[]) => ImageAttach[]) => void;
  uploadPct: number | null;
  voiceId: string;
  onVoiceIdChange: (v: string) => void;
  voiceOptions: { id: string; label: string }[];
  voiceInput: {
    recording: boolean;
    cancelMode: boolean;
    micRef: React.RefObject<HTMLButtonElement>;
    beginRecord: (e: React.PointerEvent) => void;
    moveRecord: (e: React.PointerEvent) => void;
    endRecord: (e: React.PointerEvent) => void;
  };
  onNewChat: () => void;
}

/** 输入域：Sender + 图片附件（压缩/预览/上限） + 语音（按住说话/音色选择） */
export default function ChatInput({
  senderValue, onSenderValueChange, onSubmit, onCancel, streaming,
  imageAttaches, onImageAttachesChange, uploadPct,
  voiceId, onVoiceIdChange, voiceOptions, voiceInput, onNewChat,
}: ChatInputProps) {
  const { message: msgApi } = App.useApp();
  const imgInputRef = useRef<HTMLInputElement | null>(null);
  const {
    recording, cancelMode, micRef, beginRecord, moveRecord, endRecord,
  } = voiceInput;

  return (
    <div className="dm-chat-input">
      {recording && (
        <div className={`dm-voice-overlay${cancelMode ? ' cancel' : ''}`}>
          <div className="dm-voice-overlay-tip">
            {cancelMode ? '松开手指，取消输入' : '正在聆听，松开发送 · 上滑取消'}
          </div>
        </div>
      )}
      <Sender
        value={senderValue}
        onChange={onSenderValueChange}
        onSubmit={(text) => {
          onSenderValueChange('');
          onSubmit(text);
        }}
        onCancel={onCancel}
        loading={streaming}
        placeholder={imageAttaches.length ? '可以补充文字说明，直接发送则由 AI 看图作答…' : '输入问题，Enter 发送…'}
        header={
          imageAttaches.length > 0 ? (
            <div className="dm-attach-bar" style={{ width: '100%' }}>
              {imageAttaches.map((a, i) => (
                <div key={i} className="dm-attach-thumb">
                  <Image
                    src={a.dataUrl}
                    alt={`附件${i + 1}`}
                    width={52}
                    height={52}
                    style={{ objectFit: 'cover', borderRadius: 8, cursor: 'pointer' }}
                    preview={{ mask: '预览' }}
                  />
                  {uploadPct == null && (
                    <CloseOutlined
                      className="dm-attach-close"
                      onClick={() => onImageAttachesChange(
                        (prev) => prev.filter((_, j) => j !== i))}
                    />
                  )}
                </div>
              ))}
              {uploadPct != null ? (
                <span className="dm-attach-tip">
                  <Progress type="circle" size={20} percent={uploadPct} showInfo={false} />
                  <span style={{ marginLeft: 6 }}>正在上传 {uploadPct}%</span>
                </span>
              ) : (
                <span className="dm-attach-tip">
                  {imageAttaches.length}/{MAX_IMGS} 张 · 点击图片预览
                </span>
              )}
            </div>
          ) : undefined
        }
        actions={[
          <Select
            key="voice"
            size="small"
            variant="borderless"
            value={voiceId}
            onChange={onVoiceIdChange}
            options={voiceOptions.map((o) => ({ value: o.id, label: o.label }))}
            style={{ width: 150 }}
            popupMatchSelectWidth={false}
          />,
          <button
            key="mic"
            ref={micRef}
            className={`dm-mic dm-mic-inline${recording ? ' recording' : ''}`}
            onPointerDown={beginRecord}
            onPointerMove={moveRecord}
            onPointerUp={endRecord}
            onPointerCancel={endRecord}
            onContextMenu={(e) => e.preventDefault()}
            title="按住说话"
            style={{ touchAction: 'none' }}
          >
            <AudioOutlined />
          </button>,
        ]}
        prefix={
          <>
            <button
              className="dm-img-btn dm-newchat-btn"
              title="开始新对话"
              onClick={onNewChat}
            >
              <PlusOutlined />
            </button>
            <button
              className="dm-img-btn"
              title="附加图片（AI 直接看图作答）"
              onClick={() => imgInputRef.current?.click()}
              style={{ marginLeft: 4 }}
            >
              <PaperClipOutlined />
            </button>
          </>
        }
      />
      <input
        ref={imgInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        style={{ display: 'none' }}
        onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = '';
          if (!f) return;
          if (f.size > MAX_IMAGE_BYTES) {
            msgApi.error('图片过大（上限 8MB），请压缩后重试');
            return;
          }
          try {
            // 压缩/EXIF 剥离逻辑见 imageCompress.ts（canvas 重绘）
            const dataUrl = await compressImageFile(f);
            onImageAttachesChange((prev) =>
              prev.length >= MAX_IMGS
                ? (msgApi.warning(`最多携带 ${MAX_IMGS} 张图片`), prev)
                : [...prev, { dataUrl, base64: dataUrl }]);
          } catch {
            msgApi.error('图片处理失败，请重试');
          }
        }}
      />
    </div>
  );
}
