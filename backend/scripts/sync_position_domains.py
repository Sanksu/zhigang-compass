# -*- coding: utf-8 -*-
"""岗位职能域同步（岗位投影 Leiden，08-22 Leiden 质量修复）。

背景：技能共现图的 Leiden 社区对"岗位域"不可用——min_weight 过滤后仅 7%
技能有社区，金融类岗位技能边全 nice（因子 0.2）整域被滤出图外，岗位
community_id 靠稀疏技能社区继承只落进 2-3 个技术大杂烩桶。

本脚本对「岗位-岗位投影图」（共享技能加权，load_position_projection）
跑 Leiden，直接得到岗位职能域，回填 Position 节点属性：
- `domain_id`：`dom_{cluster}`；成员数 < min-cluster-size（默认 3）的微簇
  （跨域桥梁岗/长尾）合并为 `dom_general`
- `domain_name`：域内最高 freq 岗位名（代表岗，前端超节点标签）
- 高频桥梁岗语义指派（PINNED_DOMAIN_ANCHORS）：被 Leiden 撕成单点的
  高频岗（大模型算法工程师/Python开发工程师 等）按锚点岗并入语义域

消费：能力图谱域聚合下钻（GraphNode.domain_id/domain_name 契约字段）。
幂等可重复执行；岗位聚合变化（ETL 后）需重跑。

08-24 补强：
- 孤立岗兜底：投影图只含「共享技能≥2」的岗位对，无合格边的岗位不进图
  （实测 42 岗因此无域）。写回后对仍无域的公开状态岗位统一归 dom_general。
- --llm-name：单次 LLM 调用为各语义簇起短职能域名（如「金融数据分析」），
  替代代表岗名带来的「域名=具体岗位」错位；失败/重名/与成员岗同名均
  回退代表岗名。prompt 属算法核心红线，改动须张恺天 review。

用法：
    uv run python scripts/sync_position_domains.py            # 默认 resolution=1.55, min-cluster-size=3
    uv run python scripts/sync_position_domains.py --resolution 1.45
    uv run python scripts/sync_position_domains.py --llm-name # 语义域名
"""

import argparse
import hashlib
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
# 微簇门禁：成员数 < 该值的簇并入通用域。2 人微簇（如 08-31 前的「系统可靠性」域
# =系统可靠性工程师+TypeScript工程师）是垃圾画像/单条 JD 噪声放大的温床，
# 撑不起一个语义域。08-31 由「仅合并单点」收紧为「<3 全并入」
DEFAULT_MIN_CLUSTER_SIZE = 3
# 单点簇合并域（桥梁岗 Python/大模型/DevOps/网络安全 + 长尾低频岗）
GENERAL_DOMAIN_ID = "dom_general"
GENERAL_DOMAIN_NAME = "通用与其他岗位"
# 高频桥梁岗语义指派（2026-08-31 治理，算法口径变更已知会张恺天）：
# 技能横跨多域的桥梁岗常被 Leiden 撕成小簇落通用域，freq 最高的展示位反而
# 语义缺失（大模型算法工程师 freq=376 全图第 5 却挂「通用与其他岗位」）。
# 在微簇合并前把 pinned 岗并入锚点岗（图内稳定高频岗）所在簇——合流后
# 成员数凑满 min-cluster-size 即可自持成语义域；锚点缺位时跳过并告警。
PINNED_DOMAIN_ANCHORS: dict[str, str] = {
    "大模型算法工程师": "机器视觉算法工程师",  # → 智能算法域
    "Python开发工程师": "Java开发工程师",      # → 后端开发域
    "大数据开发工程师": "Java开发工程师",      # → 后端开发域
    "DevOps工程师": "运维工程师",              # → 系统运维域
    # Go 案例补录（2026-08-31）：RB 期望度惩罚把 Go 推进低度数 AI 碎片簇，
    # 其投影最强邻居全在后端（Java 6.8/后端 6.7/DevOps 6.5），语义无悬念
    "Go开发工程师": "Java开发工程师",          # → 后端开发域
}
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
        with invocation_scope("domain_label"):
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
    # 决策信封（PR4b）：每条域名决策落 shadow 记录（cluster_label），供验收/回放；
    # 落库失败只告警不阻塞命名回写
    _try_persist_domain_records(naming, members_by_key, llm)
    return naming


