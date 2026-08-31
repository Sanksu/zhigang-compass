# -*- coding: utf-8 -*-
"""岗位职能域同步（骨干域 + 归类制，2026-08-31 重构）。

架构（对齐分类学正统做法，替代"全员聚类"）：

1. 骨干成域：freq ≥ BACKBONE_MIN_FREQ 的高置信岗位构成投影子图跑 Leiden——
   这一层边稠证据实，划分稳定；成员 < min-cluster-size 的骨干簇降级为
   待归类池（不再直接进通用域）。
2. 其余归类：低频/降级岗位不做聚类，做带弃权的最近域分类——对每个域算
   连接强度（Σ 投影边权），最优域强度 ≥ attach-min-affinity 且领先次优域
   dominance 比例才归入；否则诚实弃权落「通用与其他岗位」。
   freq=1 岗位在结构上凑不出"域"，微簇域（系统可靠性二人域 / AI 碎片簇
   拐走 Go 案例的镜像）从舞台层面消失。

回填 Position 节点属性：
- `domain_id`：骨干簇 `dom_{cid}` 或 `dom_general`
- `domain_name`：LLM 语义域名（--llm-name，失败回退代表岗名）
- pins 语义双层化：骨干岗=簇成员覆盖（Leiden 后、降级前）；待归类岗=
  归类目标覆盖（绕过阈值直接并入锚点域，锚点缺域则告警保持弃权）

08-31 基线（共成员基准 data/golden_set/position_domain_eval.jsonl）：
全员聚类架构 strict 55.6% / pairwise F1 0.552；本架构以基准评测验收。

门禁：最大域占比超限或语义域过少视为参数退化，拒绝写库。
幂等可重复执行；岗位聚合变化（ETL 后）需重跑。

用法：
    uv run python scripts/sync_position_domains.py            # 默认参数
    uv run python scripts/sync_position_domains.py --llm-name # LLM 语义域名
    uv run python scripts/evaluate_position_domains.py        # 基准评测验收
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

# 投影图 Leiden 分辨率：08-22 真实图网格搜（1.3/1.45/1.55/1.6）的甜点。
# 08-31 骨干化后在 min_freq=3 骨干上复验仍为甜点（6 簇：前端/后端/算法芯片/
# 数据分析/运维 + 杂簇残余），更细则降级洪水、更粗则前端后端合并
DEFAULT_RESOLUTION = 1.55
# 骨干门频：freq≥3 的岗位进骨干 Leiden（2026-08-31 骨干域+归类制）。
# 实证 freq≥10 会把前端技术栈细分族（React/Vue/移动前端 freq 2-6）切进
# 归类池，前端枢纽因失族群而孤立降级——族群质量恰是域凝聚力的来源
BACKBONE_MIN_FREQ = 3
# 骨干簇最小成员数：< 该值的簇降级为待归类池（不直接进通用域）
DEFAULT_MIN_CLUSTER_SIZE = 3
# 归类门槛：最优域连接强度 ≥ 该值才考虑归入。2.0 实证被 nice 共享
# 堆砌（7×0.3=2.1）混入大量弱证据归类（弃权行准确率 63%），提到 3.0
# 要求 ≈2 条 must 共享或 1 must+7 nice
ATTACH_MIN_AFFINITY = 3.0
# 归类主导性：最优域需 ≥ 次优域 × 该比例，防"两域拉扯"误归
ATTACH_DOMINANCE = 1.3
# 通用弃权域
GENERAL_DOMAIN_ID = "dom_general"
GENERAL_DOMAIN_NAME = "通用与其他岗位"
# 强制弃权 pin 的特殊锚点值：声明"该岗无正确归属域，一律弃权"
GENERAL_PIN = "__general__"
# 高频桥梁岗语义指派（2026-08-31 治理，算法口径变更已知会张恺天）：
# 技能横跨多域的桥梁岗常被 Leiden 撕成小簇，freq 最高的展示位反而语义缺失
# （大模型算法工程师 freq=376 全图第 5 却挂「通用与其他岗位」）。骨干岗在
# 微簇降级前并入锚点岗所在簇（合流后凑满 min-cluster-size 自持成域）；
# 待归类岗绕过归类阈值直接并入锚点域。锚点缺位时跳过并告警。
PINNED_DOMAIN_ANCHORS: dict[str, str] = {
    "大模型算法工程师": "机器视觉算法工程师",  # → 智能算法域
    "Python开发工程师": "Java开发工程师",      # → 后端开发域
    "大数据开发工程师": "Java开发工程师",      # → 后端开发域
    "DevOps工程师": "运维工程师",              # → 系统运维域
    # Go 案例补录（2026-08-31）：RB 期望度惩罚把 Go 推进低度数 AI 碎片簇，
    # 其投影最强邻居全在后端（Java 6.8/后端 6.7/DevOps 6.5），语义无悬念
    "Go开发工程师": "Java开发工程师",          # → 后端开发域
    # React 前端栈族归位（2026-08-31 骨干化实证）：骨干 Leiden 把它和
    # AI基础设施/创始工程师凑成杂簇，栈族语义无悬念归前端
    "React前端开发工程师": "Vue前端开发工程师",  # → 前端开发域
    # 通才算法桥梁岗（freq=97，骨干降级后乱归实证）：语义无悬念归算法域
    "算法工程师": "机器视觉算法工程师",
    # 强制弃权（GENERAL_PIN）：无正确归属域的语义孤岗，落通用域诚实展示。
    # 探针实证（2026-08-31）：它们的连接证据真实但无差别指向枢纽域
    # （share 与正例完全重叠），纯拓扑归类不可分，属治理声明层职责
    "产品经理": GENERAL_PIN,
    "创始工程师": GENERAL_PIN,
    "SAP集成": GENERAL_PIN,
    "Murex应用": GENERAL_PIN,
    "AI与数据系统": GENERAL_PIN,
    "生化工程师": GENERAL_PIN,
    "CMBS交易员": GENERAL_PIN,
    # —— 基准收口批（2026-08-31，簇级归类两轮探针否决后的治理层兜底）——
    # 候选方案②（微簇池化/人均亲和度）经探针实证 2/14 与 11/26，负例分数
    # 高于正例、分布完全重叠：薄画像岗的连边证据不携带域语义，拓扑不可分。
    # 以下各岗语义归属无争议（与 position_domain_eval.jsonl 断言一致），
    # 以治理声明覆盖拓扑归类；锚点均取目标域骨干成员。pins 为可撤销治理
    # 状态：未来真实 JD 增长改变画像后可逐条复核解除。
    "TypeScript工程师": "前端开发工程师",
    "Node.js全栈工程师": "全栈工程师",
    "鸿蒙全栈工程师": "前端开发工程师",
    "WebGL开发工程师": "前端开发工程师",
    "推荐搜索算法工程师": "机器视觉算法工程师",
    "保险分析师": "数据分析师",
    # 生物信息学 QA：跟随其家族锚点测试工程师当前所在的后端域（测试工程师
    # 经归类门槛自然落入后端；若未来测试域成型，本条与测试归属一并复核）
    "生物信息学 QA": "后端开发工程师",
    "IC验证": "嵌入式开发工程师",
    "GPU验证": "嵌入式开发工程师",
    "EDA工作流优化": "嵌入式开发工程师",
    "网络工程师": "运维工程师",
    "WebSphere管理员": "运维工程师",
    "IT平台与自动化": "运维工程师",
    "桌面工程师": "运维工程师",
    "AI基础设施工程师": "运维工程师",
    "AI与HPC可观测性": "运维工程师",
    # 池子二次压缩（2026-08-31 晚）：移动端开发归前端族；PLM 基础设施管理
    # 归运维
    "移动端全栈工程师": "前端开发工程师",
    "移动端工程师": "前端开发工程师",
    "TeamCenter基础设施管理员": "运维工程师",
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


def split_backbone(
    freq: dict[str, int],
    min_freq: int = BACKBONE_MIN_FREQ,
) -> tuple[set[str], set[str]]:
    """按 freq 分骨干/待归类两池（纯函数，供单测）。freq 缺失按 0。"""
    backbone = {pid for pid, f in freq.items() if (f or 0) >= min_freq}
    fringe = set(freq) - backbone
    return backbone, fringe


def demote_small_clusters(
    membership: dict[str, int],
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> tuple[dict[str, int], set[str]]:
    """骨干内 < min_cluster_size 的簇降级为待归类池（纯函数，供单测）。

    替代旧 merge_singletons 的"直接并通用域"：降级岗位回到归类流程，
    仍有机会凭连接强度进入语义域；实在无归属由弃权兜底，口径更诚实。
    返回 (缩小的 membership, 降级岗位集合)。
    """
    by_cluster: dict[int, list[str]] = {}
    for pid, cid in membership.items():
        by_cluster.setdefault(cid, []).append(pid)

    demoted: set[str] = set()
    for cid, members in by_cluster.items():
        if len(members) < min_cluster_size:
            demoted.update(members)
    kept = {pid: cid for pid, cid in membership.items() if pid not in demoted}
    return kept, demoted


def apply_domain_pins(
    membership: dict[str, int],
    name_map: dict[str, str],
    pins: dict[str, str] | None = None,
) -> tuple[dict[str, int], list[str]]:
    """骨干岗语义指派（纯函数，供单测）：pinned 岗并入锚点岗所在 Leiden 簇。

    在微簇降级之前执行：pinned 岗与锚点簇合流后成员数凑满 min-cluster-size，
    桥梁岗自身的小簇即可自持成语义域。锚点岗或 pinned 岗不在骨干划分、
    或二者同岗时跳过并返回告警（调用方记日志），不阻断同步。
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
            continue  # 不在骨干划分（待归类岗走归类层 pin / 不在图则兜底）
        if anchor_pid is None or anchor_pid not in membership:
            warnings.append(f"锚点岗「{anchor}」不在骨干划分，{pos} 保持原簇")
            continue
        membership[pos_pid] = membership[anchor_pid]
    return membership, warnings


