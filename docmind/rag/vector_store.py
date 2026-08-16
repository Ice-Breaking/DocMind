"""轻量向量库：内存版，numpy 余弦相似度检索。

选型说明（面试可讲）：
- 知识库规模 < 1 万片时，内存暴力检索最简单可靠，无需引入 Chroma/Milvus
- 规模化演进路线：替换本类为 Chroma（本地）→ Milvus/ES（分布式），
  检索接口 search() 保持不变，上层无感知
- 启动时优先读磁盘缓存（指纹失效策略），避免重复调 embedding API
"""
from dataclasses import dataclass, field

import numpy as np

from docmind import config
from docmind.llm import embed
from docmind.rag.cache import compute_fingerprint, load_cache, save_cache
from docmind.rag.chunker import load_chunks


@dataclass
class SearchHit:
    text: str
    source: str
    score: float
    page: int | None = None   # PDF 切片携带的页码（引用溯源/原文预览定位用）


@dataclass
class VectorStore:
    chunks: list[dict] = field(default_factory=list)
    _matrix: np.ndarray | None = None

    def build(self, knowledge_dir: str | None = None, use_cache: bool = True) -> int:
        """加载知识库 → 切片 → 向量化。优先读缓存，重建后回写。返回切片数量"""
        fingerprint = compute_fingerprint(knowledge_dir)
        if use_cache:
            cached = load_cache(fingerprint)
            if cached is not None:
                self.chunks, matrix = cached
                self._matrix = matrix.astype(np.float32)
                print(f"[DocMind] 向量索引命中缓存（{len(self.chunks)} 个切片，未调 API）")
                return len(self.chunks)

        self.chunks = load_chunks(knowledge_dir)
        if not self.chunks:
            self._matrix = None
            return 0
        vectors = embed([c["text"] for c in self.chunks])
        self._matrix = np.asarray(vectors, dtype=np.float32)
        if use_cache:
            save_cache(fingerprint, self.chunks, self._matrix)
            print(f"[DocMind] 向量索引已重建并写入缓存（{len(self.chunks)} 个切片）")
        return len(self.chunks)

    def search(self, query: str, top_k: int | None = None) -> list[SearchHit]:
        """余弦相似度检索 top-k"""
        if self._matrix is None or len(self.chunks) == 0:
            return []
        k = top_k or config.TOP_K
        q = np.asarray(embed([query])[0], dtype=np.float32)
        # 归一化后点积 = 余弦相似度
        mat = self._matrix / np.linalg.norm(self._matrix, axis=1, keepdims=True)
        q = q / np.linalg.norm(q)
        scores = mat @ q
        top_idx = np.argsort(scores)[::-1][:k]
        return [
            SearchHit(
                text=self.chunks[i]["text"],
                source=self.chunks[i]["source"],
                score=float(scores[i]),
            )
            for i in top_idx
        ]