def _try_persist_domain_records(
    naming: dict[str, str],
    members_by_key: dict[str, list[str]],
    llm,
) -> None:
    """域名决策落 llm_decision_records（status=shadow，best-effort 不阻塞）。

    cluster_label 域命名经 sanitize 硬校验（非空/不重名/不与成员岗同名），
    此处 gate_result=pass、risk_tier=R0（label_cluster 建议类）。
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app.services.llm_decision import (
        DOMAIN_CLUSTER_LABEL,
        STATUS_SHADOW,
        build_record,
        persist_record,
    )

    try:
        primary = (llm._providers or [{}])[0]
        provider = str(primary.get("name") or "")
        model = str(primary.get("model") or "")
    except Exception:
        provider, model = "", ""
    run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    for key, name in sorted(naming.items()):
        record = build_record(
            domain=DOMAIN_CLUSTER_LABEL,
            entity_type="cluster", entity_id=key,
            run_id=f"domain_label:{run_date}",
            input_hash=hashlib.sha256(f"{key}\n{name}".encode("utf-8")).hexdigest(),
            evidence_refs=[{"member_count": len(members_by_key.get(key, []))}],
            provider=provider, model=model,
            structured_output={"cluster": key, "name": name},
            confidence=None,
            gate_result="pass",
            risk_tier="R0",
            status=STATUS_SHADOW,
        )
        try:
            asyncio.run(persist_record(record))
        except Exception as e:
            logger.warning("[domain_label] 决策记录落库失败（不影响命名回写）: %s", e)


def merge_singletons(
    membership: dict[str, int],
    name_map: dict[str, str],
    freq: dict[str, int],
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> dict[str, tuple[str, str]]:
    """Leiden 划分 → 岗位 → (domain_id, domain_name) 映射（纯函数，供单测）。

    成员数 < min_cluster_size 的簇并入通用域：跨域桥梁岗（技能横跨多域，
    被各簇拉扯后独立）与长尾低频岗语义上本就无稳定同域伙伴；2 人微簇
    （08-31 前的「系统可靠性」域）是单条 JD 噪声放大的温床，撑不起语义域。
    多岗域命名 = 域内最高 freq 岗位名（freq 缺失按 0，按 name 稳定排序保证
    确定性）。
    """
    by_cluster: dict[int, list[str]] = {}
    for pid, cid in membership.items():
        by_cluster.setdefault(cid, []).append(pid)

    result: dict[str, tuple[str, str]] = {}
    domain_names: dict[int, str] = {}
    for cid, members in by_cluster.items():
        if len(members) < min_cluster_size:
            for pid in members:
                result[pid] = (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)
            continue
        rep = sorted(members, key=lambda p: (-freq.get(p, 0), name_map.get(p, p)))[0]
        domain_names[cid] = name_map.get(rep, rep)
    for cid, members in by_cluster.items():
        if cid in domain_names:
            for pid in members:
                result[pid] = (f"dom_{cid}", domain_names[cid])
    return result


def apply_domain_pins(
    membership: dict[str, int],
    name_map: dict[str, str],
    pins: dict[str, str] | None = None,
) -> tuple[dict[str, int], list[str]]:
    """高频桥梁岗语义指派（纯函数，供单测）：pinned 岗并入锚点岗所在 Leiden 簇。

    在 merge_singletons 的微簇合并**之前**执行：pinned 岗与锚点簇合流后
    成员数凑满 min-cluster-size，桥梁岗自身的小簇即可自持成语义域
    （如 Python/大数据并入 Java 簇成后端域）。锚点岗或 pinned 岗不在本次
    划分、或二者同岗时跳过并返回告警（调用方记日志），不阻断同步。
    返回 (新 membership, 告警列表)。
    """
    if pins is None:
        pins = PINNED_DOMAIN_ANCHORS
    pid_by_name = {name_map.get(pid, pid): pid for pid in membership}
    warnings: list[str] = []
    for pos, anchor in pins.items():
        if pos == anchor:
            continue
        anchor_pid = pid_by_name.get(anchor)
        pos_pid = pid_by_name.get(pos)
        if pos_pid is None:
            continue  # 不在划分中（无投影边/已下线），由孤立岗兜底阶段处理
        if anchor_pid is None or anchor_pid not in membership:
            warnings.append(f"锚点岗「{anchor}」不在本次划分，{pos} 保持原簇")
            continue
        membership[pos_pid] = membership[anchor_pid]
    return membership, warnings


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


def sync_position_domains(
    resolution: float,
    llm_name: bool = False,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> dict:
    """加载岗位投影 → Leiden → 微簇合并 → 语义指派 → 门禁 → 回填。

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
    membership, pin_warnings = apply_domain_pins(membership, name_map)
    for w in pin_warnings:
        logger.warning("[语义指派] %s", w)
    assign = merge_singletons(membership, name_map, freq, min_cluster_size)
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
    parser.add_argument("--min-cluster-size", type=int, default=DEFAULT_MIN_CLUSTER_SIZE,
                        help=f"小于该成员数的簇并入通用域（默认 {DEFAULT_MIN_CLUSTER_SIZE}）")
    parser.add_argument("--llm-name", action="store_true",
                        help="LLM 语义域名（失败回退代表岗名）")
    args = parser.parse_args()
    sync_position_domains(args.resolution, llm_name=args.llm_name,
                          min_cluster_size=args.min_cluster_size)


if __name__ == "__main__":
    main()
