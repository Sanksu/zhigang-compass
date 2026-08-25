"""独立人工集草稿（DRAFT）生成脚本 — 岗位名 / 技能名归一 LLM 决策器校准。

本脚本生成的是「供人工校准的 DRAFT 集」，不是人类标注金标准。所有标签均为
**机器自动建议**（deterministic 规则 / 词面启发式），并显式标注：

    annotation_status = "draft_auto"
    needs_human       = true

字段语义（见 data_dictionary.md / annotation_guideline.md）：
- gold_*          ：评分器读取的目标字段。此脚本把「机器建议」写入 gold_*，
                    并非人类标注；人工校准后必须覆盖 gold_*。建议值同时镜像在
                    suggested_* 字段，保证不把机器建议误当作人类 gold。
- suggested_*     ：机器建议与产出来源（suggested_via），仅供人工参考。
- candidates      ：候选标准岗位名 / 标准技能名（来自仓库内确定性词表，
                    等价于 PositionCandidateRecaller 的 pool-prefix / 词面
                    回退路径，仓库内无在线图/embedding 时即此降级形态）。

产出文件（均在 data/golden_set/llm_driven/ 下，随其它金标准一起被 git 跟踪）：
    position_normalization_draft.jsonl   （~80 条）
    skill_normalization_draft.jsonl       （~55 条）

用法：uv run python scripts/freeze_norm_gold_draft.py
（幂等：固定 seed / 确定性输入，重跑结果一致。）
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

_OUTPUT_DIR = Path(_BACKEND_DIR) / "data" / "golden_set" / "llm_driven"

from app.services.extraction.dictionary import (  # noqa: E402
    SKILL_WHITELIST,
    _ALIAS_STANDARDS,
    _EN_POSITION_MAP,
    _POSITION_KEYWORDS,
    _POSITION_SKILL_ROUTING,
    _POSITION_WHITELIST,
    normalize_position_name,
)
from app.services.extraction.dictionary_data import SKILL_ALIAS  # noqa: E402


# ---------------------------------------------------------------- 通用工具
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def clean_variant(name: str) -> str:
    """评分/比较用变体键：NFKC + 去全部空白 + casefold（对齐 _clean_variant 语义）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).casefold()


def _is_ascii_short(name: str) -> bool:
    return bool(re.match(r"^[A-Za-z]{1,6}$", name))


def _rank_key(name: str, candidate: str) -> tuple:
    """词面候选排序键（等价 candidate_rank_key 的强关联优先语义）。"""
    nl, cl = (name or "").lower(), (candidate or "").lower()
    strip = lambda s: re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s)
    upper_initials = "".join(ch for ch in candidate if ch.isupper())
    abbr = (
        2 <= len(nl) <= 6 and nl.isalpha() and nl.isupper() and upper_initials == nl
    )
    cjk_prefix = bool(_CJK_RE.search(nl)) and cl and nl.startswith(cl)
    strong = ((nl == cl or nl in cl or cl in nl or strip(nl) == strip(cl))
              and min(len(nl), len(cl)) >= 2) or abbr or cjk_prefix
    tier = 0 if strong else (1 if nl and cl and nl[0] == cl[0] else 2)
    return (tier, abs(len(cl) - len(nl)), candidate)


# 中文标准岗位名候选池（岗位类）。英文岗位名规范见于 _EN_POSITION_MAP。
_POSITION_POOL_CJK = sorted(
    set(_POSITION_WHITELIST)
    | {standard for _, standard in _POSITION_KEYWORDS}
    | {family for _, family in _POSITION_SKILL_ROUTING}
)


def _position_candidates(raw_title: str, solution: str, size: int = 8) -> list[str]:
    """候选岗位名：solution 置顶 + 邻近词面 TopK（PositionCandidateRecaller 词面回退）。

    仓库内无在线图/embedding，故采用 Recall 的 pool-prefix / 词面回退形态；
    候选集为仓库内确定性岗位词表，与生产图谱候选同源（白名单/关键词族/兜底族）。
    """
    cands: list[str] = []
    if solution and solution not in cands:
        cands.append(solution)
    ranked = sorted(_POSITION_POOL_CJK, key=lambda c: _rank_key(raw_title, c))
    for c in ranked:
        if c not in cands:
            cands.append(c)
        if len(cands) >= size:
            break
    # 纯英文岗位的候选：附上中文翻译路径的候选标准名（若可用），供 LLM 跨语言裁决
    if not _CJK_RE.search(raw_title):
        zh = normalize_position_name(raw_title, [])
        if zh:
            cands.append(zh)
    return cands[:size]


