"""阶段 C：JD 级评分 → 岗位级聚合展示。

阶段 C 把匹配候选从「聚合岗位画像」切到「原生 JD」：对单 JD 逐条评分后，
按岗位名（snapshot.normalized_position）聚合成「岗位级展示」，组内最高分 JD
作为该岗位的匹配代表 + 附 JD 级证据（复用阶段 B 展示形态）。这样：
- 候选区分度来自单 JD（聚合画像抹平的 JD 差异恢复）
- 展示仍是岗位级（前端图谱锚点/交互语义不破坏）
- Top-2 JD 证据即「该岗位下最匹配的真实 JD」（可点击原文）

聚合语义：
- group key = jd_position 映射的岗位名（snapshot.normalized_position）
- 组 score = 组内最高分（JD 直配的最优表现）；summary 取组内最高分 JD 的摘要
- 组证据 = 组内 Top-2 JD（按 total_score 排序，含标题/链接/命中技能）

仅供 recommend 异步任务使用（不动 compare 同步路径）。
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

_AGGREGATE_TOP_JD_EVIDENCE = 2


def aggregate_jd_scores(
    scored: list,
    jd_position: dict[str, str],
    top_n: int,
) -> list[dict]:
    """JD 级 MatchResult 列表 → 岗位级聚合 dict 列表。

    scored: engine.score_position 按 JD 产出的 MatchResult（position_id=jd_id、
    name=JD 标题、total_score 等）。jd_position: jd_id→岗位名（jd_profiles
    加载时的 snapshot.normalized_position 映射）。
    返回 [{position_id, position_name, total_score, must_score, nice_score,
           exp_score, matched_must, missing_must, summary, unqualified,
           jd_evidence:[{jd_id, jd_title, total_score, hit_count}]}]
    """
    groups: dict[str, list] = defaultdict(list)
    for r in scored:
        gname = jd_position.get(r.position_id)
        if not gname:
            # 无岗位名归属（snapshot.normalized_position 空）的 JD 不参与岗位
            # 聚合——没有图谱岗位锚点，进 Top-N 对展示无意义（仅作 JD 证据候选，
            # 已随命中岗位带出）。
            continue
        groups[gname].append(r)

    out: list[dict] = []
    for gname, members in groups.items():
        best = max(members, key=lambda r: r.total_score)
        # Top-2 证据按 jd_title 去重（同标题不同 jd_id 的重复挂载条目只保留最高分一条，
        # 避免证据卡同名两行——E2E 实测「数据库管理员」两条证据同标题）
        seen_titles: set[str] = set()
        jd_evidence = []
        for e in sorted(members, key=lambda r: r.total_score, reverse=True):
            if e.position_name in seen_titles:
                continue
            seen_titles.add(e.position_name)
            jd_evidence.append(e)
            if len(jd_evidence) >= _AGGREGATE_TOP_JD_EVIDENCE:
                break
        out.append({
            "position_id": gname,
            "position_name": gname,
            "total_score": round(best.total_score, 4),
            "must_score": best.must_score,
            "nice_score": round(best.nice_score, 4),
            "exp_score": round(best.exp_score, 4),
            "matched_must": best.matched_must,
            "missing_must": best.missing_must,
            "summary": best.summary,
            "unqualified": best.unqualified,
            "jd_evidence": [
                {
                    "jd_id": e.position_id,
                    "jd_title": e.position_name,
                    "total_score": round(e.total_score, 4),
                    # hit_count 统一 must+nice 命中口径（与阶段 B jd_rerank 一致；
                    # 此前仅 must，同名字段两口径——第六轮审查算法口径 4，zkt 复核）
                    "hit_count": len(e.matched_must) + len(e.matched_nice),
                }
                for e in jd_evidence
            ],
        })
    out.sort(key=lambda r: r["total_score"], reverse=True)
    return out[:top_n]