#!/usr/bin/env python3
"""生成知识库示例文件

在默认知识库中生成不同类型的示例文件，用于测试预览功能
"""
import os
import json
import pathlib

# 知识库目录
KB_DIR = "data/kb_docs/default"

# 确保目录存在
os.makedirs(KB_DIR, exist_ok=True)

# 1. Markdown 示例
markdown_content = """# AI 大模型知识问答

## 什么是大模型？

大模型（Large Language Model, LLM）是指参数量达到数十亿甚至千亿级别的深度学习模型。这些模型通过在海量文本数据上进行预训练，学习到了丰富的语言知识和世界知识。

### 典型特征

- **参数规模巨大**：通常超过 10 亿参数
- **训练数据海量**：数百 GB 到 TB 级别的文本数据
- **涌现能力**：在参数规模达到一定阈值后，模型会表现出人类难以预测的新能力

## 常见的大模型

| 模型 | 参数量 | 发布时间 | 组织 |
|------|--------|----------|------|
| GPT-3 | 175B | 2020 | OpenAI |
| GPT-4 | 未公开 | 2023 | OpenAI |
| Claude | 未公开 | 2023 | Anthropic |
| LLaMA | 7B-65B | 2023 | Meta |

## 应用场景

1. **对话系统** - 智能客服、个人助手
2. **内容创作** - 文章写作、代码生成
3. **知识问答** - 企业知识库检索
4. **数据分析** - 报告生成、数据解读

---

> 注意：大模型的能力取决于训练数据的质量和多样性
"""

(pathlib.Path(KB_DIR) / "AI大模型知识问答.md").write_text(markdown_content, encoding="utf-8")

# 2. Python 开发常见问题
python_content = """# Python 开发常见问题

## 1. 虚拟环境管理

### 创建虚拟环境
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\\Scripts\\activate   # Windows
```

### 为什么要使用虚拟环境？
- 隔离项目依赖
- 避免版本冲突
- 便于项目迁移

## 2. pip 包管理

### 安装依赖
```bash
pip install requests
pip install -r requirements.txt
```

### 导出依赖
```bash
pip freeze > requirements.txt
```

## 3. 常见错误及解决

### ModuleNotFoundError
**原因**：模块未安装或虚拟环境未激活

**解决**：
1. 确认虚拟环境已激活
2. 运行 `pip install <module_name>`

### IndentationError
**原因**：缩进不一致（混用空格和 Tab）

**解决**：统一使用 4 个空格缩进

## 4. 性能优化技巧

- 使用列表推导式代替 for 循环
- 使用生成器处理大文件
- 使用 `__slots__` 减少内存占用
- 使用 `lru_cache` 缓存函数结果
"""

(pathlib.Path(KB_DIR) / "Python开发常见问题.md").write_text(python_content, encoding="utf-8")

# 3. 产品手册
product_content = """# DocMind 产品手册

## 产品介绍

DocMind 是一款企业级 RAG（检索增强生成）平台，帮助企业快速构建智能知识问答系统。

## 核心功能

### 1. 知识库管理
- 支持 PDF、Markdown、Word、Excel 等多种格式
- 自动切片和向量化
- 增量索引，只处理变更文件

### 2. 智能对话
- 基于知识库的精准回答
- 联网搜索补充最新信息
- 多轮对话上下文理解

### 3. 多助手管理
- 自定义系统提示词
- 绑定特定知识库
- 权限控制

## 使用流程

1. **创建知识库** - 点击"新建知识库"按钮
2. **上传文档** - 支持拖拽上传
3. **重建索引** - 等待文档处理完成
4. **开始对话** - 在对话页面选择知识库

## 技术架构

- **检索引擎**：混合检索（BM25 + 向量 + Rerank）
- **向量存储**：ChromaDB
- **LLM**：通义千问系列
- **前端**：React + Ant Design

## 性能指标

- 检索召回率：95%+
- 平均响应时间：2-5 秒
- 支持并发：100+ QPS
"""

(pathlib.Path(KB_DIR) / "产品手册.md").write_text(product_content, encoding="utf-8")

# 4. 纯文本示例
txt_content = """技术文档索引

本文档用于测试纯文本文件的预览和编辑功能。

文件类型：.txt
字符编码：UTF-8
创建时间：2026-08-21

测试要点：
1. 文本内容是否正确显示
2. 编辑功能是否正常
3. 保存后是否触发重建索引

注意事项：
- 纯文本文件不支持格式化
- 建议使用 Markdown 格式获得更好的阅读体验
- 文件大小限制：50 MB
"""

(pathlib.Path(KB_DIR) / "内部机密.md").write_text(txt_content, encoding="utf-8")

# 5. JSON 配置示例
json_content = {
    "knowledge_base": {
        "id": "default",
        "name": "默认知识库",
        "description": "系统内置知识库",
        "config": {
            "chunk_size": 500,
            "chunk_overlap": 50,
            "top_k": 4,
            "rerank": True
        },
        "models": {
            "embedding": "text-embedding-v3",
            "chat": "qwen-turbo",
            "rerank": "gte-rerank-v2"
        },
        "supported_formats": [
            ".pdf",
            ".md",
            ".txt",
            ".docx",
            ".csv",
            ".json"
        ],
        "statistics": {
            "total_documents": 8,
            "total_chunks": 156,
            "last_updated": "2026-08-21T10:30:00Z"
        }
    }
}

(pathlib.Path(KB_DIR) / "知识库配置.json").write_text(json.dumps(json_content, ensure_ascii=False, indent=2), encoding="utf-8")

# 6. CSV 数据示例
csv_content = """模型名称,参数量,训练数据,发布时间,开源状态
GPT-3,175B,570GB,2020-06,否
GPT-4,未公开,未公开,2023-03,否
Claude 3,未公开,未公开,2024-03,否
LLaMA 2,7B-70B,2T tokens,2023-07,是
Qwen,1.8B-72B,3T tokens,2023-08,是
Mistral,7B,未公开,2023-09,是
"""

(pathlib.Path(KB_DIR) / "大模型对比.csv").write_text(csv_content, encoding="utf-8")

print(f"✅ 示例文件已生成到: {KB_DIR}")
print("\n生成的文件：")
for entry in sorted(pathlib.Path(KB_DIR).iterdir()):
    if entry.is_file():
        print(f"  - {entry.name} ({entry.stat().st_size} bytes)")

print("\n下一步：")
print('1. 在知识库管理页面点击"重建索引"')
print("2. 等待索引完成")
print("3. 点击文件名测试预览功能")