_SLICE_FOR_RAW: list[tuple[str, str]] = []


def _slice_for_position(raw_title: str, norm: str) -> str:
    if "实习" in raw_title:
        return "intern"
    if not _CJK_RE.search(raw_title):
        return "pure_en"
    if _CJK_RE.search(raw_title) and re.search(r"[A-Za-z0-9]", raw_title):
        return "mixed"
    return "reject" if not norm else "cjk"


# ---------------------------------------------------------------- 岗位名 DRAFT
def _position_row(jd: dict, idx: int) -> dict:
    raw = (jd.get("job_title_raw") or "").strip()
    skills = jd.get("gold_skills") or []
    solution = normalize_position_name(raw, list(skills))  # 机器建议（deterministic）
    slice_t = _slice_for_position(raw, solution)

    gold_keep = not bool(solution)
    gold_canonical = "" if gold_keep else solution
    gold_is_new = False

    # 机器建议产出来源
    if "实习" in raw:
        via = "norm_intern"
    elif "实习" in (jd.get("gold_title") or ""):
        via = "norm_intern"
    elif gold_keep:
        via = "norm_reject"
    else:
        via = "normalize_position_name"
    return {
        "id": "pos_" + str(jd.get("sample_id") or str(idx).zfill(4)),
        "raw_title": raw,
        "source": jd.get("source") or "zhilian",
        "skills": list(skills),
        "candidates": _position_candidates(raw, solution),
        "gold_canonical": gold_canonical,
        "gold_is_new": gold_is_new,
        "gold_keep_original": gold_keep,
        "slice": slice_t,
        "source_note": "jd_golden_110.jsonl #" + str(jd.get("sample_id")),
        "gold_title_ref": jd.get("gold_title") or "",
        "suggested_canonical": gold_canonical,
        "suggested_is_new": gold_is_new,
        "suggested_keep_original": gold_keep,
        "suggested_via": via,
        "annotation_status": "draft_auto",
        "needs_human": True,
    }


def _en_position_row(en_title: str, idx: int) -> dict:
    """纯英文岗位名（_EN_POSITION_MAP 键）DRAFT——覆盖英文翻译路径人工裁决。

    英文岗位名在仓库内有权威中文映射（_EN_POSITION_MAP），故 gold 恒为标准名
    （keep_original=False）；若规则路径 normalize_position_name 返回空（规则未覆盖 /
    多词未翻译），gold 回退到映射的 zh——正是规则与权威映射不一致、需人工裁决之处。
    """
    zh = _EN_POSITION_MAP[en_title]
    solution = normalize_position_name(en_title, [])
    slice_t = _slice_for_position(en_title, solution)
    gold_canonical = solution or zh  # 权威映射为准；规则空时回退映射值
    gold_is_new = False
    return {
        "id": f"pos_en_{idx:03d}",
        "raw_title": en_title,
        "source": "synthetic_en",
        "skills": [],
        "candidates": _position_candidates(en_title, gold_canonical),
        "gold_canonical": gold_canonical,
        "gold_is_new": gold_is_new,
        "gold_keep_original": False,
        "slice": slice_t,
        "source_note": "_EN_POSITION_MAP",
        "gold_title_ref": zh,
        "suggested_canonical": gold_canonical,
        "suggested_is_new": gold_is_new,
        "suggested_keep_original": False,
        "suggested_via": "_translate_en_position (rule-empty fallback zh)" if not solution else "_translate_en_position",
        "annotation_status": "draft_auto",
        "needs_human": True,
    }


