#!/usr/bin/env python3
"""手动触发知识库重建索引

直接调用内部 API，无需登录态
"""
import sys
sys.path.insert(0, '.')

from docmind.rag.vector_store import VectorStore
from docmind import config
import os

def main():
    kb_id = 'default'
    kb_dir = config.KNOWLEDGE_DIR

    print(f"开始重建知识库: {kb_id}")
    print(f"知识库目录: {kb_dir}")

    # 直接创建 VectorStore 实例
    vector_store = VectorStore(collection_name=kb_id)

    print("正在扫描文档并重建索引...")
    result = vector_store.rebuild_incremental(kb_dir)

    print(f"\n✅ 重建完成！")
    print(f"  - 新增文件: {result.get('added', 0)}")
    print(f"  - 修改文件: {result.get('modified', 0)}")
    print(f"  - 删除文件: {result.get('removed', 0)}")
    print(f"  - 未变文件: {result.get('unchanged', 0)}")
    print(f"  - 总切片数: {result.get('chunks', 0)}")

    print(f"\n现在可以在浏览器中预览文件了！")

if __name__ == '__main__':
    main()
