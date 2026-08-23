"""技能字典自治守卫（dict-guard）评估服务：Schema / Prompt / 硬门禁 / 分级。

职责边界（对齐 cluster_llm 模式——纯逻辑在本模块，IO 编排在 workers/dict_guard.py）：
1. ``DictGuardDecision``：LLM 输出 JSON Schema（Pydantic，兼作 instructor 强校验）
2. ``build_decision_prompt``：单候选评估输入（词条 + 证据 + 动作说明）
3. ``hard_gate``：写入侧硬门禁（先于分级，一票否决）
4. ``tier_for``：分级裁决（auto / proposal / skip）

红线（AGENTS.md §4.1）：本模块的 prompt 与门禁/分级规则属算法核心，
变更须算法岗张恺天 review。设计原则对齐《岗位名LLM审查设计方案》：
LLM 增强 + 规则优先 + 门控 + 失败降级；高风险变更不自动写规则库。
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.core import runtime_config
from app.services.extraction.dictionary import (
    SKILL_STOPWORDS,
    SKILL_WHITELIST,
    SOFT_SKILL_WHITELIST,
    TOOL_ALIAS,
    _ALIAS_STANDARDS,
    _POSITION_WHITELIST,
    normalize_position_name,
)
from app.services.extraction.dynamic_filters import is_dynamically_blocked

# 治理对象类型：skill=技能字典；position=图谱岗位节点；course=课程脏边或孤立课程节点
EntityType = Literal["skill", "position", "course"]

# 课程脏边初筛阈值（与 scripts/graph_health_cleanup 一致）：SEVERE 以下为严重脏
_EDGE_SEVERE = 0.30
_CJK = __import__("re").compile(r"[\u4e00-\u9fff]")


class DictGuardDecision(BaseModel):
    """LLM 对单个候选词条／图谱实体的裁决（instructor JSON Schema 约束）。

    entity_type 区分治理对象；skill 沿用字典动作，position/course 为图谱删除动作：
    - remove_node=删除岗位脏节点 / 删除孤立脏课程节点
    - remove_edge=删除课程脏边（LEARNABLE_VIA 误配，不删课程节点）
    """

    entity_type: EntityType = Field(default="skill", description="治理对象类型: skill/position/course")
    action: Literal[
        "add_stopword", "remove_stopword", "protect_whitelist", "remove_node", "remove_edge"
    ] = Field(
        description=(
            "skill: add_stopword=加入停用词拦噪音；remove_stopword=从停用词移除解误杀；"
            "protect_whitelist=保留停用词但为受影响真实技能加保护。"
            "position/course: remove_node=删除脏岗位/孤立脏课程节点；remove_edge=删除课程脏边"
        )
    )
    term: str = Field(
        description=(
            "动作作用的目标词或节点名：skill=停用词动作填词、保护动作填受影响技能名；"
            "position=岗位节点名；course remove_node=课程节点名；course remove_edge=『技能名→课程名』"
        )
    )
    reason: str = Field(default="", description="判定理由（一句话）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度 0~1")


_DECISION_PROMPT_TEMPLATE = """你是图数据库治理助手。招聘数据图谱的每日巡检产出了一个候选（技能字典词条或图谱节点/边），请判断是否需要治理。
候选对象：{term}
候选类型：{kind}
治理对象类型：{entity_type}
证据：
{evidence}

可选动作说明：
- skill add_stopword：该词不是真实技能（噪音/泛词/岗位碎片/经验短语），加入停用词拦截
- skill remove_stopword：该词是现行停用词但属误杀的真实技能，应从停用词移除
- skill protect_whitelist：该词本身该拦，但它（或包含它的真实技能名）被误伤，应为具体技能名加保护
- position remove_node：该岗位节点是脏节点（非岗位名/英文泛词/产品名/业务碎片，如 SailPoint/Staff/QA/Web/UX），应删除岗位节点
- course remove_node：该课程节点是完全孤立的低质主题词课程（教学主题词/发音打卡类，非可雇佣课程），应删除课程节点
- course remove_edge：该『技能→课程』LEARNABLE_VIA 边是语义误配（技能与该课程无可学关系），应删边保留课程节点

