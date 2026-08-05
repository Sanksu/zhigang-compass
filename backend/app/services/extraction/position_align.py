"""岗位名语义对齐（RAG 检索：与图谱已有岗位匹配）。

背景：岗位名归一化 normalize_position_name 是纯规则（关键词族/后缀表），
规则覆盖不到的语义相近岗位会被区分成不同节点（如"大模型应用开发" 与
"大模型工程师"）。本模块在规则归一化后追加一层语义匹配：对规则未归并的
岗位名，用 SBERT 与图谱已有 Position 节点名做余弦相似度，≥ 阈值命中则
替换为已有岗位名，减少相近岗位被区分开。

匹配策略（先规则、语义只补规则的洞，防误合并）：
1. 规则归一化：命中关键词族的直接返回标准名（分析师/科学家/DevOps 等已由
   规则稳定合并，语义不参与，避免把"数据工程师"误并到"数据分析师"）
2. 图谱已有岗位名：直接复用（对齐结果与图谱一致，保证 import_jd 按 name
   MERGE 命中同一节点）
3. 语义兜底：图谱不存在的新岗位名 → 与全部图谱岗位名余弦相似度 Top-1，
   ≥ 阈值（默认 0.9）映射到已有岗位名，否则保留原样

降级：SBERT 模型不可用 / 图谱加载失败 → 静默返回规则归一化结果，不阻塞
抽取主流程（与匹配引擎语义降级一致）。

幂等：对齐结果一旦命中即图谱已有名；图谱岗位名进程级缓存（TTL 300s），
重复调用稳定。消费方（batch_extract 抽取后 / import_jd 入图前）两次对齐
结果一致。
"""

import threading
import time
from typing import Callable, Optional

from app.services.extraction.dictionary import (
    _ANALYST_SUB_FAMILIES,
    _POSITION_KEYWORDS,
    normalize_position_name,
)

# 语义命中阈值（SBERT 余弦相似度，[0,1]）。可调：过低易误并，过高失去兜底作用
_SEMANTIC_THRESHOLD = 0.9
# 图谱岗位名缓存 TTL（图谱 T+1 更新，5 分钟足够覆盖单次抽取任务）
_KNOWN_NAMES_TTL = 300
# 规则族标准名集合：命中族即认为规则已归并，语义不参与。
# 含主族 + 分析师细分族（拆分后"财务分析师"等是标准名，不再走语义兜底）
_STANDARD_NAMES = frozenset(std for _, std in _POSITION_KEYWORDS) | frozenset(
    std for _, std in _ANALYST_SUB_FAMILIES
)


class PositionAligner:
    """图谱岗位名语义对齐器（进程级单例，懒加载图谱岗位名 + SBERT 向量）。

    embedder / name_source 可注入（测试替身）；缺省用全局 SkillEmbedder
    单例与 Neo4j 图谱。
    """

    _instance: Optional["PositionAligner"] = None

    def __init__(
        self,
        embedder: Optional[object] = None,
        name_source: Optional[Callable[[], list[str]]] = None,
    ) -> None:
        self._embedder = embedder
        self._name_source = name_source
        self._known: list[str] = []
        self._known_ts: float = 0.0
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "PositionAligner":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- 图谱岗位名加载（进程级 TTL 缓存） ----

    def _load_known_names(self) -> list[str]:
        now = time.monotonic()
        with self._lock:
            if self._known and now - self._known_ts < _KNOWN_NAMES_TTL:
                return self._known
            try:
                if self._name_source is not None:
                    names = [n for n in self._name_source() if n]
                else:
                    from app.core.database import neo4j_driver

                    with neo4j_driver.session() as session:
                        names = [
                            r["name"]
                            for r in session.run("MATCH (p:Position) RETURN p.name AS name")
                            if r["name"]
                        ]
            except Exception:
                # 图谱不可达：返回已缓存（若有），否则空列表 → 仅规则归并
                names = self._known
            self._known = names
            self._known_ts = now
            return names

    # ---- 语义匹配 ----

    def _semantic_best(self, name: str, known: list[str]) -> tuple[str, float] | None:
        """与图谱岗位名余弦相似度 Top-1；模型不可用返回 None（降级纯规则）。"""
        if not known:
            return None
        try:
            if self._embedder is not None:
                embedder = self._embedder
            else:
                from app.services.matching.semantic import SkillEmbedder

                embedder = SkillEmbedder.get()
            embedder.warm(known)  # 图谱岗位名向量批量预热（cache hit 后零成本）
        except Exception:
            return None
        best_name, best_score = None, 0.0
        for cand in known:
            if cand == name:
                continue
            try:
                score = embedder.similarity(name, cand)
            except Exception:
                continue
            if score > best_score:
                best_name, best_score = cand, score
        if best_name is None:
            return None
        return best_name, best_score

    # ---- 对外接口 ----

    def align(self, name: str) -> str:
        """岗位名对齐：规则归一化 → 图谱复用 → 语义兜底（顺序不可颠倒）。"""
        normalized = normalize_position_name(name)
        if not normalized:
            return ""
        # 1. 规则已归并到标准族：直接返回（语义不参与，防误并）
        if normalized in _STANDARD_NAMES:
            return normalized
        # 2. 图谱已有该岗位：直接复用（幂等，import_jd 按 name MERGE 命中）
        known = self._load_known_names()
        if normalized in known:
            return normalized
        # 3. 语义兜底：图谱中语义相近的已有岗位
        hit = self._semantic_best(normalized, known)
        if hit is not None and hit[1] >= _SEMANTIC_THRESHOLD:
            return hit[0]
        return normalized

    def align_many(self, names: list[str]) -> list[str]:
        """批量对齐（同批共享图谱岗位名加载，避免 N 次查询）。"""
        return [self.align(n) for n in names]
