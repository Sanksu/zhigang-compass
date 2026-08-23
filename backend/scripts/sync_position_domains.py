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

08-24 补强：
- 孤立岗兜底：投影图只含「共享技能≥2」的岗位对，无合格边的岗位不进图
  （实测 42 岗因此无域）。写回后对仍无域的公开状态岗位统一归 dom_general。
- --llm-name：单次 LLM 调用为各语义簇起短职能域名（如「金融数据分析」），
  替代代表岗名带来的「域名=具体岗位」错位；失败/重名/与成员岗同名均
  回退代表岗名。prompt 属算法核心红线，改动须张恺天 review。

用法：
    uv run python scripts/sync_position_domains.py            # 默认 resolution=1.55
    uv run python scripts/sync_position_domains.py --resolution 1.45
    uv run python scripts/sync_position_domains.py --llm-name # 语义域名
"""

import argparse
import sys
from pathlib import Path

from pydantic import BaseModel, Field

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

# 公开岗位状态（与 load_position_projection 查询口径一致）
_PUBLIC_STATUSES = ["active", "emerging", "stable", "declining"]


class _DomainNameItem(BaseModel):
    """LLM 域命名单项（幻觉防控第一道防线：schema 强校验）。"""

    cluster: str = Field(description="输入的簇代表岗名，原样回传作为对齐键")
    name: str = Field(min_length=2, max_length=10, description="2~10 字职能域名")


class _DomainNamePlan(BaseModel):
    domains: list[_DomainNameItem] = Field(default_factory=list)


def sanitize_llm_names(
    items: list[_DomainNameItem],
    cluster_keys: set[str],
    member_names: dict[str, set[str]],
) -> dict[str, str]:
    """LLM 命名 → 簇键→域名映射（纯函数）。

    校验：簇键必须存在且原样回传；域名去空白后须非空、不与其他域重名
    （后者回退代表岗名）、不得与任何成员岗位名相同（防"命名=照抄岗位"）。
    """
    used: set[str] = set()
    result: dict[str, str] = {}
    for item in items:
        key = item.cluster.strip()
        name = item.name.strip()
        if key not in cluster_keys or not name or name in used:
            continue
        if any(name == m for m in member_names.get(key, ())):
            continue
        result[key] = name
        used.add(name)
    return result


def _naming_input(assign: dict[str, tuple[str, str]],
                  name_map: dict[str, str]) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """划分结果 → LLM 输入（键=代表岗名 → 成员岗名列表）。"""
    members_by_key: dict[str, list[str]] = {}
    for pid, (dom_id, dom_name) in assign.items():
        if dom_id != GENERAL_DOMAIN_ID:
            members_by_key.setdefault(dom_name, []).append(name_map.get(pid, pid))
    for key in members_by_key:
        members_by_key[key].sort()
    member_names = {k: set(v) | {k} for k, v in members_by_key.items()}
    return members_by_key, member_names


_DOMAIN_NAME_PROMPT = """你是招聘技能图谱的岗位职能域命名助手。下面是若干岗位聚类，
每个聚类列出成员岗位名。为每个聚类起一个简短的中文「职能域」名。

要求：
1. 域名表达职能领域（如：金融数据分析、前端开发、机器视觉算法），不是具体岗位名
2. 2~10 个汉字；不带「工程师/岗」等后缀；各域名互不相同
3. cluster 字段必须原样回传输入的代表岗名（对齐键，不得改写）

{clusters_json}"""


def llm_domain_names(members_by_key: dict[str, list[str]]) -> dict[str, str]:
    """单次 LLM 调用为全部语义簇命名；任何失败返回 {}（调用方回退代表岗名）。"""
    import json as _json

    from app.services.extraction.llm_invocation import invocation_scope
    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        LLMExtractionError,
        LLMProviderChain,
    )

    clusters_payload = [
        {"cluster": key, "members": names}
        for key, names in sorted(members_by_key.items())
    ]
    prompt = _DOMAIN_NAME_PROMPT.format(
        clusters_json=_json.dumps(clusters_payload, ensure_ascii=False, indent=1),
    )
    try:
        llm = LLMProviderChain()
        with invocation_scope():
            plan = llm.extract_structured(
                prompt, _DomainNamePlan,
                system_prompt="你是严谨的岗位 taxonomy 标注员，严格按 JSON schema 输出。",
                timeout=30,
            )
    except LLMConfigurationError as e:
        logger.warning("LLM 未配置，域命名回退代表岗名：%s", e)
        return {}
    except LLMExtractionError as e:
        logger.warning("LLM 域命名失败，全部回退代表岗名：%s", e)
        return {}

    member_names = {k: set(v) | {k} for k, v in members_by_key.items()}
    naming = sanitize_llm_names(plan.domains, set(members_by_key), member_names)
    for key in members_by_key:
        if key not in naming:
            logger.info("簇「%s」未获有效命名，回退代表岗名", key)
    return naming


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


def sync_position_domains(resolution: float, llm_name: bool = False) -> dict:
    """加载岗位投影 → Leiden → 门禁 → 回填 Position.domain_id/domain_name。

    llm_name=True 时先经单次 LLM 调用生成语义域名（失败回退代表岗名）。
    """
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

    if llm_name:
        members_by_key, _ = _naming_input(assign, name_map)
        naming = llm_domain_names(members_by_key)
        if naming:
            assign = {
                pid: (dom_id, naming.get(dom_name, dom_name))
                for pid, (dom_id, dom_name) in assign.items()
            }
            logger.info("语义域名生效：%d/%d 簇", len(naming), len(members_by_key))

    logger.info(
        "岗位域划分：%s 岗 / %s 域（语义域 %s，最大域占比 %.1f%%，resolution=%s）",
        stats["positions"], stats["domains"], stats["semantic_domains"],
        stats["max_domain_ratio"] * 100, resolution,
    )

    # 回填：属性覆盖写（对齐删除语义——不在本次划分中的岗位清空域属性，
    # 防止已下线/改聚合的岗位残留旧域；随后对无合格投影边的公开岗位兜底
    # 归入通用域，保证公开岗位域覆盖率 100%）
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
        leftover = session.run(
            """
            MATCH (p:Position)
            WHERE p.domain_id IS NULL AND p.status IN $statuses
            RETURN count(p) AS n
            """,
            statuses=_PUBLIC_STATUSES,
        ).single()["n"]
        if leftover:
            session.run(
                """
                MATCH (p:Position)
                WHERE p.domain_id IS NULL AND p.status IN $statuses
                SET p.domain_id = $gid, p.domain_name = $gname
                """,
                statuses=_PUBLIC_STATUSES,
                gid=GENERAL_DOMAIN_ID, gname=GENERAL_DOMAIN_NAME,
            )
            logger.info("孤立岗兜底：%d 岗归 %s", leftover, GENERAL_DOMAIN_ID)
    logger.info("Position.domain_id/domain_name 回填完成：%s 岗", len(assign))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="岗位职能域同步（岗位投影 Leiden）")
    parser.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION)
    parser.add_argument("--llm-name", action="store_true",
                        help="LLM 语义域名（失败回退代表岗名）")
    args = parser.parse_args()
    sync_position_domains(args.resolution, llm_name=args.llm_name)


if __name__ == "__main__":
    main()