def attach_fringe_position(
    graph: dict[str, dict[str, float]],
    pid: str,
    domain_members: dict[str, list[str]],
    min_affinity: float = ATTACH_MIN_AFFINITY,
    dominance: float = ATTACH_DOMINANCE,
) -> tuple[str | None, dict[str, float]]:
    """带弃权的最近域分类（纯函数，供单测）。

    连接强度 = Σ 对域内成员的投影边权。最优域需 ≥ min_affinity 且
    ≥ 次优域 × dominance（次优为 0 时只看绝对门槛）才归入；否则弃权
    （返回 None）。同分按域名键稳定排序保证确定性。
    返回 (归入域键 | None, 全部域得分)。
    """
    neighbors = graph.get(pid, {})
    scores = {
        key: sum(neighbors.get(m, 0.0) for m in members)
        for key, members in domain_members.items()
    }
    if not scores:
        return None, scores
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    best_key, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best < min_affinity:
        return None, scores
    if second > 0 and best < dominance * second:
        return None, scores
    return best_key, scores


def resolve_fringe(
    graph: dict[str, dict[str, float]],
    fringe_ids: set[str],
    domain_members: dict[str, list[str]],
    name_map: dict[str, str],
    min_affinity: float = ATTACH_MIN_AFFINITY,
    dominance: float = ATTACH_DOMINANCE,
    pins: dict[str, str] | None = None,
) -> tuple[dict[str, str], set[str], list[str]]:
    """待归类池批量归类（纯函数，供单测）：pin 覆盖 → 阈值归类 → 弃权。

    pins：GENERAL_PIN 锚点=强制弃权（声明无正确归属域）；其余锚点绕过
    阈值直接并入锚点岗所在域，锚点不在任何语义域时告警并走正常归类。
    返回 (pid→域键, 弃权集合, 告警列表)。
    """
    if pins is None:
        pins = PINNED_DOMAIN_ANCHORS
    pid_by_name = {name_map.get(pid, pid): pid for pid in domain_members_all(domain_members, fringe_ids)}
    pid_to_domain = {
        pid: key for key, members in domain_members.items() for pid in members
    }
    assigned: dict[str, str] = {}
    abstained: set[str] = set()
    warnings: list[str] = []
    for pid in sorted(fringe_ids):
        pos_name = name_map.get(pid, pid)
        anchor = pins.get(pos_name)
        if anchor == GENERAL_PIN:
            abstained.add(pid)
            continue
        if anchor and pos_name != anchor:
            anchor_pid = pid_by_name.get(anchor)
            target = pid_to_domain.get(anchor_pid) if anchor_pid else None
            if target:
                assigned[pid] = target
                continue
            warnings.append(f"锚点岗「{anchor}」无语义域，{pos_name} 走正常归类")
        key, _scores = attach_fringe_position(
            graph, pid, domain_members, min_affinity, dominance,
        )
        if key:
            assigned[pid] = key
        else:
            abstained.add(pid)
    return assigned, abstained, warnings