def build_position_rows(jd_rows: list[dict], cap: int = 80, en_cap: int = 12) -> list[dict]:
    """岗位名 DRAFT：JD 派生为主 + 少量纯英文翻译切片（平衡 slice 多样性）。

    主信号来自真实 JD（jd_golden_110.jsonl），覆盖 cjk / mixed / intern / reject；
    纯英文切片仅从 _EN_POSITION_MAP 选取少量有跨语言差异的键，用于人工裁决
    英文翻译路径，避免英中直译相等（裁决价值低）。
    """
    rows: list[dict] = []
    seen_raw: set[str] = set()
    for jd in jd_rows:
        raw = (jd.get("job_title_raw") or "").strip()
        if not raw or raw in seen_raw:
            continue
        seen_raw.add(raw)
        rows.append(_position_row(jd, len(rows) + 1))

    # 纯英文切片补充（顺序确定，前 en_cap 个，取含空格且英中不等者以保留裁决价值）
    en_added = 0
    for en_title, zh in sorted(_EN_POSITION_MAP.items()):
        if en_added >= en_cap:
            break
        if not en_title.strip() or not zh:
            continue
        if " " not in en_title:  # 单英文词翻译价值低（多是缩写/工具名）
            continue
        if en_title.lower() == zh.lower():
            continue
        rows.append(_en_position_row(en_title, en_added + 1))
        en_added += 1

    # 限额到 cap，保证关键切片（intern/reject/pure_en）不被裁光
    if len(rows) > cap:
        priority = [r for r in rows if r["slice"] in ("intern", "reject", "pure_en")]
        rest = [r for r in rows if r not in priority]
        # 保底：纯英文切片只留 en_cap 个（防 pure_en 泛滥）
        pure_en = [r for r in priority if r["slice"] == "pure_en"]
        critical = [r for r in priority if r["slice"] != "pure_en"]
        pure_en = pure_en[:en_cap]
        keep = critical + pure_en
        room = cap - len(keep)
        filler = [r for r in rest if r not in keep][: max(0, room)]
        rows = keep + filler
    return rows[:cap]


