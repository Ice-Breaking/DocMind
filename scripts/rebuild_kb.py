#!/usr/bin/env python3
"""手动触发知识库重建索引

直接调用内部 API，无需登录态
"""
import sys
sys.path.insert(0, '.')

from docmind.rag.vector_store import VectorStore, COLLECTION_NAME
from docmind import config

def main():
    kb_id = 'default'
    kb_dir = config.KNOWLEDGE_DIR

    print(f"开始重建知识库: {kb_id}")
    print(f"知识库目录: {kb_dir}")

    # 直接创建 VectorStore 实例
    # 注意：collection 名必须与服务一致（COLLECTION_NAME="knowledge"），
    # 不能用 kb_id——否则切片写入孤立 collection，服务读不到，
    # 且共享 manifest 会让服务的增量重建误判"已索引"而永远跳过
    vector_store = VectorStore(collection_name=COLLECTION_NAME)

    print("正在扫描文档并重建索引...")
    result = vector_store.rebuild_incremental(kb_dir)

    print("\n✅ 重建完成！")
    print(f"  - 新增文件: {result.get('added', 0)}")
    print(f"  - 修改文件: {result.get('modified', 0)}")
    print(f"  - 删除文件: {result.get('removed', 0)}")
    print(f"  - 未变文件: {result.get('unchanged', 0)}")
    print(f"  - 总切片数: {result.get('chunks', 0)}")

    print("\n现在可以在浏览器中预览文件了！")

if __name__ == '__main__':
    main()