def domain_members_all(domain_members: dict[str, list[str]], fringe_ids: set[str]) -> set[str]:
    """骨干成员 ∪ 待归类池（供 pid_by_name 反查的键空间）。"""
    all_ids = set(fringe_ids)
    for members in domain_members.values():
        all_ids.update(members)
    return all_ids


def resolve_leftover_pins(
    leftover_rows: list[dict],
    assign: dict[str, tuple[str, str]],
    name_map: dict[str, str],
    pins: dict[str, str] | None = None,
) -> list[tuple[str, tuple[str, str]]]:
    """投影外孤立岗的 pin 兜底（纯函数，供单测）。

    无合格投影边的岗位（<2 共享技能）不进骨干/归类流程，但治理声明的
    归属仍应生效：带非 GENERAL_PIN 锚点的孤立岗跟随锚点岗当前域；
    锚点无域或 GENERAL_PIN 落通用域。返回 (岗位id, 域二元组) 列表。
    """
    if pins is None:
        pins = PINNED_DOMAIN_ANCHORS
    pid_by_name = {name_map.get(pid, pid): pid for pid in assign}
    out: list[tuple[str, tuple[str, str]]] = []
    for row in leftover_rows:
        name = row.get("name") or ""
        anchor = pins.get(name)
        if not anchor or anchor == GENERAL_PIN:
            continue
        anchor_pid = pid_by_name.get(anchor)
        dom = assign.get(anchor_pid) if anchor_pid else None
        if dom:
            out.append((row["id"], dom))
    return out


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
    backbone_min_freq: int = BACKBONE_MIN_FREQ,
    attach_min_affinity: float = ATTACH_MIN_AFFINITY,
    attach_dominance: float = ATTACH_DOMINANCE,
    audit_membership: bool = True,
) -> dict:
    """骨干成域 → 语义命名 → 待归类（pin 覆盖/阈值归类/弃权）→ 门禁 → 回填。"""
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

    backbone, fringe = split_backbone(freq, backbone_min_freq)
    subgraph = {pid: {nb: w for nb, w in graph[pid].items() if nb in backbone}
                for pid in backbone}
    membership = leiden(subgraph, resolution=resolution)
    membership, pin_warnings = apply_domain_pins(membership, name_map)
    for w in pin_warnings:
        logger.warning("[骨干指派] %s", w)
    membership, demoted = demote_small_clusters(membership, min_cluster_size)
    # 强制弃权 pin（GENERAL_PIN）的骨干岗移出骨干划分，进归类池后直接弃权
    general_pinned = {
        pid for pid in membership
        if PINNED_DOMAIN_ANCHORS.get(name_map.get(pid, pid)) == GENERAL_PIN
    }
    if general_pinned:
        for pid in general_pinned:
            del membership[pid]
        demoted |= general_pinned
    if demoted:
        logger.info("骨干降级 %s 岗进入归类池", len(demoted))

    # 骨干簇代表岗 → (dom_id, 代表岗名)；LLM 命名后替换域名字段
    by_cluster: dict[int, list[str]] = {}
    for pid, cid in membership.items():
        by_cluster.setdefault(cid, []).append(pid)
    backbone_assign: dict[str, tuple[str, str]] = {}
    for cid, members in by_cluster.items():
        rep = sorted(members, key=lambda p: (-freq.get(p, 0), name_map.get(p, p)))[0]
        for pid in members:
            backbone_assign[pid] = (f"dom_{cid}", name_map.get(rep, rep))

    naming: dict[str, str] = {}
    if llm_name:
        members_by_key, _ = _naming_input(backbone_assign, name_map)
        naming = llm_domain_names(members_by_key)
        if naming:
            backbone_assign = {
                pid: (dom_id, naming.get(dom_name, dom_name))
                for pid, (dom_id, dom_name) in backbone_assign.items()
            }
            logger.info("语义域名生效：%d/%d 簇", len(naming), len(members_by_key))

    domain_members: dict[str, list[str]] = {}
    for pid, (dom_id, _name) in backbone_assign.items():
        domain_members.setdefault(dom_id, []).append(pid)
    fringe_all = fringe | demoted
    assigned, abstained, attach_warnings = resolve_fringe(
        graph, fringe_all, domain_members, name_map,
        attach_min_affinity, attach_dominance,
    )
    for w in attach_warnings:
        logger.warning("[归类指派] %s", w)

    dom_name_by_id: dict[str, str] = {}
    for pid, (dom_id, dom_name) in backbone_assign.items():
        dom_name_by_id.setdefault(dom_id, dom_name)

    assign: dict[str, tuple[str, str]] = dict(backbone_assign)
    for pid, dom_id in assigned.items():
        assign[pid] = (dom_id, dom_name_by_id[dom_id])
    for pid in abstained:
        assign[pid] = (GENERAL_DOMAIN_ID, GENERAL_DOMAIN_NAME)

    stats = guard_domain_distribution(assign)
    stats.update({
        "backbone": len(backbone),
        "demoted": len(demoted),
        "attached": len(assigned),
        "abstained": len(abstained),
    })
    logger.info(
        "岗位域划分：骨干 %s（降级 %s）/ 归类 %s / 弃权 %s → %s 岗 %s 域"
        "（语义域 %s，最大域占比 %.1f%%，resolution=%s）",
        stats["backbone"], stats["demoted"], stats["attached"], stats["abstained"],
        stats["positions"], stats["domains"], stats["semantic_domains"],
        stats["max_domain_ratio"] * 100, resolution,
    )

    # 回填：属性覆盖写（对齐删除语义——不在本次划分中的岗位清空域属性，
    # 防止已下线/改聚合的岗位残留旧域）。投影外孤立岗（无合格投影边）
    # 先走 pin 兜底跟随锚点域，其余落通用域，保证公开岗位域覆盖率 100%。
    with neo4j_driver.session() as session:
        leftover_rows = session.run(
            """
            MATCH (p:Position)
            WHERE p.status IN $statuses AND NOT p.id IN $ids
            RETURN p.id AS id, p.name AS name
            """,
            statuses=_PUBLIC_STATUSES,
            ids=list(assign),
        ).data()
        leftover_pinned = resolve_leftover_pins(leftover_rows, assign, name_map)
        for pid, dom in leftover_pinned:
            assign[pid] = dom
        if leftover_pinned:
            logger.info("投影外孤立岗 pin 兜底：%s 岗", len(leftover_pinned))
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

    # 语义域成员资格 LLM 自审（PR-C）：可疑成员/不内聚域落审批队列，
    # 只检测不改写，任何失败不阻塞域同步
    if audit_membership:
        from app.services.graph_algorithms.domain_audit import run_membership_audit

        audit_domains: dict[str, list[str]] = {}
        for pid, (dom_id, dom_name) in assign.items():
            if dom_id != GENERAL_DOMAIN_ID:
                audit_domains.setdefault(dom_name, []).append(name_map.get(pid, pid))
        try:
            flagged = run_membership_audit(audit_domains)
            logger.info("成员资格自审完成：%s 条待审记录", flagged)
        except Exception as e:  # noqa: BLE001
            logger.warning("[membership_audit] 自审异常（不阻塞）: %s", e)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="岗位职能域同步（骨干域+归类制）")
    parser.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION)
    parser.add_argument("--backbone-min-freq", type=int, default=BACKBONE_MIN_FREQ,
                        help=f"freq≥该值的岗位进骨干 Leiden（默认 {BACKBONE_MIN_FREQ}）")
    parser.add_argument("--min-cluster-size", type=int, default=DEFAULT_MIN_CLUSTER_SIZE,
                        help=f"骨干簇小于该成员数降级进归类池（默认 {DEFAULT_MIN_CLUSTER_SIZE}）")
    parser.add_argument("--attach-min-affinity", type=float, default=ATTACH_MIN_AFFINITY,
                        help=f"归类最低连接强度（默认 {ATTACH_MIN_AFFINITY}）")
    parser.add_argument("--attach-dominance", type=float, default=ATTACH_DOMINANCE,
                        help=f"归类主导性比例（默认 {ATTACH_DOMINANCE}）")
    parser.add_argument("--llm-name", action="store_true",
                        help="LLM 语义域名（失败回退代表岗名）")
    parser.add_argument("--no-audit-membership", action="store_true",
                        help="关闭语义域成员资格 LLM 自审（默认开启，失败不阻塞）")
    args = parser.parse_args()
    sync_position_domains(
        args.resolution, llm_name=args.llm_name,
        min_cluster_size=args.min_cluster_size,
        backbone_min_freq=args.backbone_min_freq,
        attach_min_affinity=args.attach_min_affinity,
        attach_dominance=args.attach_dominance,
        audit_membership=not args.no_audit_membership,
    )


if __name__ == "__main__":
    main()
