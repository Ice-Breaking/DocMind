# 知识库文件预览+编辑功能实现方案

## 已完成：后端 API

### 1. 预览接口

**GET** `/api/kbs/{kb_id}/docs/{filename}/preview`

**返回：**
```json
{
  "filename": "AI大模型知识问答.md",
  "file_type": ".md",
  "total_chunks": 15,
  "chunks": [
    {
      "index": 0,
      "text": "# AI大模型知识问答\n\n什么是大模型...",
      "page": null
    },
    {
      "index": 1,
      "text": "大模型的训练过程包括...",
      "page": null
    }
  ]
}
```

### 2. 编辑接口

**PUT** `/api/kbs/{kb_id}/docs/{filename}/content`

**请求体：**
```json
{
  "content": "更新后的文件内容"
}
```

**支持编辑的格式：**`.md`, `.txt`, `.json`, `.csv`

**不支持编辑：**`.pdf`, `.docx`, `.png` 等二进制文件

---

## 待实现：前端组件

### 方案 A：快速实现（推荐）

在现有的 `web/src/pages/KnowledgeBases.tsx` 中添加 Modal 组件。

#### 步骤 1：安装依赖（如果没有）

```bash
cd web
npm install react-markdown
```

#### 步骤 2：创建预览 Modal 组件

**文件：`web/src/components/DocumentPreviewModal.tsx`**

```typescript
import React, { useState, useEffect } from 'react';
import { Modal, Spin, Button, message, Tabs } from 'antd';
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
  }, [visible, filename]);

  const loadPreview = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/kbs/${kbId}/docs/${encodeURIComponent(filename)}/preview`);
      if (!res.ok) {
        throw new Error('加载失败');
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

      message.success('保存成功，等待重建索引后生效');
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
      return <div style={{ textAlign: 'center', padding: '50px' }}><Spin /></div>;
    }

    if (!data) {
      return <div style={{ padding: '20px' }}>无数据</div>;
    }

    // 编辑模式
    if (isEditing) {
      return (
        <div>
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            style={{
              width: '100%',
              height: '500px',
              padding: '12px',
              fontSize: '14px',
              fontFamily: 'monospace',
              border: '1px solid #d9d9d9',
              borderRadius: '4px',
            }}
          />
        </div>
      );
    }

    // 预览模式
    const fullContent = data.chunks.map((c) => c.text).join('\n\n---\n\n');

    if (data.file_type === '.md') {
      return (
        <div
          style={{
            maxHeight: '600px',
            overflow: 'auto',
            padding: '20px',
            background: '#fafafa',
            borderRadius: '4px',
          }}
        >
          <ReactMarkdown>{fullContent}</ReactMarkdown>
        </div>
      );
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
          fontSize: '14px',
          lineHeight: '1.6',
          whiteSpace: 'pre-wrap',
          wordWrap: 'break-word',
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
          <span>{filename}</span>
          <span style={{ fontSize: '12px', color: '#999', fontWeight: 'normal' }}>
            {data && `共 ${data.total_chunks} 个切片`}
          </span>
        </div>
      }
      open={visible}
      onCancel={onClose}
      width={900}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <div>
            {isEditable && !isEditing && (
              <Button icon={<EditOutlined />} onClick={() => setIsEditing(true)}>
                编辑
              </Button>
            )}
            {isEditing && (
              <>
                <Button onClick={() => setIsEditing(false)} style={{ marginRight: '8px' }}>
                  取消
                </Button>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  loading={saving}
                  onClick={handleSave}
                >
                  保存
                </Button>
              </>
            )}
          </div>
          <Button onClick={onClose}>关闭</Button>
        </div>
      }
    >
      {renderContent()}
    </Modal>
  );
};
```

#### 步骤 3：集成到知识库管理页面

**修改：`web/src/pages/KnowledgeBases.tsx`**

```typescript
import { DocumentPreviewModal } from '../components/DocumentPreviewModal';

