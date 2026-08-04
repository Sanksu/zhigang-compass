"""Sentence-BERT 技能语义相似度模块（AL-M3-03 语义增强）。

设计文档 9.3：使用预训练 `paraphrase-multilingual-MiniLM-L12-v2` 直接推理（不微调），
技能名 Embedding 余弦相似度供匹配引擎做语义级同义词扩展（语义级 0.85-1.0 区间）。

工程约束：
- 模型缓存放 `backend/models/sbert/`（.gitignore 已忽略），首次调用才加载（懒加载）
- 模型不可用（未下载/加载失败）时抛 `SemanticUnavailableError`，
  匹配引擎捕获后降级为纯规则匹配，不阻塞主流程
"""

import threading
from pathlib import Path
from typing import Optional

# 模型名称（设计文档 9.3 指定）与缓存目录
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_CACHE_DIR = Path(__file__).resolve().parents[3] / "models" / "sbert"


class SemanticUnavailableError(Exception):
    """语义模型不可用（未下载/加载失败），调用方降级规则匹配。"""


class SkillEmbedder:
    """技能名 → 向量 + 余弦相似度（懒加载 + 名称向量缓存）。

    单例使用（进程内共享模型），线程安全由锁 + 幂等初始化保证。
    """

    _instance: Optional["SkillEmbedder"] = None

    def __init__(self) -> None:
        self._model = None
        self._cache: dict[str, object] = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "SkillEmbedder":
        """获取进程级单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- 内部 ----

    def _load(self):
        """加载 SBERT 模型（仅首次，失败包装为 SemanticUnavailableError）。"""
        with self._lock:
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

    def preload(self) -> None:
        """预热：加载模型（进程启动后后台执行，避免首次匹配请求阻塞 >30s）。"""
        try:
            self._load()
        except Exception:
            pass

    def warm(self, names: list[str]) -> None:
        """批量预计算技能名向量（一次 batch encode，避免评分时逐条前向推理）。

        names 为技能名集合；已缓存的跳过。模型不可用时静默忽略——
        后续单条调用同样会捕获 SemanticUnavailableError 降级纯规则匹配。
        """
        missing = [n.strip() for n in names if n and n.strip() not in self._cache]
        if not missing:
            return
        try:
            vecs = self._load().encode(missing)
        except Exception:
            return
        for key, vec in zip(missing, vecs):
            self._cache[key] = vec

    def embed(self, text: str) -> list:
        """文本 → 384 维向量（list[float]），供 pgvector 查询绑定与批量入库。

        模型不可用时抛 SemanticUnavailableError（调用方降级语义路）。
        """
        return list(self._vec(text))

    def similarity(self, a: str, b: str) -> float:
        """技能名语义余弦相似度（[0,1]）。

        向量为 SBERT encode 的 numpy 数组，用 numpy 点积（纯 Python 逐元素
        循环在评分全量调用下会成为瓶颈：单次 ~47ms × 数万次 >> 30s）。
        """
        va = self._vec(a)
        vb = self._vec(b)
        try:
            import numpy as np

            norm_a = float(np.linalg.norm(va))
            norm_b = float(np.linalg.norm(vb))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return float(np.dot(va, vb) / (norm_a * norm_b))
        except ImportError:
            # 防御：numpy 缺失时退回纯 Python 点积
            norm_a = float(sum(x * x for x in va) ** 0.5)
            norm_b = float(sum(x * x for x in vb) ** 0.5)
            if norm_a == 0 or norm_b == 0:
                return 0.0
            dot = float(sum(x * y for x, y in zip(va, vb)))
            return dot / (norm_a * norm_b)