# ---------------------------------------------------------------- 技能名 DRAFT
# 人工裁决类切片（非纯别名派生）：覆盖 near_synonym / same_initial /
# short_ascii / version_variant / cjk_abbr 五类"需要人类裁决的硬案例"。
# 每项含：variant、歧义主题 note、以及建议（由 SKILL_ALIAS / 词面启发式产出，
# 不以任何形式伪装成人类 gold）。
_SKILL_HARD_CASES: list[dict] = [
    # ---- near_synonym：近似同义，可并但边界需人类裁决 ----
    {"variant": "大模型", "note": "与『大语言模型』近义：并入『大语言模型』还是独立保留？"},
    {"variant": "LLM", "note": "缩略全称：并入『大语言模型』；短缩写是否受保护？"},
    {"variant": "AIGC创作", "note": "业务侧『AIGC创作』是否并入『AIGC』？"},
    {"variant": "多模态大模型", "note": "并入『多模态模型』 vs 单列（大模型具体化）？"},
    {"variant": "前端全栈", "note": "『前端全栈』是『全栈』子类还是独立技能？"},
    {"variant": "自动驾驶测试", "note": "『自动驾驶测试』 vs 『自动化测试』不同领域，是否合并？"},
    {"variant": "数据资产", "note": "『数据资产』 vs 『数据治理』：业务词还是独立技能？"},
    {"variant": "深度学习算法", "note": "并入『深度学习』（别名）vs 保留『深度学习算法』（细分）？"},
    {"variant": "机器学习算法", "note": "并入『机器学习』（别名）vs 保留『机器学习算法』？"},
    {"variant": "分布式事务", "note": "并入『分布式技术』（别名）vs 独立技术点？"},
    {"variant": "性能优化", "note": "并入『性能调优』（别名）vs 保留『性能优化』？"},
    {"variant": "故障排查", "note": "并入『故障处理』（别名）vs 保留『故障排查』？"},
    {"variant": "敏捷", "note": "并入『敏捷开发』（别名）vs 保留『敏捷』（方法论）？"},
    {"variant": "可视化分析", "note": "并入『数据可视化』（别名）vs 保留『可视化分析』？"},
    {"variant": "大屏可视化", "note": "并入『数据可视化』（别名）vs 保留『大屏可视化』（前端展示）？"},
    {"variant": "微信小程序", "note": "并入『小程序』（别名）vs 保留『微信小程序』（平台限定）？"},
    {"variant": "单元测试编写", "note": "并入『单元测试』（别名）vs 保留『单元测试编写』（动作）？"},
    {"variant": "全栈开发", "note": "并入『全栈』（别名）vs 保留『全栈开发』？"},
    {"variant": "模型微调", "note": "『模型微调』 vs 『微调训练』：近义是否合并？"},
    {"variant": "图谱构建", "note": "『图谱构建』 vs 『知识图谱』：是否同一技能？"},
    {"variant": "数据标注", "note": "『数据标注』 vs 『音频标注/视频标注』：是否分层级归并？"},
    {"variant": "SFT", "note": "『SFT』（监督微调）并入『模型微调』（别名）；短缩写是否保护？"},
    {"variant": "RAG", "note": "『RAG』并入『检索增强生成』（别名）；短缩写是否保护？"},
    {"variant": "NLU", "note": "『NLU』(自然语言理解) 并入『自然语言处理』还是独立？"},
    {"variant": "对话系统", "note": "『对话系统』 vs 『智能体』：是否同一技能簇？"},
    # ---- same_initial：同首字母、义不同，机器词面易误并 ----
    {"variant": "AS", "note": "同首字母：应用服务器 vs 组装语言？无关联语境须 keep 防误并。"},
    {"variant": "GIS", "note": "地理信息系统 vs 气体绝缘（电学）：须靠语境，词面不可判。"},
    {"variant": "ID", "note": "识别 vs 工业设计：缩写歧义，判 keep。"},
    {"variant": "UI", "note": "用户界面 vs 上位机/统一接口：须 preserve。"},
    {"variant": "AP", "note": "接入网 vs 应用/Access Point：极端歧义，keep。"},
    {"variant": "AOP", "note": "面向切面编程 vs 其他 AOP 全称；短大写缩写 keep。"},
    {"variant": "EDC", "note": "电子数据采集 vs 工程开发中心：歧义，keep。"},
    {"variant": "SPA", "note": "单页应用 vs 其他 SPA；歧义，keep。"},
    # ---- short_ascii：短 ASCII 保护（≤6 全大写），防 SBERT/LLM 误并 ----
    {"variant": "AI", "note": "白名单短词，保持独立（与『人工智能』需人类确认是否合并）。"},
    {"variant": "API", "note": "标准名，保持独立。"},
    {"variant": "SQL", "note": "标准名，保持独立；不并入『数据库』。"},
    {"variant": "SRE", "note": "站点可靠性工程，独立技能。"},
    {"variant": "AWS", "note": "云厂商标准名，保持独立。"},
    {"variant": "GCP", "note": "云厂商标准名，保持独立。"},
    {"variant": "DevOps", "note": "方法论标准名，保持独立。"},
    {"variant": "BGP", "note": "路由协议标准名，保持独立。"},
    # ---- version_variant：版本/语种变体，机器归并 vs 人类裁决 ----
    {"variant": "Python3", "note": "并入『Python』（规则），但严格版本语义是否保留？"},
    {"variant": "React 18", "note": "并入『React』 vs 保留 React 18（版本语义）。"},
    {"variant": "Vue2", "note": "并入『Vue.js』（规则），版本语义由人类裁决。"},
    {"variant": "SpringBoot", "note": "并入『Spring Boot』：连写变体。"},
    {"variant": "Go 1.20", "note": "并入『Go』 vs 保留版本：人类裁决。"},
    {"variant": "Vue3", "note": "并入『Vue.js』（规则）vs 保留 Vue3（大版本语义）。"},
    {"variant": "ES6", "note": "并入『JavaScript』（别名）vs 保留 ES6（语言版本）。"},
    {"variant": "React Native", "note": "『React Native』 vs 『React』：不同技术栈，勿并。"},
    # ---- cjk_abbr：中文缩写/口语，需人类确认是否归一 ----
    {"variant": "小程序", "note": "是否并入『微信小程序』还是独立技能（通用小程序）？"},
    {"variant": "AI编程", "note": "并入『AI辅助编程』（规则），词面难以区分是否同义。"},
    {"variant": "数据科学", "note": "独立技能 vs 并入『数据分析』？"},
    {"variant": "网络攻防", "note": "并入『网络安全』（规则），攻防细分语义是否保留？"},
    {"variant": "后端", "note": "『后端』 vs 『后端开发』：是否独立技能？"},
    {"variant": "老代码维护", "note": "业务活动词，是否判 noise？"},
    {"variant": "前端页面", "note": "『前端页面』 vs 『前端开发』：是否独立技能？"},
    {"variant": "模型微服务化", "note": "『模型微服务化』 vs 『微服务』：是否同一技能？"},
]


