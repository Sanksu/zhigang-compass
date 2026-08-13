"""RAG 增强抽取验证：盲审集对比基线 vs 图谱参照变体（实验性，不入产品链路）。

假设（08-13 评估）：LLM 抽取岗位名/技能时无图谱外部知识，岗位名靠 prompt
规则自由生成（英文翻译/限定词保留），技能靠模型记忆——接入图谱 RAG
（已有岗位定义 + 权威库 + 白名单技能）可提高准确率。

本脚本在**同一盲审集**上跑两条路径并输出对比：
- 基线：JDExtractor.extract（现有链路，纯 prompt）
- RAG：岗位名粗提 → retrieve_context 检索图谱 → prompt 注入"已知岗位参照"段

用法：
    python tests/evaluate/run_rag_jd_eval.py            # 全量对比
    python tests/evaluate/run_rag_jd_eval.py --samples 3  # 冒烟跑 3 条
"""

import argparse
import asyncio
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.logging import setup_logging  # noqa: E402

logger = setup_logging("run_rag_jd_eval")

# 复用盲审加载/对比逻辑（不复制粘贴）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_manual_jd_eval import (  # noqa: E402
    _compare_set,
    _load_round1_blind_rows,
    _metric,
    _safe_exception_summary,
    apply_gold_revisions,
    load_gold_revisions,
)

DEFAULT_XLSX = ROOT / "data" / "golden_set" / "review" / "jd_manual_review_round1.xlsx"

# 岗位名粗提：JD 首行/标题（评估用 job_title_raw 模拟真实链路中标题可获取场景；
# 真实 ETL 中可从 raw_text 首行正则提取，见 _extract_query_from_text）
_TITLE_RE = re.compile(r"^(.*(?:工程师|科学家|分析师|经理|设计师|架构师|开发|专员|顾问|主管|实习生)[^\n]*)")


def _extract_query_from_text(jd_text: str) -> str:
    """从 JD 正文粗提岗位关键词（零 LLM 成本，真实链路用）。

    取标题行岗位词，剥离"岗位名称："前缀与括号方向限定（如
    "岗位名称：全栈工程师（java，go…）" → "全栈工程师"）。失败返回空串。
    """
    m = _TITLE_RE.search(jd_text or "")
    if not m:
        return ""
    title = m.group(1).strip()
    title = re.sub(r"^岗位名称[：:]\s*", "", title)
    title = re.split(r"[（(]", title)[0].strip()
    return title[:20]


async def _retrieve_rag(query: str) -> str:
    """检索图谱岗位/技能，组装参照段（≤2000 token 截断）。

    验证用检索路（第 3 轮优化：**只注入图谱中文标准名**）：
    - 图谱 Position 节点（Neo4j）：中文标准岗位名 + 高频技能
    - 图谱 Skill 节点（Neo4j）：标准技能名（skill 全文路）

    刻意**排除权威库 occupations 英文定义**（Generative AI Engineer 等）——
    实测英文定义对中文抽取是噪音（jd_030 检索到英文岗位定义后岗位名被带偏，
    skills F1 -0.278）。抽取参照只需"中文标准名"，权威英文定义是接地场景的
    输入，不是抽取场景的参照。
    """
    from app.core.database import neo4j_driver

    if not query:
        return ""
    lines: list[str] = []

    with neo4j_driver.session() as session:
        # 1) Position：中文标准岗位名 + 高频技能
        rows = session.run(
            """
            MATCH (p:Position)
            WHERE toLower(p.name) CONTAINS toLower($q) OR $q CONTAINS toLower(p.name)
            WITH p LIMIT 5
            OPTIONAL MATCH (p)-[r:REQUIRES]->(s:Skill)
            WITH p, s ORDER BY r.weight DESC
            WITH p, collect(s.name)[..8] AS skills
            RETURN p.name AS name, skills
            """,
            q=query.strip()[:30],
        ).data()
        for r in rows:
            name = r.get("name") or ""
            if name:
                lines.append(name)  # 第 4 轮：仅岗位名（技能列表会干扰 LLM 技能抽取，jd_012 实测 -0.25）

        # 2) Skill：标准技能名（图谱 Skill 全文路）
        if len(lines) < 6:
            srows = session.run(
                """
                MATCH (s:Skill)
                WHERE toLower(s.name) CONTAINS toLower($q)
                RETURN s.name AS name LIMIT 8
                """,
                q=query.strip()[:30],
            ).data()
            for r in srows:
                name = r.get("name") or ""
                if name and name not in lines:
                    lines.append(name)
    return "\n".join(lines[:10])


