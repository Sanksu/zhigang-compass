"""社区层级索引同步（图算法优化方案阶段三：层次化提取）。

将 `louvain_hierarchical` 的全部层级写入 Neo4j Community 节点：
- `(:Community {id: comm_{level}_{cluster}, name, modularity, cluster_count, updated_at})`
- `(:Skill)-[:BELONGS_TO_COMMUNITY {level}]->(:Community)` 技能隶属边
- `(:Community {level: n})-[:NESTED_IN]->(:Community {level: n+1})` 层间嵌套边

消费：`GET /graph/algorithms/community-tree` 从 Neo4j 读层级树（dendrogram
树状图 + 层级导航）；参数（resolution/min_weight）与 skill-clusters 端点同源
（configs/graph_algo.yaml），保证实时簇与索引层级一致。

全量重建语义：先 DETACH DELETE 旧 Community（连带隶属/嵌套边）再写入，
幂等可重复执行，参数变更自动收敛。

用法：
    uv run python scripts/sync_communities.py                  # 读 configs/graph_algo.yaml
    uv run python scripts/sync_communities.py --resolution 1.2 --min-weight 2.5
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("sync_communities")

# 单事务写入条数（节点 + 边混合，按语句数分批）
_BATCH_SIZE = 500


def _community_name(members: list[str], name_map: dict[str, str], graph: dict) -> str:
    """社区名称：簇内按共现权重 Top-3 技能拼接（与 postprocess.rule_label 同口径）。"""
    from app.services.graph_algorithms.postprocess import rule_label

    return rule_label(members, name_map, graph)


def _nested_edges(levels: list[dict]) -> list[tuple[int, int, int]]:
    """相邻层嵌套关系：level n 簇 cid → level n+1 包含它的簇 ncid。

    社区嵌套性质：Louvain 聚合保证上级簇成员为下级簇成员并集，故
    下一层中成员 ⊇ 本层簇成员的簇即父簇。
    """
    edges: list[tuple[int, int, int]] = []
    for i in range(len(levels) - 1):
        cur = levels[i]["membership"]
        nxt = levels[i + 1]["membership"]
        cur_members: dict[int, set[str]] = {}
        for sk, cid in cur.items():
            cur_members.setdefault(cid, set()).add(sk)
        nxt_members: dict[int, set[str]] = {}
        for sk, cid in nxt.items():
            nxt_members.setdefault(cid, set()).add(sk)
        for cid, members in cur_members.items():
            for ncid, nmembers in nxt_members.items():
                if members <= nmembers:
                    edges.append((i, cid, ncid))
                    break
    return edges


def sync_communities(resolution: float, min_weight: float) -> dict:
    """全量重建 Neo4j Community 层级索引，返回层级统计。"""
    from app.core.database import neo4j_driver
    from app.services.graph_algorithms.louvain import louvain_hierarchical
    from app.services.graph_algorithms.network import load_skill_cooccurrence

    with neo4j_driver.session() as session:
        graph, name_map = load_skill_cooccurrence(session, min_weight=min_weight)

    hier = louvain_hierarchical(graph, resolution=resolution)
    levels = hier["levels"]
    if not levels:
        logger.warning("空图，无社区层级可写入")
        return {"levels": 0, "communities": 0, "nested_edges": 0}

    # 1. 清空旧索引（连带 BELONGS_TO_COMMUNITY / NESTED_IN 边）
    with neo4j_driver.session() as session:
        session.run("MATCH (c:Community) DETACH DELETE c")
    logger.info("旧 Community 索引已清空")

    # 2. 写入 Community 节点 + BELONGS_TO_COMMUNITY 隶属边（按层分批）
    total_nodes = 0
    with neo4j_driver.session() as session:
        tx_batch: list[tuple[str, dict]] = []
        for lv in levels:
            level, membership = lv["level"], lv["membership"]
            by_cluster: dict[int, list[str]] = {}
            for sk, cid in membership.items():
                by_cluster.setdefault(cid, []).append(sk)
            for cidx, members in sorted(by_cluster.items()):
                cid = f"comm_{level}_{cidx}"
                name = _community_name(members, name_map, graph)
                tx_batch.append((
                    "MERGE (c:Community {id: $cid}) SET c.level = $level, c.name = $name, "
                    "c.modularity = $modularity, c.cluster_count = $cluster_count, c.updated_at = $updated_at",
                    {
                        "cid": cid,
                        "level": level,
                        "name": name,
                        "modularity": lv["modularity"],
                        "cluster_count": len(by_cluster),
                        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                ))
                total_nodes += 1
                for sk in members:
                    tx_batch.append((
                        "MATCH (s:Skill {id: $skill_id}), (c:Community {id: $cid}) "
                        "MERGE (s)-[r:BELONGS_TO_COMMUNITY {level: $level}]->(c)",
                        {"skill_id": sk, "cid": cid, "level": level},
                    ))
                if len(tx_batch) >= _BATCH_SIZE:
                    _flush(session, tx_batch)
                    tx_batch = []
        _flush(session, tx_batch)

    # 3. 写入 NESTED_IN 嵌套边
    nested_edges = _nested_edges(levels)
    with neo4j_driver.session() as session:
        tx_batch = []
        for level, cidx, ncidx in nested_edges:
            tx_batch.append((
                "MATCH (c1:Community {id: $child}), (c2:Community {id: $parent}) "
                "MERGE (c1)-[:NESTED_IN]->(c2)",
                {"child": f"comm_{level}_{cidx}", "parent": f"comm_{level + 1}_{ncidx}"},
            ))
            if len(tx_batch) >= _BATCH_SIZE:
                _flush(session, tx_batch)
                tx_batch = []
        _flush(session, tx_batch)

    stats = {
        "levels": len(levels),
        "communities": total_nodes,
        "nested_edges": len(nested_edges),
        "best_level": hier["best_level"],
        "resolution": resolution,
        "min_weight": min_weight,
    }
    logger.info(
        "Community 索引重建完成：%s 层 / %s 节点 / %s 嵌套边（best_level=%s）",
        stats["levels"], stats["communities"], stats["nested_edges"], stats["best_level"],
    )
    return stats


def _flush(session, tx_batch: list[tuple[str, dict]]) -> None:
    """批量执行写入语句（单事务提交）。"""
    if not tx_batch:
        return
    with session.begin_transaction() as tx:
        for query, params in tx_batch:
            tx.run(query, **params)
    tx_batch.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description="社区层级索引同步（阶段三：层次化提取）")
    parser.add_argument("--resolution", type=float, help="Louvain 分辨率 γ（默认读 configs/graph_algo.yaml）")
    parser.add_argument("--min-weight", type=float, help="共现边权重下限（默认读 configs/graph_algo.yaml）")
    args = parser.parse_args()

    from app.services.graph_algorithms.config import load_graph_algo_config

    cfg = load_graph_algo_config()
    resolution = args.resolution or cfg["resolution"]
    min_weight = args.min_weight or cfg["min_weight"]
    logger.info("参数: resolution=%s min_weight=%s", resolution, min_weight)

    sync_communities(resolution, min_weight)


if __name__ == "__main__":
    main()