// 在组件内部添加状态
const [previewVisible, setPreviewVisible] = useState(false);
const [previewFilename, setPreviewFilename] = useState('');

// 修改文件列表的文件名显示为可点击
// 在渲染文件列表时：
{docs.map((doc) => (
  <div key={doc.name} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0' }}>
    <div>
      {/* 文件名改为可点击 */}
      <a
        onClick={() => {
          setPreviewFilename(doc.name);
          setPreviewVisible(true);
        }}
        style={{ cursor: 'pointer', color: '#1890ff' }}
      >
        📄 {doc.name}
      </a>
      <div style={{ fontSize: '12px', color: '#999' }}>
        {formatBytes(doc.size)} · 更新于 {formatDate(doc.modified)}
      </div>
    </div>
    <Button
      danger
      size="small"
      onClick={() => handleDelete(doc.name)}
    >
      删除
    </Button>
  </div>
))}

{/* 在组件底部添加 Modal */}
<DocumentPreviewModal
  visible={previewVisible}
  onClose={() => setPreviewVisible(false)}
  kbId={selectedKbId}
  filename={previewFilename}
/>
```

---

### 方案 B：完整实现（更强大）

如果需要更强大的功能（语法高亮、代码编辑器、PDF 预览），可以使用以下库：

```bash
npm install @monaco-editor/react  # 代码编辑器（VS Code 同款）
npm install react-pdf              # PDF 预览
```

然后扩展 `DocumentPreviewModal` 组件，根据文件类型选择不同的渲染器。

---

## 使用流程

### 用户视角

1. 打开"知识库管理"页面
2. 点击某个知识库，查看文件列表
3. **点击文件名**（新增：原来不可点击，现在变成蓝色链接）
4. 弹出 Modal 窗口，显示文件的切片内容
5. 对于 `.md` / `.txt` / `.json` / `.csv` 文件：
   - 点击"编辑"按钮 → 进入编辑模式
   - 修改内容后点击"保存"
   - 系统提示"保存成功，等待重建索引后生效"
6. 对于 `.pdf` / `.docx` / 图片文件：
   - 只能预览，不能编辑

---

## 技术要点

### 1. 切片显示

- 后端返回的是 `chunks` 数组（切片后的内容）
- 前端用 `---` 分隔符连接所有切片，形成完整预览

### 2. 编辑 vs 预览

- **预览模式：**
  - Markdown 用 `react-markdown` 渲染
  - 其他文本用 `<pre>` 标签展示

- **编辑模式：**
  - 用 `<textarea>` 或 Monaco Editor
  - 保存时调用 PUT 接口

### 3. 重建索引

- 编辑保存后，会创建 `ingest_task`
- 需要手动触发"重建索引"（知识库管理页面的按钮）
- 或等待自动重建（如果有定时任务）

---

## 部署步骤

1. **后端已完成**（已提交到代码）
2. **前端实现：**
   ```bash
   cd web
   
   # 安装依赖
   npm install react-markdown
   
   # 创建组件
   # 复制上面的 DocumentPreviewModal.tsx 代码
   
   # 修改 KnowledgeBases.tsx
   # 集成 Modal 组件
   
   # 重新构建
   npm run build
   ```

3. **验证：**
   - 打开知识库管理
   - 点击文件名
   - 查看是否弹出预览窗口
   - 尝试编辑和保存

---

## 注意事项

1. **权限：** 所有接口都需要登录，自动继承 cookie 认证
2. **大文件：** 如果切片数量过多（>100），前端可能渲染慢，可以考虑分页加载
3. **实时性：** 编辑保存后，需要重建索引才能在对话中生效
4. **PDF 预览：** 当前只显示切片文本，如果需要原始 PDF 预览，需要额外实现

---

**实现优先级：**
- ✅ 后端 API（已完成）
- ⏳ 前端基础预览（方案 A，推荐先做）
- ⏳ 前端编辑功能（方案 A）
- ⏳ 增强功能（方案 B，可选）

需要我继续帮你实现前端代码吗？还是这个文档已经足够你自己完成了？