def _extract_with_rag(jd_text: str, llm, title_hint: str = "") -> tuple[object, str]:
    """RAG 变体抽取：检索图谱 → prompt 强约束注入 → LLM 抽取 → 后处理。

    第 2 轮实验（强约束）：检索到的图谱岗位名作为"候选标准名"，prompt
    明确要求岗位名与候选匹配时必须采用候选名（而非自创）。返回 (result,
    rag_context)。

    title_hint：采集端标题（JobItem.title / job_title_raw）——真实链路中
    Scrapy 采集的 JD 标题可用，且标题是岗位名检索的最佳 query（盲审集
    detail_raw_text 正文可能无标题，08-13 实测 jd_012 正文直接是职责描述）。
    """
    from app.services.extraction.jd_extractor import JDExtractor
    from app.services.extraction.post_processor import post_process
    from app.services.extraction.prompts import FEW_SHOT_EXAMPLES, SYSTEM_PROMPT, TASK_TEMPLATE
    from app.services.extraction.schemas import JDExtractionResult

    query = (title_hint or "").strip()[:20] or _extract_query_from_text(jd_text)
    # asyncio.run 在已有 event loop（LLM provider 异步链）内会抛
    # "Event loop is closed"/嵌套 loop 冲突——改用当前 loop 的 run_until_complete
    try:
        rag_block = asyncio.get_event_loop().run_until_complete(_retrieve_rag(query))
    except RuntimeError:
        rag_block = asyncio.run(_retrieve_rag(query))

    if rag_block:
        # 强约束注入：候选岗位名是"标准答案参照"，命中时强制采用
        # （区别于第 1 轮"仅作参考"——LLM 会忽略弱参考，实测零修正）
        prompt = TASK_TEMPLATE.format(jd_text=jd_text) + f"""

【图谱已知岗位候选】（强约束：若 JD 岗位与下列任一候选匹配，position_name 必须采用该候选名，禁止自创变体；仅当 JD 岗位明显不属于任何候选时才按原规则命名）
{rag_block}
"""
    else:
        prompt = TASK_TEMPLATE.format(jd_text=jd_text)

    try:
        result = llm.extract_structured(
            prompt, JDExtractionResult,
            system_prompt=SYSTEM_PROMPT + "\n\n" + FEW_SHOT_EXAMPLES,
        )
    except Exception:
        # RAG 变体不改变降级语义：LLM 失败走规则抽取
        extractor = JDExtractor(llm=llm)
        return extractor._rule_based_extract(jd_text), rag_block
    return post_process(result), rag_block


def _run_variant(rows, llm_factory, use_rag: bool) -> dict:
    """跑一个变体（基线或 RAG），返回指标与逐条明细。"""
    from app.services.extraction.jd_extractor import JDExtractor
    from app.services.extraction.dictionary import normalize_position_name

    revisions = load_gold_revisions()
    details = []
    title_hits = title_raw_hits = title_count = 0
    skills_total: Counter[str] = Counter()
    bonus_total: Counter[str] = Counter()
    sample_skill_f1: list[float] = []
    sample_bonus_f1: list[float] = []

    for row in rows:
        llm = llm_factory()
        sid = row.get("sample_id", "?")
        gold_title = (row.get("review_gold_title") or "").strip()
        gold_skills, gold_bonus = apply_gold_revisions(
            revisions, sid,
            [s for s in _json_list(row.get("review_gold_skills")) if s],
            [s for s in _json_list(row.get("review_gold_bonus_skills")) if s],
        )
        try:
            if use_rag:
                # 检索 query 用采集端标题（真实链路 JobItem.title 可用）——
                # 盲审 detail_raw_text 正文可能无标题（jd_012 实测）
                result, rag_ctx = _extract_with_rag(
                    row["detail_raw_text"], llm,
                    title_hint=row.get("job_title_raw", ""),
                )
            else:
                result = JDExtractor(llm=llm).extract(row["detail_raw_text"])
                rag_ctx = ""
        except Exception as exc:
            import traceback
            logger.error("样本 %s RAG 变体失败: %s\n%s", row.get("sample_id"), _safe_exception_summary(exc), traceback.format_exc(limit=3))
            details.append({"sample_id": sid, "status": "failed", "error": _safe_exception_summary(exc)})
            continue

        pred_title = (getattr(result, "position_name", "") or "").strip()
        pred_skills = [s.name for s in getattr(result, "skills", []) if s and getattr(s, "name", None)]
        pred_bonus = [s.name for s in getattr(result, "bonus_skills", []) if s and getattr(s, "name", None)]

        # title 判定（与基线脚本口径一致：normalize 后比对）
        if gold_title:
            title_count += 1
            norm_gold = normalize_position_name(gold_title)
            norm_pred = normalize_position_name(pred_title)
            if norm_pred == norm_gold:
                title_hits += 1
            if pred_title == gold_title:
                title_raw_hits += 1

        skill_cmp = _compare_set(gold_skills, pred_skills)
        bonus_cmp = _compare_set(gold_bonus, pred_bonus)
        for k, v in skill_cmp.items():
            skills_total[k] += len(v) if isinstance(v, list) else 0
        for k, v in bonus_cmp.items():
            bonus_total[k] += len(v) if isinstance(v, list) else 0
        sample_skill_f1.append(skill_cmp["f1"])
        sample_bonus_f1.append(bonus_cmp["f1"])

        details.append({
            "sample_id": sid,
            "status": "ok",
            "rag_context": rag_ctx[:200],
            "title_gold": gold_title,
            "title_pred": pred_title,
            "skills_gold": gold_skills,
            "skills_pred": pred_skills,
            "skill_f1": skill_cmp["f1"],
            "bonus_f1": bonus_cmp["f1"],
        })

    n = len(details)
    return {
        "samples": n,
        "title_normalized_accuracy": title_hits / title_count if title_count else None,
        "title_raw_accuracy": title_raw_hits / title_count if title_count else None,
        "skills_micro_f1": _metric(**{k: skills_total.get(k, 0) for k in ("tp", "fp", "fn")})["f1"],
        "skills_avg_f1": sum(sample_skill_f1) / n if n else None,
        "bonus_micro_f1": _metric(**{k: bonus_total.get(k, 0) for k in ("tp", "fp", "fn")})["f1"],
        "details": details,
    }