def _suggest_skill(variant: str) -> tuple[str, str, str]:
    """为技能变体给出机器建议：(action, target_standard, via)。"""

    def _near_std() -> list[str]:
        return sorted(
            set(SKILL_WHITELIST) | set(_ALIAS_STANDARDS),
            key=lambda c: _rank_key(variant, c),
        )

    alias = SKILL_ALIAS.get(variant) or SKILL_ALIAS.get(variant.lower())
    if alias:
        return ("merge", alias, "SKILL_ALIAS")
    standards = _near_std()
    top = standards[0] if standards else ""
    if _is_ascii_short(variant) and variant.isalpha() and variant.isupper():
        return ("keep", variant, "ascii_short_protect")
    if top and _rank_key(variant, top)[0] == 0 and top.lower() != variant.lower():
        return ("merge", top, "candidate_rank_key")
    return ("keep", variant, "candidate_rank_key_keep")


def build_skill_rows() -> list[dict]:
    rows: list[dict] = []
    used: set[str] = set()
    for i, case in enumerate(_SKILL_HARD_CASES, start=1):
        variant = case["variant"].strip()
        if not variant or variant in used:
            continue
        used.add(variant)
        action, target, via = _suggest_skill(variant)
        if variant in {"AI", "API", "SQL", "SRE", "AWS", "GCP", "DevOps", "BGP"}:
            slice_t = "short_ascii"
        elif variant in {
            "AS", "GIS", "ID", "UI", "AP", "AOP", "EDC", "SPA",
        }:
            slice_t = "same_initial"
        elif variant in {
            "Python3", "React 18", "Vue2", "SpringBoot", "Go 1.20", "Vue3",
            "ES6", "React Native",
        }:
            slice_t = "version_variant"
        elif _CJK_RE.search(variant):
            slice_t = "cjk_abbr"
        else:
            slice_t = "near_synonym"
        # 候选 = 词面 TopK；对齐生产 alias-prepend：SKILL_ALIAS 落点置顶入候选，
        # 使建议 target 一定在 candidates 内（评分时可无歧义比较）。
        cand = sorted(
            set(SKILL_WHITELIST) | set(_ALIAS_STANDARDS),
            key=lambda c: _rank_key(variant, c),
        )[:15]
        alias_target = SKILL_ALIAS.get(variant) or SKILL_ALIAS.get(variant.lower())
        if alias_target and alias_target not in cand:
            cand = [alias_target] + [c for c in cand if c != alias_target]
            cand = cand[:15]
        row = {
            "id": "skill_draft_" + str(i).zfill(3),
            "variant": variant,
            "gold_action": action,          # 机器建议（目标字段=建议初始，人工覆盖）
            "gold_standard": target,
            "gold_keep": action == "keep",
            "slice": slice_t,
            "source_note": case["note"],
            "candidates": cand,
            "suggested_action": action,
            "suggested_standard": target,
            "suggested_via": via,
            "annotation_status": "draft_auto",
            "needs_human": True,
        }
        rows.append(row)
    return rows


def main() -> None:
    jd_path = _BACKEND_DIR / "data" / "golden_set" / "final" / "jd_golden_110.jsonl"
    jd_rows = [
        json.loads(line)
        for line in jd_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pos_rows = build_position_rows(jd_rows)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pos_path = _OUTPUT_DIR / "position_normalization_draft.jsonl"
    pos_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pos_rows),
        encoding="utf-8",
    )

    skill_rows = build_skill_rows()
    skill_path = _OUTPUT_DIR / "skill_normalization_draft.jsonl"
    skill_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in skill_rows),
        encoding="utf-8",
    )

    print(f"[pos] {len(pos_rows)} 条 -> {pos_path}")
    print(f"     切片分布: {sorted({r['slice'] for r in pos_rows})}")
    print(f"[skill] {len(skill_rows)} 条 -> {skill_path}")
    print(f"     切片分布: {sorted({r['slice'] for r in skill_rows})}")


if __name__ == "__main__":
    main()
