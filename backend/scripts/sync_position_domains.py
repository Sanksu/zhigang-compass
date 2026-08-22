# -*- coding: utf-8 -*-
"""岗位职能域同步（岗位投影 Leiden，08-22 Leiden 质量修复）。

背景：技能共现图的 Leiden 社区对"岗位域"不可用——min_weight 过滤后仅 7%
技能有社区，金融类岗位技能边全 nice（因子 0.2）整域被滤出图外，岗位
community_id 靠稀疏技能社区继承只落进 2-3 个技术大杂烩桶。

本脚本对「岗位-岗位投影图」（共享技能加权，load_position_projection）
跑 Leiden，直接得到岗位职能域，回填 Position 节点属性：
- `domain_id`：`dom_{cluster}`；单点簇（跨域桥梁岗/长尾）合并为 `dom_general`
- `domain_name`：域内最高 freq 岗位名（代表岗，前端超节点标签）

消费：能力图谱域聚合下钻（GraphNode.domain_id/domain_name 契约字段）。
幂等可重复执行；岗位聚合变化（ETL 后）需重跑。

用法：
    uv run python scripts/sync_position_domains.py            # 默认 resolution=1.55
    uv run python scripts/sync_position_domains.py --resolution 1.45
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("sync_position_domains")

# 投影图 Leiden 分辨率：08-22 真实图网格搜（1.3/1.45/1.55/1.6）的甜点——
# 1.55 时金融/数据分析 19 岗成簇（投资/精算/策略/成本/信贷全聚齐）、语音算法
# 归算法域、前端/硬件/教育各自成簇共 12 个语义簇；1.6 过细（Python/大模型/
# DevOps 桥梁岗被撕成单点），1.3 偏粗（金融混入 IT 系统管理）
DEFAULT_RESOLUTION = 1.55
# 单点簇合并域（桥梁岗 Python/大模型/DevOps/网络安全 + 长尾低频岗）
GENERAL_DOMAIN_ID = "dom_general"
GENERAL_DOMAIN_NAME = "通用与其他岗位"
# 门禁：最大域占比超限或语义域过少视为参数退化，拒绝写库
_MAX_DOMAIN_RATIO = 0.5
_MIN_SEMANTIC_DOMAINS = 5


def merge_singletons(
    membership: dict[str, int],
    name_map: dict[str, str],
    freq: dict[str, int],
) -> dict[str, tuple[str, str]]:
    """Leiden 划分 → 岗位 → (domain_id, domain_name) 映射（纯函数，供单测）。

    单点簇合并为通用域：跨域桥梁岗（技能横跨多域，被各簇拉扯后独立）与
    长尾低频岗语义上本就无稳定同域伙伴。多岗域命名 = 域内最高 freq 岗位名
    （freq 缺失按 0，按 name 稳定排序保证确定性）。
    """
    by_cluster: dict[int, list[str]] = {}
    for pid, cid in membership.items():
        by_cluster.setdefault(cid, []).append(pid)

    result: dict[str, tuple[str, str]] = {}
    domain_names: dict[int, str] = {}
    for cid, members in by_cluster.items():
        if len(members) == 1:
            result[members[0]] = (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
            continue
        rep = sorted(members, key=lambda p: (-freq.get(p, 0), name_map.get(p, p)))[0]
        domain_names[cid] = name_map.get(rep, rep)
    for cid, members in by_cluster.items():
        if cid in domain_names:
            for pid in members:
                result[pid] = (f"dom_{cid}", domain_names[cid])
    return result


def guard_domain_distribution(assign: dict[str, tuple[str, str]]) -> dict:
    """写库前门禁（与 guard_community_distribution 同模式）：参数退化拒绝写库。

    退化形态：最大域占比 > 50%（单簇吞并，分辨率过低）；语义域（非通用桶）
    数 < 5（分辨率过高把域撕碎或图过小）。
    """
    counts: dict[str, int] = {}
    for dom_id, _ in assign.values():
        counts[dom_id] = counts.get(dom_id, 0) + 1
    total = len(assign)
    semantic = [c for d, c in counts.items() if d != GENERAL_DOMAIN_ID]
    max_ratio = max(counts.values()) / total if total else 1.0
    stats = {
        "positions": total,
        "domains": len(counts),
        "semantic_domains": len(semantic),
        "max_domain_ratio": round(max_ratio, 4),
    }
    if max_ratio > _MAX_DOMAIN_RATIO:
        raise ValueError(f"最大域占比 {max_ratio:.2f} > {_MAX_DOMAIN_RATIO}，疑似分辨率过低单簇吞并")
    if len(semantic) < _MIN_SEMANTIC_DOMAINS:
        raise ValueError(f"语义域数 {len(semantic)} < {_MIN_SEMANTIC_DOMAINS}，疑似分辨率过高或图过小")
    return stats


def sync_position_domains(resolution: float) -> dict:
    """加载岗位投影 → Leiden → 门禁 → 回填 Position.domain_id/domain_name。"""
    from app.core.database import neo4j_driver
    from app.services.graph_algorithms.leiden import leiden
    from app.services.graph_algorithms.network import load_position_projection

    with neo4j_driver.session() as session:
        graph, name_map = load_position_projection(session)
        if not graph:
            raise ValueError("岗位投影图为空（Neo4j 不可达或无共享技能岗位对）")
        freq_rows = session.run(
            "MATCH (p:Position) WHERE p.id IN $ids RETURN p.id AS id, coalesce(p.freq, 0) AS f",
            ids=list(graph),
        ).data()
        freq = {r["id"]: int(r["f"] or 0) for r in freq_rows}

    membership = leiden(graph, resolution=resolution)
    assign = merge_singletons(membership, name_map, freq)
    stats = guard_domain_distribution(assign)
    logger.info(
        "岗位域划分：%s 岗 / %s 域（语义域 %s，最大域占比 %.1f%%，resolution=%s）",
        stats["positions"], stats["domains"], stats["semantic_domains"],
        stats["max_domain_ratio"] * 100, resolution,
    )

    # 回填：属性覆盖写（对齐删除语义——不在本次划分中的岗位清空域属性，
    # 防止已下线/改聚合的岗位残留旧域）
    with neo4j_driver.session() as session:
        session.run(
            """
            MATCH (p:Position)
            WHERE p.domain_id IS NOT NULL AND NOT p.id IN $ids
            SET p.domain_id = null, p.domain_name = null
            """,
            ids=list(assign),
        )
        session.run(
            """
            UNWIND $rows AS row
            MATCH (p:Position {id: row.id})
            SET p.domain_id = row.dom_id, p.domain_name = row.dom_name
            """,
            rows=[
                {"id": pid, "dom_id": dom_id, "dom_name": dom_name}
                for pid, (dom_id, dom_name) in assign.items()
            ],
        )
    logger.info("Position.domain_id/domain_name 回填完成：%s 岗", len(assign))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="岗位职能域同步（岗位投影 Leiden）")
    parser.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION)
    args = parser.parse_args()
    sync_position_domains(args.resolution)


if __name__ == "__main__":
    main()