严格按 JSON 输出，字段：entity_type, action, term, reason, confidence。
要求：
1. 只依据通用技术招聘市场与课程常识判定，不臆造
2. 合法岗位/技能/课程宁可保留，不确定时给低 confidence（<0.8 会转人工审核，宁缺毋滥）
3. skill 动作的 term 语义见字段说明；不确定动作就用确定性更高的一种，confidence 相应下调
"""


def build_decision_prompt(candidate: dict) -> str:
    """构造单候选评估 prompt。candidate: {term, kind, evidence}。"""
    evidence_lines = "\n".join(f"- {k}: {v}" for k, v in (candidate.get("evidence") or {}).items())
    if not evidence_lines:
        evidence_lines = "- 无结构化证据"
    return _DECISION_PROMPT_TEMPLATE.format(
        term=candidate.get("term", ""),
        kind=candidate.get("kind", ""),
        entity_type=candidate.get("entity_type", "skill"),
        evidence=evidence_lines,
    )


_TOOL_ALIAS_KEYS_LOWER = {k.lower() for k in TOOL_ALIAS}
_TOOL_ALIAS_VALUES = set(TOOL_ALIAS.values())


def _split_edge(term: str) -> tuple[str, str]:
    """拆解课程脏边候选 term『技能→课程』。"""
    if "→" in term:
        a, _, b = term.partition("→")
        return a.strip(), b.strip()
    return term, ""


def hard_gate(
    action: str, term: str, entity_type: str = "skill"
) -> tuple[bool, str]:
    """写入侧硬门禁（一票否决，先于分级）。

    核心不变量：动态停用词与白名单/别名标准名互斥——is_noise_skill 判定
    顺序中白名单保护在动态停用词之前（纵深防御），此处从源头拒绝互斥条目。
    position/course 的图谱删除动作同理：白名单/别名/技能白名单实体一票否决，
    防 LLM 误删受过保护的合法实体。
    """
    term = term.strip()
    if len(term) < 2:
        return False, "词条过短"
    if action in ("remove_node", "remove_edge"):
        if entity_type == "position" and action == "remove_node":
            if term in _POSITION_WHITELIST or term in _ALIAS_STANDARDS:
                return False, "命中岗位白名单/别名标准名"
        if action == "remove_edge":
            source, target = _split_edge(term)
            if not source or not target:
                return False, "remove_edge 需为『技能→课程』格式"
        if term in SKILL_WHITELIST:
            return False, "命中技能白名单（可能是技能词被误当候选）"
        if term in SOFT_SKILL_WHITELIST:
            return False, "命中软技能白名单"
        return True, ""
    if action == "add_stopword":
        if term in SKILL_STOPWORDS or is_dynamically_blocked(term):
            return False, "已是现行停用词"
        if term in SKILL_WHITELIST or term in _ALIAS_STANDARDS:
            return False, "命中白名单/别名标准名（停用词优先于白名单，误加即误杀）"
        if term.lower() in _TOOL_ALIAS_KEYS_LOWER or term in _TOOL_ALIAS_VALUES:
            return False, "命中工具别名表"
        if term in SOFT_SKILL_WHITELIST:
            return False, "命中软技能白名单"
        return True, ""
    if action == "remove_stopword":
        if term in SKILL_STOPWORDS or is_dynamically_blocked(term):
            return True, ""
        return False, "目标不是现行停用词（静态移除走 git 固化流程）"
    if action == "protect_whitelist":
        if term in SKILL_WHITELIST or term in _ALIAS_STANDARDS:
            return False, "已受白名单/别名标准名保护"
        if not (term in SKILL_STOPWORDS or is_dynamically_blocked(term)):
            return False, "目标未被任何停用词拦截，无需保护"
        return True, ""
    return False, f"未知 action: {action}"


def tier_for(action: str, gate_ok: bool, impact_nodes: int, confidence: float) -> str:
    """分级裁决：auto（自动生效）/ proposal（进审核池）/ skip（不处理）。

    仅 add_stopword 可自动生效（拦噪音是收紧，风险不对称地低于放行）；
    remove_stopword / protect_whitelist 一律进人工审批。
    """
    if not gate_ok:
        return "skip"
    # 收紧/删除类动作（add_stopword 拦噪音、remove_node/remove_edge 删脏实体）
    # 在低影响 + 高置信下可自动生效；放行类（remove_stopword/protect_whitelist）
    # 一律进人工审批（风险不对称，防误杀真实实体）。
    if action in ("add_stopword", "remove_node", "remove_edge"):
        threshold = runtime_config.get("dict_guard_auto_impact_threshold", 50)
        min_confidence = runtime_config.get("dict_guard_min_confidence", 0.8)
        if impact_nodes > threshold:
            return "proposal"
        if confidence < min_confidence:
            return "proposal"
        return "auto"
    return "proposal"


def select_suspect_skills(rows: list[dict]) -> list[dict]:
    """图谱长尾可疑技能筛选（纯函数）：低引用 + 白名单外 + 非现行停用词。

    rows 来自 Neo4j 查询（name/first_seen/category/req_count），已按引用升序。
    """
    suspects = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if len(name) < 2:
            continue
        if name in SKILL_WHITELIST or name in _ALIAS_STANDARDS:
            continue
        if name in SKILL_STOPWORDS or is_dynamically_blocked(name):
            continue
        suspects.append({
            "term": name,
            "kind": "suspect_skill",
            "evidence": {
                "图谱引用数(REQUIRES)": r.get("req_count", 0),
                "首次入图": r.get("first_seen") or "未知",
                "分类": r.get("category") or "未分类",
            },
        })
    return suspects


def select_stopword_misuse(
    corpus: str, stopwords: set[str], protected_names: set[str]
) -> list[dict]:
    """停用词误杀检测（纯函数）：停用词是某受保护技能名的子串且两者都在语料出现。

    例：停用词「微」⊂「微信小程序」，语料中「微信小程序」有命中 → 「微」过宽
    误杀证据。返回候选（stopword/victim 成对），由 LLM 裁决 remove 或 protect。
    """
    misuses = []
    for victim in sorted(protected_names):
        if not victim or len(victim) < 2 or victim not in corpus:
            continue
        for sw in stopwords:
            if len(sw) < 2 or sw not in victim:
                continue
            misuses.append({
                "term": sw,
                "kind": "stopword_misuse",
                "evidence": {
                    "受影响技能": victim,
                    "该技能语料命中": corpus.count(victim),
                    "停用词语料命中": corpus.count(sw),
                },
            })
    return misuses


def _same_language(a: str, b: str) -> bool:
    """同语言对判定（中-中 / 英-英）；跨语言对跳过（SBERT 跨语言 sim 天然低）。"""
    return _CJK.search(a) is not None and _CJK.search(b) is not None or (
        _CJK.search(a) is None and _CJK.search(b) is None
    )


def select_dirty_positions(rows: list[dict]) -> list[dict]:
    """图谱零引用岗位候选（纯函数）：无岗位 REQUIRES 出边 + 归一化为空。

    真脏岗位（产品名/英文泛词/业务碎片，如 SailPoint/Staff/QA/Web/UX）通常
    零引用且不可归一化（normalize_position_name 返回空串——泛词/停用词/剥离
    碎片/技能词——不再入图），规则初筛到此类再交 LLM 裁决。白名单岗位在
    硬门禁一票否决。

    rows 来自 Neo4j（name/req_count/first_seen），已按引用升序。
    """
    dirty = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if len(name) < 2:
            continue
        if name in _POSITION_WHITELIST or name in _ALIAS_STANDARDS:
            continue
        if r.get("req_count", 0) > 0:
            continue  # 仅零引用（无岗位依赖）才作候选，真脏岗位通常无人引用
        if normalize_position_name(name):
            continue  # 归一化为合法岗位名，不判脏（LLM 不裁决合法岗）
        dirty.append({
            "term": name,
            "kind": "position_dirty",
            "entity_type": "position",
            "evidence": {
                "图谱引用数(REQUIRES)": r.get("req_count", 0),
                "首次入图": r.get("first_seen") or "未知",
                "归一化结果": "(空串=泛词/碎片/停用词，不再入图)",
            },
        })
    return dirty


def select_isolated_courses(rows: list[dict]) -> list[dict]:
    """完全孤立的课程节点候选（纯函数）：无任何入/出边（LEARNABLE_VIA 等）。

    孤立低质课程（教学主题词/发音打卡类，非可雇佣课程）由 LLM 裁决是否删除；
    合法课程的孤立可能因缺建边，故仅 LLM 高置信 + 低影响才自动删，否则人工审批。
    """
    isolated = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if len(name) < 2:
            continue
        isolated.append({
            "term": name,
            "kind": "course_isolated",
            "entity_type": "course",
            "evidence": {
                "边数(入+出)": r.get("edge_count", 0),
                "平台": r.get("platform") or "未知",
                "标题": (r.get("title") or "").strip() or name,
            },
        })
    return isolated


def select_dirty_course_edges(rows: list[dict], semantic) -> list[dict]:
    """课程脏边候选（纯函数）：同语言 LEARNABLE_VIA sim < SEVERE 的交 LLM 复核。

    rows 来自 Neo4j（skill/course/rel_id），semantic 为 SkillEmbedder（None 时
    返回空——worker 未加载 embedder 则跳过硬边治理，不影响孤立课程）。
    """
    if semantic is None:
        return []
    dirty: list[dict] = []
    for e in rows:
        try:
            sim = semantic.similarity(e["skill"], e["course"])
        except Exception:
            continue
        if sim < _EDGE_SEVERE and _same_language(e["skill"], e["course"]):
            key = f"{e['skill']}→{e['course']}"
            dirty.append({
                "term": key,
                "kind": "course_dirty_edge",
                "entity_type": "course",
                "evidence": {
                    "技能": e["skill"],
                    "课程": e["course"],
                    "语义相似度": round(sim, 3),
                    "关系": "LEARNABLE_VIA",
                },
            })
    return dirty