def _json_list(v: str) -> list[str]:
    import json
    if not v:
        return []
    try:
        parsed = json.loads(v)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return [x.strip() for x in v.replace("[", "").replace("]", "").split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 增强抽取 vs 基线（盲审集对比）")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--sheet", default="Round1盲标")
    parser.add_argument("--samples", type=int, default=0, help=">0 时只跑前 N 条（冒烟）")
    args = parser.parse_args()

    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain

    try:
        provider = LLMProviderChain()
    except LLMConfigurationError as exc:
        logger.error("LLMProviderChain 不可用：%s", exc)
        return 1

    rows = _load_round1_blind_rows(args.xlsx, args.sheet)
    if args.samples:
        rows = rows[: args.samples]
    logger.info("盲审集 %s 条（%s）", len(rows), args.sheet)

    def llm_factory():
        return provider

    baseline = _run_variant(rows, llm_factory, use_rag=False)
    rag = _run_variant(rows, llm_factory, use_rag=True)

    print("\n" + "=" * 64)
    print(f"对比（{len(rows)} 条盲审）")
    print("=" * 64)
    for name, v in (("基线(pure prompt)", baseline), ("RAG(图谱参照)", rag)):
        print(f"\n[{name}]")
        print(f"  title_normalized_accuracy: {v['title_normalized_accuracy']:.1%}" if v["title_normalized_accuracy"] is not None else "  title_normalized_accuracy: None")
        print(f"  skills_micro_f1: {v['skills_micro_f1']:.4f}  avg: {v['skills_avg_f1']:.4f}")
        print(f"  bonus_micro_f1: {v['bonus_micro_f1']:.4f}")
    print("\n逐条差异（title 命中变化 + skills F1 变化）：")
    for b, r in zip(baseline["details"], rag["details"]):
        if b.get("status") != "ok" or r.get("status") != "ok":
            print(f"  {b.get('sample_id')}: {b.get('status')}/{r.get('status')}")
            continue
        b_hit = b["title_pred"] == b["title_gold"]
        r_hit = r["title_pred"] == r["title_gold"]
        tmark = "🔄 修正" if not b_hit and r_hit else ("↩️ 回归" if b_hit and not r_hit else ("✅" if b_hit and r_hit else "—"))
        df = r["skill_f1"] - b["skill_f1"]
        print(f"  {b['sample_id']}: title {tmark} gold={b['title_gold']!r} base={b['title_pred']!r} rag={r['title_pred']!r} | skills ΔF1={df:+.3f} ({b['skill_f1']:.3f}→{r['skill_f1']:.3f})")
        if r["rag_context"]:
            print(f"      RAG ctx: {r['rag_context'][:120]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
