"""学习路径生成器（AL-M4-03，设计文档 §9.5 / §4.6）。

流程：差距分析（gap.py 三态）→ 对每个 missing/weak 技能：先修链展开
（prerequisites.py 字典）→ 课程匹配（courses.py LEARNABLE_VIA + 质量分 Top-3）
→ 输出甘特图格式 {skill, prerequisites[], courses[], estimated_hours, priority}。
"""

import asyncio
from typing import Awaitable, Callable

from app.services.learning_path.courses import load_courses_for_skill
from app.services.learning_path.gap import analyze_gaps
from app.services.learning_path.prerequisites import base_hours, prerequisite_chain
from app.services.learning_path.schemas import (
    CourseRecommendation,
    GapType,
    LearningPathItem,
    LearningPathResult,
)

_TOP_COURSES = 3
# 学习路径项上限（设计文档 §9.5：差距按优先级 Top-5 作为重点改进项）
_MAX_PATH_ITEMS = 5

# 课程加载器签名：输入 (skill_id, skill_name, top_k, semantic, sim_threshold)，
# 输出按质量分排序的课程。semantic 供语义 fallback（岗位中文技能 → 课程英文标准名）
CourseLoader = Callable[
    [str, str, int, object, float | None], Awaitable[list[CourseRecommendation]]
]


class LearningPathGenerator:
    """学习路径生成器。

    Args:
        course_loader: 课程加载器，可注入（测试用假加载器）；缺省为
            图谱 LEARNABLE_VIA + PostgreSQL 质量分关联的真实实现。
    """

    def __init__(self, course_loader: CourseLoader | None = None):
        self._course_loader = course_loader or load_courses_for_skill

    async def generate(
        self,
        candidate,
        position,
        semantic=None,
        sim_threshold: float | None = None,
    ) -> LearningPathResult:
        """生成学习路径。

        Args:
            candidate: CandidateProfile（候选人画像）
            position: PositionProfile（岗位画像）
            semantic: Sentence-BERT 相似度器（与匹配引擎共用）
            sim_threshold: 语义命中阈值，None 时从 configs/match_weights.json 读取
        """
        # 差距分析为同步函数，内部可能调用同步 SBERT similarity，放线程池避免阻塞事件循环
        gaps = await asyncio.to_thread(analyze_gaps, candidate, position, semantic, sim_threshold)
        path_gaps = [
            g for g in gaps if g.gap_type in (GapType.MISSING, GapType.WEAK)
        ][:_MAX_PATH_ITEMS]

        items: list[LearningPathItem] = []
        for gap in path_gaps:
            chain = prerequisite_chain(gap.skill)
            courses = await self._course_loader(
                gap.skill_id or "", gap.skill, _TOP_COURSES, semantic, sim_threshold
            )
            hours = sum(base_hours(s) for s in [gap.skill, *chain])
            items.append(
                LearningPathItem(
                    skill=gap.skill,
                    skill_id=gap.skill_id,
                    prerequisites=chain,
                    courses=courses,
                    estimated_hours=round(hours, 1),
                    priority=gap.priority,
                )
            )

        return LearningPathResult(gaps=gaps, items=items)
