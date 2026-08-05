"""岗位 ↔ 国家职业分类（Occupation）对齐（设计文档 §5.1 BELONGS_TO_OCCUPATION）。

背景：Occupation 节点已由 scripts/import_occupations.py 导入（三源 O*NET / 人社部 /
LinkedIn），但图谱中无 (Position)-[:BELONGS_TO_OCCUPATION]->(Occupation) 边，
Position.occupation_code 也未写入。本模块在岗位入图（import_jd）与回填
（scripts/align_positions.py）时做对齐，输出 (occupation_code, match_confidence)。

匹配策略（先规则后语义，防误挂）：
1. 规则：position_name 命中 occupations.aliases（忽略大小写精确匹配）或与
   occupation.name 相等 → confidence 1.0
2. 语义兜底：SBERT 与全部 occupation.name 余弦 Top-1 ≥ 0.6 → confidence = 相似度
3. 未命中 → None（不入边，避免误挂）

降级：occupations 不可加载 / SBERT 模型不可用 → 返回 None，不阻塞入图
（语义嵌入耗时且依赖模型，故对齐在入图事务外执行，失败只丢 occupation 边）。

数据源：occupations 默认从 Neo4j 同步加载（PG occupations 表的图谱副本，字段
code/name/aliases 与 import_occupations.py 同步口径一致）。取 Neo4j 而非 PG 是
避免在已运行的异步事件循环内 asyncio.run 绑定 async engine 的 loop 冲突；
occupations_source 可注入（测试替身），进程级 TTL 缓存。
"""

import threading
import time
from typing import Callable, Optional

# 语义命中阈值（SBERT 余弦相似度，[0,1]）：设计文档 §5.1 match_confidence 语义档
_SEMANTIC_THRESHOLD = 0.6
# occupations 缓存 TTL（静态权威库，1 小时足够，避免每次入图都查图）
_OCCUPATIONS_TTL = 3600


class OccupationAligner:
    """Position ↔ Occupation 对齐器（进程级单例，规则优先 + SBERT 语义兜底）。

    embedder / occupations_source 可注入（测试替身）；缺省用全局 SkillEmbedder
    单例与 Neo4j 图谱 Occupation 节点。
    """

    _instance: Optional["OccupationAligner"] = None

    def __init__(
        self,
        embedder: Optional[object] = None,
        occupations_source: Optional[Callable[[], list[dict]]] = None,
    ) -> None:
        self._embedder = embedder
        self._occupations_source = occupations_source
        self._occupations: list[dict] = []
        self._ts: float = 0.0
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "OccupationAligner":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- occupations 加载（进程级 TTL 缓存） ----

    def _load_occupations(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            if self._occupations and now - self._ts < _OCCUPATIONS_TTL:
                return self._occupations
            try:
                if self._occupations_source is not None:
                    rows = self._occupations_source()
                else:
                    from app.core.database import neo4j_driver

                    with neo4j_driver.session() as session:
                        records = session.run(
                            "MATCH (o:Occupation) "
                            "RETURN o.code AS code, o.name AS name, o.aliases AS aliases"
                        )
                        rows = [
                            {
                                "code": r["code"],
                                "name": r["name"],
                                "aliases": list(r["aliases"] or []),
                            }
                            for r in records
                            if r["code"] and r["name"]
                        ]
            except Exception:
                # 图谱不可达：返回已缓存（若有），否则空列表 → 规则/语义均无命中
                rows = self._occupations
            self._occupations = rows
            self._ts = now
            return rows

    # ---- 规则匹配 ----

    def _rule_match(self, position_name: str, occ: dict) -> bool:
        """规则命中：别名/名称精确匹配，或别名作为连续子串完整出现在岗位名中。

        包含匹配限定别名长度 ≥ 3，避免过短别名（如 "Go"）误挂到
        "Go开发工程师" 之外的不相干岗位。
        """
        low = position_name.lower()
        if occ.get("name") == position_name:
            return True
        for alias in occ.get("aliases") or []:
            a = str(alias).strip().lower()
            if not a:
                continue
            if a == low:
                return True
            if len(a) >= 3 and a in low:
                return True
        return False

    # ---- 语义兜底 ----

    def _semantic_best(self, name: str, occupations: list[dict]) -> tuple[str, float] | None:
        """与 occupations.name 余弦 Top-1；模型不可用返回 None（降级仅规则）。"""
        if not occupations:
            return None
        try:
            if self._embedder is not None:
                embedder = self._embedder
            else:
                from app.services.matching.semantic import SkillEmbedder

                embedder = SkillEmbedder.get()
            embedder.warm([o["name"] for o in occupations])
        except Exception:
            return None
        best_code, best_score = None, 0.0
        for occ in occupations:
            try:
                score = embedder.similarity(name, occ["name"])
            except Exception:
                continue
            if score > best_score:
                best_code, best_score = occ["code"], score
        if best_code is None:
            return None
        return best_code, best_score

    # ---- 对外接口 ----

    def align(self, position_name: str) -> tuple[str, float] | None:
        """对齐：规则优先 → 语义兜底（≥0.6）→ 未命中 None（不入边）。

        返回 (occupation_code, match_confidence)。空岗位名/无 occupations 返回 None。
        """
        name = position_name.strip()
        if not name:
            return None
        occupations = self._load_occupations()
        if not occupations:
            return None
        for occ in occupations:
            if self._rule_match(name, occ):
                return occ["code"], 1.0
        hit = self._semantic_best(name, occupations)
        if hit is not None and hit[1] >= _SEMANTIC_THRESHOLD:
            return hit
        return None
