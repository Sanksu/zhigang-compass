"""Sentence-BERT 技能语义相似度模块（AL-M3-03 语义增强）。

设计文档 9.3：使用预训练 `paraphrase-multilingual-MiniLM-L12-v2` 直接推理（不微调），
技能名 Embedding 余弦相似度供匹配引擎做语义级同义词扩展（语义级 0.85-1.0 区间）。

工程约束：
- 模型缓存放 `backend/models/sbert/`（.gitignore 已忽略），首次调用才加载（懒加载）
- 模型不可用（未下载/加载失败）时抛 `SemanticUnavailableError`，
  匹配引擎捕获后降级为纯规则匹配，不阻塞主流程
"""

from pathlib import Path
from typing import Optional

# 模型名称（设计文档 9.3 指定）与缓存目录
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_CACHE_DIR = Path(__file__).resolve().parents[3] / "models" / "sbert"


class SemanticUnavailableError(Exception):
    """语义模型不可用（未下载/加载失败），调用方降级规则匹配。"""


class SkillEmbedder:
    """技能名 → 向量 + 余弦相似度（懒加载 + 名称向量缓存）。

    单例使用（进程内共享模型），线程安全由 GIL + 幂等初始化保证。
    """

    _instance: Optional["SkillEmbedder"] = None

    def __init__(self) -> None:
        self._model = None
        self._cache: dict[str, object] = {}

    @classmethod
    def get(cls) -> "SkillEmbedder":
        """获取进程级单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- 内部 ----

    def _load(self):
        """加载 SBERT 模型（仅首次，失败包装为 SemanticUnavailableError）。"""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(MODEL_NAME, cache_folder=str(_CACHE_DIR))
            except Exception as e:  # 网络/依赖/资源错误统一视为不可用
                raise SemanticUnavailableError(f"SBERT 模型加载失败: {e}") from e
        return self._model

    def _vec(self, text: str) -> object:
        key = text.strip()
        if key not in self._cache:
            self._cache[key] = self._load().encode([key])[0]
        return self._cache[key]

    # ---- 对外接口 ----

    def similarity(self, a: str, b: str) -> float:
        """技能名语义余弦相似度（[0,1]）。"""
        va = self._vec(a)
        vb = self._vec(b)
        norm_a = float(sum(x * x for x in va) ** 0.5)
        norm_b = float(sum(x * x for x in vb) ** 0.5)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        dot = float(sum(x * y for x, y in zip(va, vb)))
        return dot / (norm_a * norm_b)
