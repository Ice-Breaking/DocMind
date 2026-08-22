import React, { useState, useEffect } from 'react';
import { Modal, Spin, Button, message, Alert } from 'antd';
import { EditOutlined, SaveOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';

interface Chunk {
  index: number;
  text: string;
  page: number | null;
}

interface PreviewData {
  filename: string;
  file_type: string;
  total_chunks: number;
  chunks: Chunk[];
}

interface DocumentPreviewModalProps {
  visible: boolean;
  onClose: () => void;
  kbId: string;
  filename: string;
}

export const DocumentPreviewModal: React.FC<DocumentPreviewModalProps> = ({
  visible,
  onClose,
  kbId,
  filename,
}) => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<PreviewData | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [saving, setSaving] = useState(false);

  // 判断是否可编辑
  const isEditable = data?.file_type && ['.md', '.txt', '.json', '.csv'].includes(data.file_type);

  // 加载预览数据
  useEffect(() => {
    if (visible && filename) {
      loadPreview();
    }
  }, [visible, filename, kbId]);

  const loadPreview = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/kbs/${kbId}/docs/${encodeURIComponent(filename)}/preview`);
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || '加载失败');
      }
      const result = await res.json();
      setData(result);

      // 合并所有切片为完整内容
      const fullContent = result.chunks.map((c: Chunk) => c.text).join('\n\n---\n\n');
      setEditContent(fullContent);
    } catch (err: any) {
      message.error(err.message || '加载预览失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/kbs/${kbId}/docs/${encodeURIComponent(filename)}/content`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editContent }),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || '保存失败');
      }

      message.success('保存成功！请在知识库管理页面点击"重建索引"使更改生效');
      setIsEditing(false);
      loadPreview(); // 重新加载
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const renderContent = () => {
    if (loading) {
      return (
        <div style={{ textAlign: 'center', padding: '100px 0' }}>
          <Spin size="large" />
          <div style={{ marginTop: '16px', color: '#999' }}>加载中...</div>
        </div>
      );
    }

    if (!data) {
      return (
        <Alert
          message="无数据"
          description="无法加载文档内容，请稍后重试"
          type="warning"
          showIcon
        />
      );
    }

    // 编辑模式
    if (isEditing) {
      return (
        <div>
          <Alert
            message="编辑模式"
            description="修改后点击保存，然后需要手动重建索引才能在对话中生效"
            type="info"
            showIcon
            style={{ marginBottom: '16px' }}
          />
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            placeholder="在此编辑文件内容..."
            style={{
              width: '100%',
              height: '500px',
              padding: '12px',
              fontSize: '14px',
              fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", Menlo, monospace',
              border: '1px solid #d9d9d9',
              borderRadius: '4px',
              resize: 'vertical',
            }}
          />
        </div>
      );
    }

    // 预览模式
    const fullContent = data.chunks.map((c) => c.text).join('\n\n---\n\n');

    // Markdown 渲染
    if (data.file_type === '.md') {
      return (
        <div
          style={{
            maxHeight: '600px',
            overflow: 'auto',
            padding: '20px',
            background: '#fafafa',
            borderRadius: '4px',
            border: '1px solid #f0f0f0',
          }}
        >
          <ReactMarkdown
            components={{
              h1: ({ node, ...props }) => <h1 style={{ borderBottom: '2px solid #e8e8e8', paddingBottom: '8px' }} {...props} />,
              h2: ({ node, ...props }) => <h2 style={{ borderBottom: '1px solid #e8e8e8', paddingBottom: '6px' }} {...props} />,
              code: ({ node, inline, ...props }: any) =>
                inline ? (
                  <code style={{ background: '#f5f5f5', padding: '2px 6px', borderRadius: '3px', fontSize: '0.9em' }} {...props} />
                ) : (
                  <code style={{ background: '#f5f5f5', display: 'block', padding: '12px', borderRadius: '4px', fontSize: '0.9em' }} {...props} />
                ),
            }}
          >
            {fullContent}
          </ReactMarkdown>
        </div>
      );
    }

    // JSON 格式化显示
    if (data.file_type === '.json') {
      try {
        const formatted = JSON.stringify(JSON.parse(fullContent), null, 2);
        return (
          <pre
            style={{
              maxHeight: '600px',
              overflow: 'auto',
              padding: '20px',
              background: '#fafafa',
              borderRadius: '4px',
              border: '1px solid #f0f0f0',
              fontSize: '13px',
              lineHeight: '1.6',
              margin: 0,
            }}
          >
            {formatted}
          </pre>
        );
      } catch {
        // JSON 解析失败，按普通文本显示
      }
    }

    // 其他文本类型：纯文本展示
    return (
      <pre
        style={{
          maxHeight: '600px',
          overflow: 'auto',
          padding: '20px',
          background: '#fafafa',
          borderRadius: '4px',
          border: '1px solid #f0f0f0',
          fontSize: '14px',
          lineHeight: '1.6',
          whiteSpace: 'pre-wrap',
          wordWrap: 'break-word',
          margin: 0,
        }}
      >
        {fullContent}
      </pre>
    );
  };

  return (
    <Modal
      title={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontWeight: 600 }}>📄 {filename}</span>
          <span style={{ fontSize: '12px', color: '#999', fontWeight: 'normal' }}>
            {data && `共 ${data.total_chunks} 个切片`}
          </span>
        </div>
      }
      open={visible}
      onCancel={() => {
        if (isEditing) {
          if (window.confirm('有未保存的更改，确定关闭吗？')) {
            setIsEditing(false);
            onClose();
          }
        } else {
          onClose();
        }
      }}
      width={900}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            {!isEditable && data && (
              <span style={{ fontSize: '12px', color: '#999' }}>
                {data.file_type === '.pdf' || data.file_type === '.docx'
                  ? '此文件类型不支持在线编辑'
                  : ''}
              </span>
            )}
          </div>
          <div>
            {isEditable && !isEditing && (
              <Button icon={<EditOutlined />} onClick={() => setIsEditing(true)} style={{ marginRight: '8px' }}>
                编辑
              </Button>
            )}
            {isEditing && (
              <>
                <Button onClick={() => {
                  if (window.confirm('放弃未保存的更改？')) {
                    setIsEditing(false);
                    loadPreview();
                  }
                }} style={{ marginRight: '8px' }}>
                  取消
                </Button>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  loading={saving}
                  onClick={handleSave}
                  style={{ marginRight: '8px' }}
                >
                  保存
                </Button>
              </>
            )}
            <Button onClick={onClose}>关闭</Button>
          </div>
        </div>
      }
    >
      {renderContent()}
    </Modal>
  );
};
