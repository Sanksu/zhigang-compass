"""ARQ 异步任务定义。

任务类型（对齐设计文档 §4.4 ETL 管线）：
- ETL 编排：crawl_platform / run_etl_pipeline / validate_temporal / detect_inflation / snapshot_graph
- 业务异步：resume_parse / batch_extract

设计要点：
- 爬虫通过 subprocess 调用 `scrapy crawl`，避免 Twisted reactor 与 asyncio loop 冲突
- ETL 任务编排采用 fail-fast：任一阶段失败立即抛出，由 ARQ 重试机制兜底
- 时滞/通胀检测 M2 仅交付框架，M3 LLM 抽取上线后接入真实数据
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func

from app.core.config import settings
from app.services.alerting import send_alert

logger = logging.getLogger(__name__)

# 子进程 stdout/stderr 强制 UTF-8（中文 Windows 默认 GBK 管道，按 UTF-8 解码会乱码）
# 与 crawlers/spiders 下各 spider 调外部进程的模式一致
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# ── 爬虫项目根（backend/data/crawlers）──
_CRAWLERS_DIR = Path(__file__).resolve().parents[2] / "data" / "crawlers"
_OUTPUT_DIR = _CRAWLERS_DIR / "output"

# 爬虫子进程需 import crawlers 包（位于 backend/data/，scrapy.cfg 同目录），
# 显式设置 PYTHONPATH——worker 以服务方式启动时继承不到交互终端的 PYTHONPATH，
# 缺省会导致 `ModuleNotFoundError: No module named 'crawlers'`，全部爬虫静默失败
_CRAWL_ENV = {
    **_UTF8_ENV,
    "PYTHONPATH": os.pathsep.join(
        [str(_CRAWLERS_DIR.parent), str(_CRAWLERS_DIR.parent.parent)]
    ),
}

# 显式消费 -a max_results 参数的 spider（其余源由各自默认采集量控制）
# zhilian：08-13 新增条数上限（默认 200，spider 层 CloseSpider 截断）
MAX_RESULTS_SUPPORTED = {"arxiv", "zhilian"}

# CDP 爬虫：需连接真实 Chrome（9222）复用登录态，无登录态时会自动拉起浏览器
# （ensure_cdp_chrome），本地手动触发 ETL 可 skip_cdp=True 跳过，避免干扰用户浏览器
CDP_SPIDERS = {"boss", "monster", "glassdoor", "maimai"}

# 爬虫日志队列 key 前缀（Redis LIST，SSE 端点按 offset 增量拉取）
_CRAWL_LOG_PREFIX = "crawl:log:"
_CRAWL_LOG_TTL_SECONDS = 3600


async def _push_crawl_log(ctx: dict, task_id: str | None, line: str) -> None:
    """把爬虫输出行写入 Redis 日志队列（供 SSE 实时推送）。

    日志写入失败不阻断爬虫（仅丢失实时日志）；task_id 缺失（ETL 编排直接
    调用场景）跳过。ctx["redis"] 为 ARQ 注入的连接，缺失时跳过。
    """
    if not task_id or not line:
        return
    try:
        redis = ctx.get("redis")
        if redis is None:
            return
        key = _CRAWL_LOG_PREFIX + task_id
        # rpush + expire 合并为一次管道往返，避免日志高频写入时的双倍 RTT
        await redis.pipeline().rpush(key, line).expire(key, _CRAWL_LOG_TTL_SECONDS).execute()
    except Exception:
        pass


async def _update_crawl_task(task_id: str | None, **fields) -> None:
    """更新 crawl 任务状态（TaskStatus：running/success/failed + result/error）。

    任务不存在或 DB 不可用时静默跳过，不阻断爬虫（状态追踪为增强能力）。
    """
    if not task_id:
        return
    from app.core.database import async_session_factory
    from app.models.business import TaskStatus

    async with async_session_factory() as s:
        task = await s.get(TaskStatus, task_id)
        if task is None:
            return
        for k, v in fields.items():
            if k == "result" and isinstance(v, dict):
                # 合并而非覆盖：保留触发时写入的 platform/keyword（历史查询依赖）
                task.result = {**(task.result or {}), **v}
            else:
                setattr(task, k, v)
        await s.commit()


# ============================================================
# ETL 阶段任务
# ============================================================

# 单源爬虫超时上限（秒）：Playwright 渲染慢源（zhilian 8kw×5city 全量）正常
# 需 20-40min，但挂死（网络黑洞/风控验证码循环）会无限阻塞 ETL 阶段 1
# （08-13 实测 zhilian 挂死 8h，job 超时 kill 后 subprocess 成孤儿继续跑）。
# 900s 对齐 run_etl_pipeline 注释声明的单源上限；超时 kill 后已写入 jsonl
# 保留（Scrapy 逐行落盘），后续 load 仍消费已产出数据。
_CRAWL_TIMEOUT_SEC = 900

async def crawl_platform(
    ctx: dict,
    spider_name: str,
    keywords: list[str] | None = None,
    cities: list[str] | None = None,
    max_results: int | None = None,
    task_id: str | None = None,
) -> dict:
    """触发单个 Scrapy 爬虫。

    通过 subprocess 调用而非 in-process，原因：
    - Scrapy 基于 Twisted reactor，与 asyncio event loop 不兼容
    - subprocess 隔离崩溃，单爬虫失败不污染 worker

    单源超时：_CRAWL_TIMEOUT_SEC（900s）内未退出则 kill 子进程并报错——
    避免爬虫挂死无限阻塞 ETL 阶段 1（ARQ job 超时不会终止 subprocess，
    会残留孤儿爬虫继续打源站）。

    task_id 存在时（手动触发场景）：
    - 输出逐行写入 Redis 日志队列（SSE 端点 /admin/crawl/task/{task_id}/stream 实时推送）
    - 同步 TaskStatus 状态（running → success/failed），进度 0.1 → 1.0

    输出：output/{spider}_{YYYYMMDD_HHMMSS}.jsonl
    """
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    output_file = _OUTPUT_DIR / f"{spider_name}_{timestamp}.jsonl"
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"[crawl_platform] 任务开始: task_id={task_id} spider={spider_name} "
        f"keywords={keywords} cities={cities or '(默认)'} output={output_file}",
        flush=True,
    )

    cmd = [
        sys.executable, "-m", "scrapy", "crawl", spider_name,
        "-o", str(output_file),
    ]
    if keywords:
        cmd.extend(["-a", f"keywords={','.join(keywords)}"])
    if cities:
        cmd.extend(["-a", f"cities={','.join(cities)}"])
    # max_results 仅 arxiv 等显式消费该参数的 spider 生效，其余忽略并提示（避免静默失效）
    if max_results:
        if spider_name in MAX_RESULTS_SUPPORTED:
            cmd.extend(["-a", f"max_results={max_results}"])
        else:
            print(f"[crawl_platform] spider={spider_name} 不支持 max_results，参数已忽略", flush=True)
    print(f"[crawl_platform] 完整命令: {' '.join(cmd)}", flush=True)

    await _update_crawl_task(
        task_id,
        status="running",
        progress=0.1,
        result={"spider": spider_name, "output_file": str(output_file)},
    )

    # cwd 设到 crawlers/ 让 scrapy.cfg 生效；env 强制 UTF-8 + PYTHONPATH（见 _CRAWL_ENV）
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_CRAWLERS_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_CRAWL_ENV,
        )
    except Exception as e:
        msg = f"启动爬虫子进程失败: {e}"
        print(f"[crawl_platform] {msg}", flush=True)
        await _update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_failed", msg, spider=spider_name)
        raise RuntimeError(msg) from e
    print(f"[crawl_platform] 子进程已启动: task_id={task_id} pid={getattr(proc, 'pid', '?')}", flush=True)

    # 并发逐行读取 stdout/stderr：实时写入日志队列，stderr 尾部留存用于失败信息
    stderr_tail: list[str] = []

    async def _drain(stream, tail: list[str] | None = None):
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            await _push_crawl_log(ctx, task_id, text)
            if tail is not None:
                tail.append(text)
                if len(tail) > 200:
                    tail.pop(0)

    await asyncio.gather(
        _drain(proc.stdout),
        _drain(proc.stderr, stderr_tail),
    )
    # 单源超时保护（P0-1）：爬虫挂死时 kill 子进程，避免 ETL 阶段 1 无限阻塞；
    # 已写入 jsonl 保留（Scrapy 逐行落盘），后续 load 消费已产出数据
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=_CRAWL_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        msg = f"爬虫 {spider_name} 超时（>{_CRAWL_TIMEOUT_SEC}s），已强制终止"
        print(f"[crawl_platform] 任务超时: task_id={task_id} {msg}", flush=True)
        await _update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_timeout", msg, spider=spider_name)
        raise RuntimeError(msg)
    print(f"[crawl_platform] 子进程退出: task_id={task_id} returncode={returncode}", flush=True)

    if returncode != 0:
        detail = "\n".join(stderr_tail[-20:])[-2000:]
        msg = f"爬虫 {spider_name} 退出码 {returncode}: {detail}"
        print(f"[crawl_platform] 任务失败: task_id={task_id} {msg[:300]}", flush=True)
        await _update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_failed", msg, spider=spider_name, exit_code=returncode)
        raise RuntimeError(msg)

    # 统计产出条数（按行数）
    line_count = 0
    if output_file.exists():
        with output_file.open(encoding="utf-8") as f:
            line_count = sum(1 for _ in f)

    # 退出码 0 但无产出视为失败：爬虫"跑通"但未拿到数据，不能显示成功
    if line_count == 0:
        detail = "\n".join(stderr_tail[-20:])[-2000:]
        msg = f"爬虫 {spider_name} 产出 0 条数据: {detail}"
        print(f"[crawl_platform] 任务失败（无产出）: task_id={task_id} {msg[:300]}", flush=True)
        await _update_crawl_task(task_id, status="failed", error=msg[:500])
        await send_alert("crawl_failed", msg, spider=spider_name, items=0)
        raise RuntimeError(msg)

    print(f"[crawl_platform] 任务成功: task_id={task_id} spider={spider_name} items={line_count}", flush=True)

    await _update_crawl_task(
        task_id,
        status="success",
        progress=1.0,
        result={
            "spider": spider_name,
            "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
            "items": line_count,
            "crawled_at": timestamp,
        },
    )

    return {
        "spider": spider_name,
        "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
        "items": line_count,
        "crawled_at": timestamp,
    }


# ============================================================
# 时滞 / 通胀检测辅助函数（设计文档 §4.7/4.8，M3 接入 jd_raw）
# ============================================================

# 与 extraction/schemas.py REQUIRESRelation.level 对齐的岗位级别集合
_QUALITY_LEVELS = {"初级", "中级", "高级", "资深", "专家"}


def _extraction_of(row) -> dict | None:
    """从 jd_raw 行取 LLM 抽取结果（snapshot.extraction），缺失返回 None。"""
    snap = row.snapshot or {}
    ext = snap.get("extraction")
    return ext if isinstance(ext, dict) else None


def _skills_of(ext: dict) -> list[str]:
    """抽取结果的技能名列表（requirements 优先，缺省 skills）。"""
    reqs = ext.get("requirements") or []
    if reqs:
        return [r.get("skill_name", "") for r in reqs if r.get("skill_name")]
    return [s.get("name", "") for s in (ext.get("skills") or []) if s.get("name")]


def _publish_date(snapshot: dict, crawled_at: str) -> date | None:
    """解析发布日期：snapshot.post_date 优先，缺省用 crawled_at；无法解析返回 None。"""
    raw = str(snapshot.get("post_date") or crawled_at or "")[:19]
    # 空格分隔时间格式（智联等源，占库内 46%）：缺此格式会导致时滞检测
    # 把合法日期误标 no_skills_or_publish_date 跳过
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _skill_first_seen_days(
    group: list[tuple[int, date, list[str]]],
    skills: list[str],
    today: date,
    graph_first_seen: dict[str, date] | None = None,
) -> list[int]:
    """技能首见时长（天）。

    优先读图谱 Skill.first_seen（全局首次入图时间，G-02 主口径）；图谱无
    该技能首见记录（存量节点无属性/未入图）时回退同岗位 jd_raw 最早出现
    日期近似。group: 同岗位已抽取记录 (jd_id, publish_date, skills)。
    某技能两种来源均无记录时不计入（数据不足不武断判定）。
    """
    from app.services.extraction.post_processor import canonical_skill_name

    ages = []
    for skill in skills:
        first = None
        if graph_first_seen:
            first = graph_first_seen.get(canonical_skill_name(skill))
        if first is None:
            for _, pdate, group_skills in group:
                if skill in group_skills and (first is None or pdate < first):
                    first = pdate
        if first is not None:
            ages.append(max(0, (today - first).days))
    return ages


def _graph_skill_first_seen(skills: Iterable[str]) -> dict[str, date]:
    """从图谱读 Skill.first_seen（首次入图时间，G-02）→ 归一化技能名 → 日期。

    技能节点按归一化名存储（canonical_skill_name），未归一化的原始名查询
    会 miss，故先归一化再匹配；无 first_seen 的存量节点跳过（回退 jd_raw）。
    图谱不可达时返回空 dict（回退 jd_raw 推算）：validate_temporal 原本为
    纯 PG 依赖，不因本次加读图而引入 Neo4j 强依赖（backfill 脚本可独立运行）。
    """
    import logging

    from app.core.database import neo4j_driver
    from app.services.extraction.post_processor import canonical_skill_name

    logger = logging.getLogger(__name__)
    names = {canonical_skill_name(s) for s in skills if canonical_skill_name(s)}
    if not names:
        logger.info("_graph_skill_first_seen: 无有效技能名，跳过读图（空映射，回退 jd_raw）")
        return {}
    logger.info(
        "_graph_skill_first_seen: 技能请求=%d 归一化去重后=%d",
        len(skills), len(names),
    )
    try:
        with neo4j_driver.session() as session:
            rows = session.run(
                "MATCH (s:Skill) WHERE s.name IN $names "
                "RETURN s.name AS name, s.first_seen AS first_seen",
                names=list(names),
            ).data()
    except Exception as exc:
        # 图谱不可达（懒连接失败/服务停止）不阻断时滞检测，回退 jd_raw 推算
        logger.warning(
            "_graph_skill_first_seen: 图谱不可达，回退 jd_raw 推算: %s: %s",
            type(exc).__name__, exc,
        )
        return {}
    out: dict[str, date] = {}
    parse_failed: list[str] = []
    for r in rows:
        raw = r.get("first_seen")
        if not raw:
            continue
        try:
            out[r["name"]] = datetime.fromisoformat(str(raw)).date()
        except ValueError:
            parse_failed.append(r["name"])
    missing = sorted(names - set(out))
    logger.info(
        "_graph_skill_first_seen: 图谱命中=%d/%d%s",
        len(out), len(names),
        "" if not missing else f"，缺失 {len(missing)} 个将回退 jd_raw: {missing[:10]}"
        + ("" if len(missing) <= 10 else f" 等共 {len(missing)} 个"),
    )
    if parse_failed:
        logger.warning(
            "_graph_skill_first_seen: %d 个技能 first_seen 解析失败被跳过（回退 jd_raw）: %s",
            len(parse_failed), parse_failed[:10],
        )
    return out


def _experience_years(snapshot: dict) -> int | None:
    """解析经验要求最小年限（如 "3-5年" → 3）；无法解析返回 None。"""
    import re

    m = re.search(r"(\d+)", str(snapshot.get("experience") or ""))
    return int(m.group(1)) if m else None


def _history_skill_sets(group: list[tuple[int, date, list[str]]], jd_id: int) -> list[set[str]]:
    """同岗位历史 JD 的技能集合（按发布时间升序），排除当前 JD 自身。

    僵尸 JD 判定依赖"连续 N 期技能几乎不变"，与当前技能完全相同的历期
    （Jaccard=1.0）是最强信号，必须保留参与相似度计数；仅排除当前 JD 自身
    （原实现 `if gs != skills` 误排除了完全相同技能的历期，
    导致 detect_zombie_jd 的连续周期永远数不足 4 期，僵尸检测失效）。
    """
    return [
        set(gs)
        for r_id, pdate, gs in sorted(group, key=lambda g: g[1])
        if r_id != jd_id
    ]


def _snapshot_with_skip(snapshot: dict | None, key: str, reason: str) -> dict:
    """复制 snapshot 并写入检测跳过标记（数据不足，游标收敛用，不做判定）。

    时滞/通胀检测对数据不足的 JD 不做武断判定，但若不写标记，
    `snapshot[key] is None` 游标会反复选中这些 JD，每次 ETL 空转。
    skipped 标记不含 decay_weight，聚合层 `_jd_decay_weight` 视为 1.0。
    """
    snap = dict(snapshot or {})
    snap[key] = {"skipped": True, "reason": reason}
    return snap


async def validate_temporal(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 200,
) -> dict:
    """时滞检测（设计文档 §4.7）：jd_raw 已抽取记录接入 SAI/僵尸/抄袭检测。

    技能首见时长优先读图谱 Skill.first_seen（G-02，全局首次入图时间），
    图谱缺失时回退同岗位 jd_raw 历史最早出现日期近似。
    检测结果写回 `snapshot["validation"]`（含三类结果 + 叠加降权系数）；
    数据不足（无技能/无发布日期）的 JD 跳过，不做武断判定。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.temporal_detector import (
        RECENT_WINDOW_DAYS,
        apply_temporal_decay,
        classify_sai,
        compute_sai,
        detect_plagiarism,
        detect_zombie_jd,
    )
    from app.services.data_quality.schemas import JDSkillSet

    today = date.today()
    async with async_session_factory() as session:
        stmt = select(JDRaw).where(
            JDRaw.snapshot["extraction"].astext.isnot(None),
            # 游标：仅处理未做时滞检测的记录（幂等，重复执行不空转旧数据）
            JDRaw.snapshot["validation"].astext.is_(None),
        )
        if jd_ids:
            stmt = stmt.where(JDRaw.id.in_(jd_ids))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()).limit(limit))).all()

        results: dict = {"checked": 0, "skipped": 0, "flagged": []}
        # 已抽取记录视图：(jd_id, position, publish_date, skills)
        views = []
        for row in rows:
            ext = _extraction_of(row)
            if not ext:
                results["skipped"] += 1
                continue
            publish = _publish_date(row.snapshot or {}, row.crawled_at or "")
            skills = _skills_of(ext)
            if not skills or publish is None:
                row.snapshot = _snapshot_with_skip(row.snapshot, "validation", "no_skills_or_publish_date")
                results["skipped"] += 1
                continue
            views.append((row, (row.id, ext.get("position_name") or "", publish, skills)))

        # 历史组补齐：时滞检测的技能首见时长/抄袭比对需要同岗位全量历史
        # （含此前已验证批次）。仅用本次未验证的 limit 条记录，首见时长会被
        # 低估、抄袭比对缺参照（审查 major：validate_temporal 历史组不齐）。
        position_names = {v[1][1] for v in views}
        hist_by_pos: dict[str, list[tuple[int, date, list[str]]]] = {}
        if position_names:
            hist = (await session.scalars(
                select(JDRaw).where(
                    JDRaw.snapshot["extraction"].astext.isnot(None),
                    JDRaw.snapshot["extraction"]["position_name"].astext.in_(position_names),
                )
            )).all()
            for row in hist:
                ext = _extraction_of(row)
                if not ext:
                    continue
                publish = _publish_date(row.snapshot or {}, row.crawled_at or "")
                skills = _skills_of(ext)
                if not skills or publish is None:
                    continue
                pos = ext.get("position_name") or ""
                hist_by_pos.setdefault(pos, []).append((row.id, publish, skills))

        # 图谱 Skill.first_seen 一次性读取（G-02 主口径）：当前批次 + 历史组
        # 全部技能名批量查询，避免逐技能 N+1 查询
        all_skills: set[str] = set()
        for _, (_, _, _, v_skills) in views:
            all_skills.update(v_skills)
        for grp in hist_by_pos.values():
            for _, _, gs in grp:
                all_skills.update(gs)
        graph_first_seen = _graph_skill_first_seen(all_skills) if all_skills else {}

        for row, (jd_id, position, publish, skills) in views:
            # group 覆盖同岗位全部历史记录（含当前批次），按首见时长/抄袭比对口径
            group = hist_by_pos.get(position, [])
            skill_ages = _skill_first_seen_days(group, skills, today, graph_first_seen)
            if not skill_ages:
                row.snapshot = _snapshot_with_skip(row.snapshot, "validation", "no_skill_first_seen_ages")
                results["skipped"] += 1
                continue

            # 同岗位近 90 天窗口的技能首见时长聚合，作为 SAI 基线
            recent_ages = [
                age
                for _, pdate, gs in group
                if (today - pdate).days <= RECENT_WINDOW_DAYS
                for age in _skill_first_seen_days(group, gs, today, graph_first_seen)
            ]
            sai = classify_sai(compute_sai(skill_ages, recent_ages))

            history_skills = _history_skill_sets(group, jd_id)
            zombie = detect_zombie_jd(history_skills, set(skills), sai.sai)

            oldest = min(group, key=lambda g: g[1])
            plagiarism = None
            if oldest[0] != jd_id:
                plagiarism = detect_plagiarism(
                    JDSkillSet(jd_id=str(jd_id), position_name=position, publish_date=publish, skills=skills),
                    JDSkillSet(jd_id=str(oldest[0]), position_name=position, publish_date=oldest[1], skills=oldest[2]),
                )

            decay = apply_temporal_decay(1.0, sai, zombie, plagiarism)
            snap = dict(row.snapshot or {})
            snap["validation"] = {
                "sai": sai.model_dump(),
                "zombie": zombie.model_dump(),
                "plagiarism": plagiarism.model_dump() if plagiarism else None,
                "decay_weight": decay,
            }
            row.snapshot = snap
            results["checked"] += 1
            flagged = sai.label != "fresh" or zombie.is_zombie or (plagiarism is not None and plagiarism.is_plagiarism)
            if flagged:
                results["flagged"].append({
                    "jd_id": jd_id,
                    "position": position,
                    "sai": sai.label,
                    "zombie": zombie.is_zombie,
                    "plagiarism": plagiarism.is_plagiarism if plagiarism else False,
                    "decay_weight": decay,
                })
        await session.commit()

    return results


async def detect_inflation(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 200,
) -> dict:
    """通胀检测（设计文档 §4.8）：从 jd_raw + LLM 抽取结果接入四维通胀评分。

    输入：extraction.level（岗位级别）/ education / requirements（数量 + 专家级数量）
         + snapshot.experience（最小年限，如 "3-5年" → 3）。
    结果写回 `snapshot["inflation"]`（含四维分 / inflation_score / label / decay_weight）。
    缺岗位级别或经验解析失败的 JD 跳过，不做武断判定。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.inflation_detector import compute_inflation_score

    async with async_session_factory() as session:
        stmt = select(JDRaw).where(
            JDRaw.snapshot["extraction"].astext.isnot(None),
            # 游标：仅处理未做通胀检测的记录（幂等，重复执行不空转旧数据）
            JDRaw.snapshot["inflation"].astext.is_(None),
        )
        if jd_ids:
            stmt = stmt.where(JDRaw.id.in_(jd_ids))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()).limit(limit))).all()

        results: dict = {"checked": 0, "skipped": 0, "flagged": []}
        for row in rows:
            ext = _extraction_of(row)
            if not ext:
                results["skipped"] += 1
                continue
            level = ext.get("level") or ""
            if level not in _QUALITY_LEVELS:
                row.snapshot = _snapshot_with_skip(row.snapshot, "inflation", "no_level")
                results["skipped"] += 1
                continue
            min_years = _experience_years(row.snapshot or {})
            if min_years is None:
                row.snapshot = _snapshot_with_skip(row.snapshot, "inflation", "no_experience")
                results["skipped"] += 1
                continue

            reqs = ext.get("requirements") or []
            skill_count = len(reqs) if reqs else len(ext.get("skills") or [])
            expert_count = sum(1 for r in reqs if r.get("level") == "专家")
            edu = (ext.get("education") or {}).get("level") or "不限"
            inflation = compute_inflation_score(level, min_years, skill_count, expert_count, edu)

            snap = dict(row.snapshot or {})
            snap["inflation"] = inflation.model_dump()
            row.snapshot = snap
            results["checked"] += 1
            if inflation.label != "normal":
                results["flagged"].append({
                    "jd_id": row.id,
                    "label": inflation.label,
                    "inflation_score": inflation.inflation_score,
                })
        await session.commit()

    return results


async def dedup_simhash(ctx: dict, limit: int | None = None) -> dict:
    """SimHash 跨平台近似去重（设计文档 §4.2 消费方）。

    扫描 jd_raw 已入库记录的 snapshot->_simhash（CleaningPipeline 采集时写入，
    基于脱敏后文本），批量 find_similar_pairs（汉明距 ≤ 3）找出近似重复 JD。
    jd_embeddings 语义辅助（§11.4.3）：两记录的向量余弦 < 0.9 视为语义不相似，
    不标记重复（降低 SimHash 误判）；向量缺失时保留 SimHash 判定。
    将后入库记录标记 `snapshot["_duplicate_of"]` = 先入库记录 id。
    聚合层（aggregation.build_aggregates）跳过被标记记录，避免重复 JD 虚高频次。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.simhash import find_similar_pairs
    from app.services.embeddings.vector_store import load_jd_vectors
    from app.services.matching.semantic import cosine_similarity

    # JD 语义去重辅助阈值（§11.4.3 jd_embeddings Cosine）：低于该值不标记
    _EMBED_DEDUP_THRESHOLD = 0.9

    async with async_session_factory() as session:
        # 只加载带 _simhash 的记录，避免全表拉取（审查 major：dedup_simhash 全表加载）
        stmt = select(JDRaw).where(
            JDRaw.snapshot["_simhash"].astext.isnot(None),
        )
        if limit:
            stmt = stmt.limit(limit)
        stmt = stmt.order_by(JDRaw.id.asc())
        rows = (await session.scalars(stmt)).all()

        records: list[tuple[str, int]] = []
        for r in rows:
            sh = (r.snapshot or {}).get("_simhash")
            if isinstance(sh, int) and sh:
                records.append((str(r.id), sh))

        pairs = find_similar_pairs(records)

        # 语义辅助：SimHash 命中对用 jd_embeddings 向量校验（缺向量不拦截）
        emb_map = await load_jd_vectors(session)
        verified_pairs: list[tuple[str, str]] = []
        skipped_emb = 0
        for id_a, id_b in pairs:
            va, vb = emb_map.get(id_a), emb_map.get(id_b)
            if va is not None and vb is not None:
                if cosine_similarity(va, vb) < _EMBED_DEDUP_THRESHOLD:
                    skipped_emb += 1
                    continue  # 语义不相似，SimHash 误判，不标记重复
            verified_pairs.append((id_a, id_b))

        # pairs 顺序即 records 输入顺序（id 升序），先入库者保留，后入库者标记
        id_map = {str(r.id): r for r in rows}
        marked = 0
        for id_a, id_b in verified_pairs:
            dup = id_map.get(id_b)
            if dup is None:
                continue
            snap = dict(dup.snapshot or {})
            if snap.get("_duplicate_of") != id_a:
                snap["_duplicate_of"] = id_a
                dup.snapshot = snap
                marked += 1
        await session.commit()

    return {
        "checked": len(records),
        "pairs": len(pairs),
        "skipped_emb": skipped_emb,
        "marked": marked,
    }


async def load_courses(ctx: dict) -> dict:
    """课程数据入图（course_raw → Course/Skill 节点 + LEARNABLE_VIA 关系）。

    遍历 course_raw.snapshot 调 import_course（Neo4j MERGE 幂等，重复执行
    不产生重复节点）。单条失败不阻塞整体（批量语义）。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import CourseRaw
    from app.services.kg.kg_service import import_course

    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw))).all()
    data = [dict(r.snapshot or {}) for r in rows]

    def _import_all():
        # 同步 Neo4j 写入放线程池，避免阻塞 ARQ 事件循环（Redis 心跳超时崩溃根因）
        imported = 0
        failed = 0
        with neo4j_driver.session() as neo4j_session:
            for course_data in data:
                try:
                    import_course(neo4j_session, course_data)
                    imported += 1
                except Exception:
                    failed += 1
        return imported, failed

    imported, failed = await asyncio.to_thread(_import_all)
    return {"total": len(data), "imported": imported, "failed": failed}


async def evaluate_courses(ctx: dict) -> dict:
    """课程质量评估（DA-M4-01，设计文档 §4.6）。

    遍历 course_raw 全量课程 → 六维加权质量评分 → 幂等写回
    `snapshot["quality"]`（覆盖更新）。返回推荐池统计，供学习路径取 Top-3。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CourseRaw
    from app.services.data_quality.course_quality import (
        RECOMMEND_MIN_SCORE,
        evaluate_course,
    )

    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw).order_by(CourseRaw.id.asc()))).all()
        results = []
        for row in rows:
            snap = dict(row.snapshot or {})
            result = evaluate_course(snap)
            snap["quality"] = result.model_dump()
            row.snapshot = snap
            results.append(result)
        await session.commit()

    recommended = [r for r in results if r.recommended]
    return {
        "total": len(results),
        "recommended": len(recommended),
        "recommend_min_score": RECOMMEND_MIN_SCORE,
        "top3": [r.model_dump() for r in sorted(results, key=lambda r: r.quality_score, reverse=True)[:3]],
    }


async def diversity_report(ctx: dict, top_n: int = 10) -> dict:
    """数据多样性报告（DA-M4-02）。

    聚合四类 raw 表多样性指标，写入 reports/diversity_{date}.json（幂等覆盖）。
    指标口径见 app/services/data_quality/diversity.py。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
    from app.services.data_quality.diversity import (
        course_diversity,
        dedup_stats,
        position_diversity,
        source_distribution,
    )

    async def _jd_items(rows):
        items = []
        for r in rows:
            ext = (r.snapshot or {}).get("extraction") or {}
            name = (ext.get("position_name") or "").strip()
            if not name:
                continue
            skills = [s.get("name") for s in (ext.get("skills") or []) if s.get("name")]
            items.append({"position_name": name, "skills": skills})
        return items

    async def _course_items(rows):
        items = []
        for r in rows:
            snap = r.snapshot or {}
            skills = snap.get("skills") or []
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            items.append({"platform": snap.get("platform", r.source), "skills": skills})
        return items

    async with async_session_factory() as session:
        jd_rows = (await session.scalars(select(JDRaw))).all()
        course_rows = (await session.scalars(select(CourseRaw))).all()
        paper_rows = (await session.scalars(select(PaperRaw))).all()
        community_rows = (await session.scalars(select(CommunityRaw))).all()

    report = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "jd": {
            "total": len(jd_rows),
            "sources": source_distribution([{"source": r.source} for r in jd_rows]),
            "dedup": dedup_stats([{"fingerprint": r.fingerprint} for r in jd_rows]),
            "positions": position_diversity(await _jd_items(jd_rows), top_n=top_n),
        },
        "course": {
            **course_diversity(await _course_items(course_rows)),
            "dedup": dedup_stats([{"fingerprint": r.fingerprint} for r in course_rows]),
        },
        "paper": {
            "total": len(paper_rows),
            "sources": source_distribution([{"source": r.source} for r in paper_rows]),
        },
        "community": {
            "total": len(community_rows),
            "sources": source_distribution([{"source": r.source} for r in community_rows]),
        },
    }

    report_dir = Path(__file__).resolve().parents[2] / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"diversity_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[diversity_report] 报告已写入: {report_path}", flush=True)
    return {"report_path": str(report_path)}


async def check_data_freshness(ctx: dict) -> dict:
    """数据更新新鲜度检查（DA-M4-03，设计文档 T+1 承诺）。

    按来源聚合四类 raw 表最新抓取时间，判定平台级新鲜度（≤1 天），
    写入 reports/freshness_{date}.json。过期来源返回在结果中供告警。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
    from app.services.data_quality.update_status import platform_freshness

    async def _rows(model):
        async with async_session_factory() as session:
            return (await session.scalars(select(model))).all()

    def _section(rows):
        return platform_freshness(
            [{"source": r.source, "crawled_at": r.crawled_at} for r in rows]
        )

    report = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "jd": _section(await _rows(JDRaw)),
        "course": _section(await _rows(CourseRaw)),
        "paper": _section(await _rows(PaperRaw)),
        "community": _section(await _rows(CommunityRaw)),
    }

    report_dir = Path(__file__).resolve().parents[2] / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"freshness_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    stale = [
        f"{name}:{source}"
        for name in ("jd", "course", "paper", "community")
        for source in report[name]["stale_sources"]
    ]
    if stale:
        # T+1 承诺被破坏时告警，避免数据过期无人感知（§4.4 / DA-M4-03）
        await send_alert(
            "data_stale",
            f"数据过期来源（超过 T+1）: {', '.join(stale)}",
            stale_sources=stale,
            report_path=str(report_path),
        )
    print(f"[check_data_freshness] 报告已写入: {report_path} 过期来源: {stale}", flush=True)
    return {"report_path": str(report_path), "stale_sources": stale}


async def aggregate_positions(ctx: dict) -> dict:
    """岗位聚合（设计文档 §5.5）：jd_raw 抽取结果 → Position 热度 + REQUIRES 边权重。

    全量重算，幂等（覆盖写回 Neo4j）：
    - Position.freq / required_years / last_updated
    - REQUIRES.weight / necessity / source_count
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw
    from app.services.kg.aggregation import build_aggregates, write_aggregates

    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    agg = build_aggregates(rows)

    def _write():
        # 同步 Neo4j 写入放线程池，并正确关闭 session（原实现 session 泄漏）
        with neo4j_driver.session() as session:
            return write_aggregates(session, agg, now)

    return await asyncio.to_thread(_write)


async def cross_validate_jds(ctx: dict, limit: int | None = None) -> dict:
    """多平台交叉验证（DA-M3-03，设计文档 §4.5）。

    聚合 jd_raw 已抽取记录按归一化岗位名分组，校验技能一致性（≥2 源印证）、
    薪资异常、经验分歧、跨源置信度，结果写回 `snapshot["cross_validation"]`
    （幂等覆盖）。返回组级统计供管线审计。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.cross_validate import (
        build_position_groups,
        validate_group,
    )
    from app.services.extraction.dictionary import normalize_position_name

    async with async_session_factory() as session:
        stmt = select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()))).all()
        if limit:
            rows = rows[:limit]

        records = [
            {"snapshot": r.snapshot or {}, "source": r.source, "crawled_at": r.crawled_at}
            for r in rows
        ]

        def _validate():
            # 纯 CPU 分组校验，放线程池避免阻塞事件循环
            return [
                validate_group(pos, group)
                for pos, group in build_position_groups(records).items()
            ]

        results = await asyncio.to_thread(_validate)

        group_map = {r.position_name: r for r in results}
        written = 0
        for row in rows:
            ext = (row.snapshot or {}).get("extraction") or {}
            # 失真兜底族按 JD 技能路由，写回口径与 build_position_groups 保持一致
            result = group_map.get(normalize_position_name(
                ext.get("position_name") or "",
                skills=[s["name"] for s in (ext.get("skills") or [])
                        if isinstance(s, dict) and s.get("name")],
            ))
            if result is None:
                continue
            snap = dict(row.snapshot or {})
            snap["cross_validation"] = result.model_dump()
            row.snapshot = snap
            written += 1
        await session.commit()

    return {
        "groups": len(results),
        "multi_source": sum(1 for r in results if r.source_count >= 2),
        "verified": sum(1 for r in results if r.verified),
        "below_confidence": sum(1 for r in results if r.confidence < 0.6),
        "written": written,
    }


async def sync_skill_normalization(ctx: dict) -> dict:
    """技能归一化 + SIMILAR_TO 建边（设计文档 §5.3，ETL 阶段 9.5）。

    对图谱全量 Skill 名做 SBERT 层次聚类，回写 `Skill.normalized_name`，
    同簇相似度 ≥ 0.85 自动建 `SIMILAR_TO {similarity}` 关系（幂等 MERGE）。

    模型不可用时归一化退化为词典路径（normalize_skill 在线词典不变），
    不阻塞 ETL 主线。
    """

    def _run():
        # 同步 Neo4j 全量读取 + SBERT 聚类 + 关系回写为 CPU/IO 密集，整体放线程池
        from app.core.database import neo4j_driver
        from app.services.extraction.normalization import (
            SkillNormalizer,
            guard_cluster_distribution,
        )

        with neo4j_driver.session() as session:
            rows = session.run("MATCH (s:Skill) RETURN s.name AS name").data()
            names = [r["name"] for r in rows if r.get("name")]
        if not names:
            return {"skills": 0, "normalized": 0, "similar_pairs": 0, "detail": "图谱无 Skill 节点"}

        normalizer = SkillNormalizer()
        normalized = normalizer.normalize_many(names)
        if not normalized:
            return {"skills": len(names), "normalized": 0, "similar_pairs": 0, "detail": "归一化无输出"}

        # ── 写回前门禁（P0）：簇分布异常拒绝写库，防链式漂移污染图谱 ──
        # 08-13 事故：单链接漂移把 1185 个技能并入"2D可视化"簇后直接入库。
        # 门禁拦截同类异常：巨型簇 / 映射率越界 → 不写库 + 告警 + 返回 blocked
        # （单阶段失败不阻塞 ETL 主线，与 run_etl_pipeline 其余阶段同语义）。
        try:
            guard_cluster_distribution(normalized)  # 门禁校验：异常直接抛 ValueError 拦截
        except ValueError as e:
            msg = f"技能归一化门禁拦截：{e}"
            print(f"[sync_skill_normalization] {msg}", flush=True)
            from app.services.alerting import send_alert

            send_alert("normalization_blocked", msg)
            return {
                "skills": len(names),
                "normalized": 0,
                "similar_pairs": 0,
                "detail": msg,
                "blocked": True,
            }

        changed = sum(1 for n, r in normalized.items() if r.standard != n)
        written = 0
        skipped_standard = 0
        name_set = set(names)  # 图谱现存技能名：过滤 standard 不在图谱的对，避免 MERGE 空匹配丢边
        with neo4j_driver.session() as session:
            # 回写 normalized_name（含自指 SET，幂等）
            for name, res in normalized.items():
                session.run(
                    "MATCH (s:Skill {name: $name}) SET s.normalized_name = $standard",
                    name=name, standard=res.standard,
                )
            # SIMILAR_TO 关系：同标准名组内相似度 ≥ 0.85（§5.3 阈值过滤，非自指）
            for standard, member, sim in normalizer.similar_pairs(normalized):
                if standard not in name_set:
                    skipped_standard += 1
                    continue
                session.run(
                    """
                    MATCH (a:Skill {name: $standard}), (b:Skill {name: $member})
                    MERGE (a)-[r:SIMILAR_TO]->(b)
                    SET r.similarity = $similarity
                    """,
                    standard=standard, member=member, similarity=sim,
                )
                written += 1

        return {
            "skills": len(names),
            "normalized": changed,
            "similar_pairs": written,
            "skipped_standard": skipped_standard,
            "detail": "SIMILAR_TO 已回写（幂等）",
        }

    return await asyncio.to_thread(_run)


async def backfill_embeddings(ctx: dict) -> dict:
    """pgvector 三表向量回填（设计文档 §11.4.3，ETL 阶段 13）。

    从 Neo4j Skill、jd_raw、resume_cache 生成向量写入
    skill_embeddings / jd_embeddings / project_embeddings（幂等）。
    模型不可用时跳过，不阻塞 ETL 主线（语义路降级为关键词/内存相似度）。
    """
    from app.core.database import async_session_factory
    from app.services.embeddings.backfill import run_backfill
    from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder

    try:
        # 单例首次获取会同步加载 SBERT 模型（可达分钟级），放线程池避免阻塞事件循环
        embedder = await asyncio.to_thread(SkillEmbedder.get)
        async with async_session_factory() as db:
            return await run_backfill(db, embedder)
    except SemanticUnavailableError:
        return {"detail": "语义模型不可用，回填跳过"}


async def run_etl_pipeline(ctx: dict, run_date: str | None = None, skip_cdp: bool = False) -> dict:
    """编排完整 ETL 管线（设计文档 §4.4）。

    管线顺序：
        crawl_jds → clean_jds(已在 Scrapy Pipeline 内嵌) → dedup
        → validate_temporal → detect_inflation → structure → load_to_db
        → load_to_neo4j（含课程入图 + 岗位聚合）

    Args:
        run_date: 调度日期 YYYY-MM-DD，None 时取 UTC+8 当日
        skip_cdp: True 时跳过 CDP 爬虫（boss/monster/glassdoor/maimai，需真实
            Chrome 登录态），仅爬非 CDP 源——本地手动触发且无浏览器登录态时使用
    """
    if run_date is None:
        run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    # 按设计文档 §4.4 数据更新频率分组
    # 国内 A 级 + B 级（02:00 / 04:00）
    domestic_platforms = ["boss", "zhilian"]
    # 国际 A/B 级（错峰）。monster 的 DataDome 防护（IP 信誉 + 浏览器真实性双重
    # 校验）在容器环境实测不可绕过（headless/CDP/指纹伪装/xdotool 全被拦截），
    # 暂从自动采集列表移除；保留 spider 代码，待有住宅代理/指纹浏览器后再启用
    international_platforms = ["indeed", "glassdoor"]
    # 非招聘数据源（论文/社区/课程）
    trend_platforms = ["arxiv", "github", "stackoverflow"]

    crawl_platforms = domestic_platforms + international_platforms + trend_platforms
    if skip_cdp:
        crawl_platforms = [p for p in crawl_platforms if p not in CDP_SPIDERS]

    results: dict = {
        "run_date": run_date,
        "stages": {},
    }

    # ── 阶段 1：爬虫（A 级国内主源）──
    crawl_results = []
    for spider in crawl_platforms:
        try:
            r = await crawl_platform(ctx, spider)
            crawl_results.append(r)
        except Exception as e:
            # 单源失败不阻塞其他源（设计文档「单源失效不影响整体」）
            crawl_results.append({"spider": spider, "error": str(e)})
    results["stages"]["crawl"] = crawl_results

    # ── 阶段 2：清洗 + 去重 ──
    # 已嵌入 Scrapy CleaningPipeline（SHA256 指纹 upsert 即精确去重）
    # SimHash 近似去重（跨平台）由阶段 2.5 执行
    results["stages"]["clean_dedup"] = {
        "status": "embedded_in_scrapy_pipeline",
    }

    # ── 阶段 2.5：SimHash 跨平台近似去重（标记重复，聚合层跳过）──
    results["stages"]["dedup_simhash"] = await dedup_simhash(ctx)

    # ── 阶段 3：结构化 + 入库（M3 启用：LLM 抽取 → snapshot 写回 → Neo4j 入图）──
    results["stages"]["structure_load"] = await batch_extract(ctx, limit=500)

    # ── 阶段 4：时滞检测（M3 启用，须在抽取之后：依赖 snapshot.extraction）──
    results["stages"]["validate_temporal"] = await validate_temporal(ctx, jd_ids=[])

    # ── 阶段 5：通胀检测（M3 启用，须在抽取之后：依赖 snapshot.extraction）──
    results["stages"]["detect_inflation"] = await detect_inflation(ctx, jd_ids=[])

    # ── 阶段 6：课程入图（course_raw → Course + LEARNABLE_VIA）──
    results["stages"]["load_courses"] = await load_courses(ctx)

    # ── 阶段 7：课程质量评估（DA-M4-01，六维加权 → 推荐池写回 snapshot["quality"]）──
    results["stages"]["evaluate_courses"] = await evaluate_courses(ctx)

    # ── 阶段 8：岗位聚合（Position.freq + REQUIRES weight/source_count）──
    results["stages"]["aggregate_positions"] = await aggregate_positions(ctx)

    # ── 阶段 9：多平台交叉验证（DA-M3-03，技能跨源印证/薪资异常/置信度）──
    results["stages"]["cross_validate"] = await cross_validate_jds(ctx)

    # ── 阶段 9.5：技能归一化 + SIMILAR_TO 建边（§5.3，SBERT 聚类，幂等）──
    results["stages"]["skill_normalization"] = await sync_skill_normalization(ctx)

    # ── 阶段 10：数据多样性报告（DA-M4-02，reports/diversity_{date}.json）──
    results["stages"]["diversity_report"] = await diversity_report(ctx)

    # ── 阶段 11：数据更新新鲜度检查（DA-M4-03，T+1 承诺审计）──
    results["stages"]["check_data_freshness"] = await check_data_freshness(ctx)

    # ── 阶段 12.5：技能关系建边（§5.1：PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF，字典驱动，幂等）──
    from app.services.kg.skill_relations import sync_skill_relations
    from app.core.database import neo4j_driver as _neo4j_driver

    def _run_skill_relations() -> dict:
        # 同步 Neo4j 全量建边放线程池，避免阻塞事件循环（ARQ 心跳超时）
        with _neo4j_driver.session() as _ns:
            return sync_skill_relations(_ns)

    results["stages"]["skill_relations"] = await asyncio.to_thread(_run_skill_relations)

    # ── 阶段 12.6：岗位演化关系推导（§5.1：EVOLVED_FROM，版本 diff，幂等）──
    from app.services.evolution.evolved_from import derive_evolved_from

    results["stages"]["evolved_from"] = await derive_evolved_from()

    # ── 阶段 13：pgvector 三表向量回填（§11.4.3，模型不可用时跳过）──
    results["stages"]["backfill_embeddings"] = await backfill_embeddings(ctx)

    # ── 阶段 14：发布图谱版本快照（§7.1 T+1 版本管理）──
    # 置于 skill_relations/evolved_from 之后：快照须覆盖本管线全部图变更，
    # 否则发布的版本缺失 SIMILAR_TO/EVOLVED_FROM 等边（审查 major：snapshot 顺序）。
    results["stages"]["snapshot_graph"] = await snapshot_graph(ctx, triggered_by="scheduled")

    # ── 阶段 15：新岗位发现 + 自动状态流转（须在快照发布后：依赖当日窗口序列）──
    # 链入 ETL 而非独立 cron，保证 discovery_auto_transition 读到当日快照。
    # 单侧失败仅记录，不阻塞 ETL 整体（ETL 结果可审计）。
    try:
        results["stages"]["discovery_daily"] = await discovery_daily(ctx)
        results["stages"]["discovery_auto_transition"] = await discovery_auto_transition(ctx)
    except Exception as e:
        results["stages"]["discovery"] = {"error": str(e)[:500]}

    return results


# ============================================================
# 业务异步任务（M3/M4 实现）
# ============================================================

async def resume_parse(ctx: dict, file_path: str, task_id: str | None = None) -> dict:
    """简历解析异步任务（M4 实现）。

    流程：文件文本抽取 → PII 脱敏 → LLM 抽取（规则兜底）→ 画像落库 resume_cache。
    - 结果按 file_hash upsert 到 resume_cache（幂等，重复执行覆盖更新）
    - 任务状态经 TaskStatus 追踪（parse_resume 路由入队时携带 task_id）
    - 任一环节失败标记 task failed 并记录错误，不做假成功返回
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.business import ResumeCache, TaskStatus
    from app.services.resume.extractor import ResumeExtractor
    from app.services.resume.file_parser import extract_text
    from app.services.resume.pii_mask import mask_pii, restore_pii

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id) if task_id else None
        if task is None:
            # 兼容未携带 task_id 的旧入队：按 result.file_path 定位
            task = await session.scalar(
                select(TaskStatus).where(
                    TaskStatus.result["file_path"].astext == str(file_path)
                )
            )
        if task is None:
            return {"status": "failed", "error": "TaskStatus 不存在"}

        result_info = task.result or {}
        task.status = "running"
        task.progress = 0.2
        await session.commit()

        try:
            # 1. 文件文本抽取（pdf/docx/txt；扫描件抛 ResumeParseError）
            text = await asyncio.to_thread(extract_text, file_path)

            # 2. PII 脱敏（送入 LLM 前必须先脱敏，设计文档 §8.2）
            masked, pii_mapping = await asyncio.to_thread(mask_pii, text)

            # 3. LLM 结构化抽取（无 api_key / 全 provider 失败降级规则抽取）。
            #    同步 LLM 网络调用放线程池，避免阻塞 ARQ 事件循环（Redis 心跳超时崩溃）
            task.progress = 0.6
            await session.commit()
            result = await asyncio.to_thread(ResumeExtractor().extract, masked)

            # 4. 占位符回填为原始值（设计文档 §8.2：LLM 抽取完成后经映射表回填）。
            #    映射仅当前任务内存存活，不外泄日志；回填后画像含真实联系方式，
            #    受 resume/match 端点 user+ 认证保护
            parsed = await asyncio.to_thread(restore_pii, result.model_dump(), pii_mapping)
            cache = await session.scalar(
                select(ResumeCache).where(ResumeCache.file_hash == result_info["file_hash"])
            )
            if cache is None:
                # id 复用任务 id（task.id）：上传端 resume_files.resume_id = task.id，
                # 归属校验（match/recommend `_owns_resume` 查 resume_files）依赖
                # cache.id == resume_files.resume_id 一致；否则新简历无法发起匹配，
                # 报"无权使用该简历发起匹配"（2026-08-09 修复）。
                cache = ResumeCache(
                    id=task.id,
                    file_hash=result_info["file_hash"],
                    file_name=result_info.get("file_name") or Path(file_path).name,
                    parsed_data=parsed,
                )
                session.add(cache)
            else:
                cache.parsed_data = parsed
                cache.version += 1
            await session.flush()

            task.status = "success"
            task.progress = 1.0
            certs = [c for c in parsed.get("certifications", []) if c.get("name")]
            logger.info(
                "resume_parse 完成：resume_id=%s 技能=%d 证书=%d 证书明细=%s",
                str(cache.id),
                len(parsed.get("skills", [])),
                len(certs),
                [{ "name": c.get("name"), "issuer": c.get("issuer", "") } for c in certs[:10]],
            )
            task.result = {
                "resume_id": str(cache.id),
                "skills": [s.get("name") for s in parsed.get("skills", []) if s.get("name")],
            }
        except Exception as e:
            task.status = "failed"
            task.error = str(e)[:500]
        await session.commit()

        if task.status == "success":
            return {"status": "success", "resume_id": task.result["resume_id"]}
        return {"status": "failed", "error": task.error}


# 匹配结果 Redis 快照 TTL（与 match.py 对齐：24h，供 result/gap/path/feedback 查询）
_MATCH_RESULT_TTL = 24 * 60 * 60


def _complete_recommend_result(prev: dict | None, match_id: str, top_n: int) -> dict:
    """任务成功结果：合并入队时的归属字段（user_id/resume_id），追加 match_id/top_n。

    不得整体覆盖 result——入队时 result 含 user_id，GET /match/task 的归属校验
    （match.py match_task_status）依赖它；覆盖会导致任务成功后归属校验恒失败，
    前端轮询报"匹配任务不存在或已过期"。
    """
    return {**(prev or {}), "match_id": match_id, "top_n": top_n}


async def match_recommend(
    ctx: dict,
    resume_id: str,
    top_n: int = 10,
    task_id: str | None = None,
    user_id: str = "",
) -> dict:
    """自动推荐 Top-N 岗位（异步任务，落地 §2.4.4 202 + task_id 契约）。

    流程：resume_cache 候选人画像 → 图谱岗位画像 → RuleBasedMatcher 匹配 →
    结果写 Redis match:result（TTL 24h）+ match_results 幂等落库（§11.4.1，
    Redis 为主存储，落库失败仅记日志不阻断成功）。
    任务状态经 TaskStatus 追踪（match/recommend 路由入队时携带 task_id）。
    """
    import uuid

    from sqlalchemy import select

    from app.core.database import async_session_factory, redis_client
    from app.models.business import MatchResultRecord, ResumeCache, TaskStatus
    from app.services.matching.engine import RuleBasedMatcher
    from app.services.matching.loaders import build_candidate, load_positions_from_graph
    from app.services.matching.schemas import MatchMode, MatchRequest
    from app.services.matching.semantic import SkillEmbedder

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id) if task_id else None
        if task is None:
            return {"status": "failed", "error": "TaskStatus 不存在"}

        cache = await session.get(ResumeCache, resume_id)
        if cache is None:
            task.status = "failed"
            task.error = "简历不存在"
            await session.commit()
            return {"status": "failed", "error": "简历不存在"}

        task.status = "running"
        task.progress = 0.3
        await session.commit()

        try:
            candidate = build_candidate(cache.parsed_data)
            task.progress = 0.6
            await session.commit()
            # 项目向量（pgvector project_embeddings）：未回填时为空 dict，评分回退文本相似度
            from app.services.embeddings.vector_store import load_project_vectors

            project_vectors = await load_project_vectors(session, resume_id)

            def _match():
                # 同步 Neo4j 图谱加载 + SBERT + 规则匹配放线程池，避免阻塞事件循环
                matcher = RuleBasedMatcher(
                    load_positions_from_graph(),
                    semantic=SkillEmbedder.get(),
                )
                return matcher.match(
                    MatchRequest(
                        candidate=candidate, mode=MatchMode.AUTO, top_n=top_n,
                        project_vectors=project_vectors,
                    )
                )

            results = await asyncio.to_thread(_match)
            match_id = str(uuid.uuid4())
            data = {
                "items": [r.model_dump() for r in results],
                "match_id": match_id,
                "user_id": user_id,
            }
            await redis_client.set(
                f"match:result:{match_id}",
                json.dumps(data, ensure_ascii=False),
                ex=_MATCH_RESULT_TTL,
            )
            # match_results 幂等落库（§11.4.1）：失败仅记日志，Redis 为主存储
            try:
                row = await session.scalar(
                    select(MatchResultRecord).where(
                        MatchResultRecord.match_id == match_id
                    )
                )
                if row is None:
                    session.add(MatchResultRecord(
                        match_id=match_id,
                        position_name=results[0].position_name if results else "",
                        user_id=user_id,
                        result=data,
                    ))
                else:
                    row.result = data
                await session.flush()
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "匹配结果落库失败，跳过（不影响任务成功）"
                )

            task.status = "success"
            task.progress = 1.0
            # 合并而非覆盖：保留入队时的 user_id/resume_id（GET /match/task 归属校验依赖）
            task.result = _complete_recommend_result(task.result, match_id, len(results))
        except Exception as e:
            task.status = "failed"
            task.error = str(e)[:500]
        await session.commit()

        if task.status == "success":
            return {"status": "success", "match_id": task.result["match_id"]}
        return {"status": "failed", "error": task.error}


# 参与 JD 正文拼接的 snapshot 字段（按此顺序，跳过来源无关的元数据）
_JD_TEXT_FIELDS = (
    "title", "company", "location", "salary", "experience",
    "education", "description", "requirements",
)


def _build_jd_text(snapshot: dict, raw_text: str) -> str:
    """拼装 JD 抽取正文。

    优先 snapshot 的干净文本字段（raw_text 为原始 HTML/JSON 备份，不适合直接喂 LLM）；
    但正文字段（description/requirements）缺失时拼接结果过短无法抽取，
    此时回退 raw_text（黄金集等数据正文可能只存在 raw_text 中）。
    """
    body_fields = (snapshot.get("description"), snapshot.get("requirements"))
    if not any(str(f or "").strip() for f in body_fields):
        return raw_text
    parts = [str(snapshot.get(f, "")).strip() for f in _JD_TEXT_FIELDS]
    return "\n".join(p for p in parts if p)


def _is_jd_text_short(snapshot: dict, raw_text: str) -> bool:
    """JD 正文是否过短（<10 字符，无法抽取）。"""
    return len((_build_jd_text(snapshot, raw_text) or "").strip()) < 10


async def batch_extract(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 100,
) -> dict:
    """LLM 批量实体抽取 + JD 入图（M3 实现，依赖 AL-M3-01）。

    选取 jd_raw 中尚未抽取（snapshot 无 extraction 标记）的记录：
    - 拼装 JD 正文 → JDExtractor.extract_batch（N 条/批一次 LLM 调用，设计文档 §6.5
      批量抽取优化：批量输出 token 线性放大，走独立 batch_timeout；整批失败/条数
      错位时该批降级逐条 extract，单条失败不阻塞整体）
    - 抽取结果写回 jd_raw.snapshot["extraction"]（可审计、可重跑）
    - kg_service.import_jd 入图（Neo4j MERGE 幂等，重复执行不产生重复节点）

    全部失败时抛出，由 ARQ 重试机制兜底。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw
    from app.services.extraction.jd_extractor import JDExtractor
    from app.services.kg.kg_service import import_jd

    extractor = JDExtractor()

    async with async_session_factory() as session:
        if jd_ids:
            rows = (await session.scalars(
                select(JDRaw).where(JDRaw.id.in_(jd_ids))
            )).all()
        else:
            # 未抽取 = snapshot 无 extraction 键（JSONB 键缺失时为 SQL NULL）
            rows = (await session.scalars(
                select(JDRaw)
                .where(JDRaw.snapshot["extraction"].astext.is_(None))
                .order_by(JDRaw.id.asc())
                .limit(limit)
            )).all()

        # 过滤过短正文（<10 字符无法抽取）与低质 JD（needs_review 人工复核标记）：
        # 写 skipped 标记推进游标，否则 `extraction IS NULL` 游标永不推进
        # （短文本行/低质行堆积时正常 JD 饿死）
        valid: list[JDRaw] = []
        results: dict = {"processed": 0, "succeeded": 0, "failed": [], "positions": []}
        for row in rows:
            snap = row.snapshot or {}
            if _is_jd_text_short(snap, row.raw_text or ""):
                snap = dict(snap)
                snap["extraction"] = {"skipped": True, "reason": "JD 正文过短（<10 字符）"}
                row.snapshot = snap
                results["failed"].append({"jd_id": row.id, "error": "JD 正文过短（<10 字符），跳过"})
            elif snap.get("needs_review"):
                # 低质 JD（爬虫端质量评分 < 0.6 标记）：跳过 LLM 抽取，
                # 写 skipped 标记推进游标，否则 `extraction IS NULL` 游标不推进
                snap = dict(snap)
                snap["extraction"] = {"skipped": True, "reason": "质量评分 < 0.6，需人工复核"}
                row.snapshot = snap
                results["failed"].append({"jd_id": row.id, "error": "质量评分 < 0.6，跳过"})
            else:
                valid.append(row)

        # 批量抽取：一次调用处理全部有效 JD——组批（batch_size 条数 + max_batch_chars
        # 文本总长双封顶）→ 每批一次 LLM 调用（独立 batch_timeout，设计文档 §6.5）→
        # 拆条落库。返回顺序与 valid 一一对应（错位/失败批次已降级逐条）。
        total = len(valid)
        texts = [_build_jd_text(r.snapshot or {}, r.raw_text or "") for r in valid]
        if texts:
            # 同步 LLM 批量调用放线程池，避免阻塞 ARQ 事件循环（Redis 心跳超时崩溃根因）。
            # concurrency=6 / batch_size=8：2026-08-07 用户确认提速（max_tokens 同步调至 4096）。
            # LLM 生成时间由输出 token 总量决定，并发提吞吐；若触发 provider 429，退避期
            # 整批降级逐条反而更慢，届时回调参数。
            extractions = await asyncio.to_thread(
                extractor.extract_batch,
                texts,
                batch_size=8,
                batch_timeout=180,  # 批量输出 token 放大，独立超时
                max_batch_chars=8000,
                concurrency=6,
            )
        else:
            extractions = []

        # 岗位名归一化（纯规则）：与聚合链路共用 normalize_position_name，保证
        # 快照、入图、聚合三处岗位名口径一致（修复：此前语义兜底对齐与聚合规则
        # 不一致，导致聚合岗位名 MATCH 不上图节点）
        if extractions:
            from app.services.extraction.dictionary import normalize_position_name

            for extraction in extractions:
                normalized = normalize_position_name(
                    extraction.position_name,
                    skills=[s.name for s in (extraction.skills or [])],
                )
                if normalized and normalized != extraction.position_name:
                    extraction.position_name = normalized

        for i, (row, extraction) in enumerate(zip(valid, extractions), start=1):
            # 逐条打印 jd_id + 进度百分比：batch_extract 只在循环结束 commit，
            # 中间进度 DB 不可见，靠此日志实时确认推进（worker.err.log）
            print(
                f"[batch_extract] 处理 jd_id={row.id}（{i}/{total}，{i / total * 100:.0f}%）",
                flush=True,
            )
            try:
                evidence = {
                    "source": row.source,
                    "source_url": row.source_url,
                    "crawled_at": row.crawled_at,
                    "raw_text": _build_jd_text(row.snapshot or {}, row.raw_text or ""),
                }
                with neo4j_driver.session() as neo4j_session:
                    # 同步 Neo4j 写入放线程池，避免阻塞事件循环
                    position_id = await asyncio.to_thread(
                        import_jd, neo4j_session, extraction, evidence
                    )
                # 入图成功后才写 extraction 标记：先标记后入图会让失败记录
                # extraction 落库，下次批跑 `extraction IS NULL` 不再选中，图数据永久缺失
                snap = dict(row.snapshot or {})
                snap["extraction"] = extraction.model_dump()
                row.snapshot = snap
                results["processed"] += 1
                results["succeeded"] += 1
                results["positions"].append({"jd_id": row.id, "position_id": position_id})
            except Exception as e:
                # 入图失败：不写 extraction（保持 IS NULL 下次批跑重试），
                # 错误写入 extraction_error 落库审计（failed 可追溯）
                snap = dict(row.snapshot or {})
                snap["extraction_error"] = str(e)[:500]
                row.snapshot = snap
                results["failed"].append({"jd_id": row.id, "error": str(e)[:500]})
        await session.commit()

    if results["processed"] > 0 and results["succeeded"] == 0:
        raise RuntimeError(f"批量抽取全部失败: {results['failed'][:5]}")
    return results


async def snapshot_graph(ctx: dict, triggered_by: str = "scheduled") -> dict:
    """每日图谱版本快照（设计文档 §7.1 T+1 版本管理）。

    流程：Neo4j 全量导出 {nodes, edges}（排除 Counter 内部标签）→
    写入 PostgreSQL graph_versions（幂等：同日期版本覆盖更新）→
    与上一版本 set 差集计算节点增减 → 90 天保留清理。

    由外部 cron（scripts/cron/snapshot_daily.py）每日 05:00 前触发，
    或作为 run_etl_pipeline 阶段 12 随 ETL 完成后自动发布。
    """
    from app.services.evolution.graph_version import GraphVersionManager

    meta = await GraphVersionManager().create_snapshot(triggered_by=triggered_by)
    return meta.model_dump()


# 项目统一时区 UTC+8（与 services 层常量一致，first_seen/观测起点均按东八区取日期）
_TZ_CN = timezone(timedelta(hours=8))


def _first_seen_date_of(row) -> str:
    """岗位单条 JD 的观测日期（ISO）：post_date 解析日优先，入库日兜底。

    回爬 90 天历史后，存量老岗位的入库日（created_at）是回爬当天，会掩盖其
    真实出现时间，靠发布日（post_date）才能识别为存量；缺失时回退入库日。
    清洗层已把 post_date 归一化（相对时间转绝对 ISO），此处仅截取日期前缀。
    """
    raw = str((row.snapshot or {}).get("post_date") or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    return row.created_at.astimezone(_TZ_CN).date().isoformat()


async def discovery_daily(ctx: dict) -> dict:
    """每日新岗位发现（AL-M4-01，设计文档 7.2.3 节）。

    流程：聚合 jd_raw 已抽取记录 → 计算候选特征（freq/源多样性/Z-score）
    → 阶段一门控（detect_candidates）→ 阶段二 RAG 接地（权威库 + 种子）
    → 幂等 upsert discovery_candidates 候选池 → 自动状态流转持久化。

    幂等设计：按 position_name upsert，重复执行覆盖更新（同岗位不重复入池）。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CommunityRaw, JDRaw, PaperRaw
    from app.services.discovery.detector import DiscoveryDetector, DiscoveryInput
    from app.services.discovery.confidence import compute_confidence
    from app.services.extraction.dictionary import normalize_position_name
    from app.services.discovery.schemas import DiscoveryFeatures

    # ── 1. 聚合 jd_raw 已抽取记录 → 岗位频次/源多样性/首次观测日 ──
    # keyset 游标分批加载，避免全表拉取（与 dedup_simhash 修复一致）
    position_stats: dict[str, dict] = {}
    position_skills: dict[str, set[str]] = {}
    # 系统采集首日（jd_raw 最早入库日，东八区）：post_date 缺失兜底用——
    # 入库日 == 采集首日的岗位视为起步期存量（首日即被采到）
    collection_start = None
    _PAGE = 2000
    last_id = 0
    async with async_session_factory() as session:
        while True:
            batch = (await session.scalars(
                select(JDRaw)
                .where(
                    JDRaw.snapshot["extraction"].astext.isnot(None),
                    JDRaw.id > last_id,
                )
                .order_by(JDRaw.id.asc())
                .limit(_PAGE)
            )).all()
            if not batch:
                break
            for row in batch:
                ext = (row.snapshot or {}).get("extraction") or {}
                name = normalize_position_name(
                    ext.get("position_name") or "",
                    skills=[s["name"] for s in (ext.get("skills") or [])
                            if isinstance(s, dict) and s.get("name")],
                )
                if not name:
                    continue
                stat = position_stats.setdefault(
                    name, {"count": 0, "sources": set(), "has_post_date": False}
                )
                stat["count"] += 1
                stat["sources"].add(row.source)
                # post_date 缺失标记：任一记录有真实 post_date 即不算缺失
                # （存量排除兜底见 _is_mature_position）
                if str((row.snapshot or {}).get("post_date") or "").strip():
                    stat["has_post_date"] = True
                # 首次观测日：post_date 解析日优先（回爬老岗位靠发布日识别存量，
                # 避免入库日被回爬当天掩盖），入库日兜底
                fd = _first_seen_date_of(row)
                if stat.get("first_seen") is None or fd < stat["first_seen"]:
                    stat["first_seen"] = fd
                # 采集首日：jd_raw 最早入库日（东八区日期）
                cd = row.created_at.astimezone(_TZ_CN).date().isoformat()
                if collection_start is None or cd < collection_start:
                    collection_start = cd
                # 收集岗位关联技能（供 §7.2.2 辅助加分特征关联 arxiv/github 信号）
                skills = position_skills.setdefault(name, set())
                for s in ext.get("skills") or []:
                    if isinstance(s, dict) and s.get("name"):
                        skills.add(s["name"])
            last_id = batch[-1].id

    if not position_stats:
        return {"candidates": 0, "detail": "无已抽取岗位记录"}

    # ── 2. 组装 DiscoveryInput（Z-score 门控 + 冷启动 Wilson 兜底）──
    # 从 graph_versions 快照序列重建岗位频次窗口，计算真实 Z-score /
    # 3 月移动平均 / 环比增长率，替代此前 history_days=1/z_score=None 硬编码
    # （否则正常 Z-score 门控永不触发，只能走冷启动）
    from app.models.business import GraphVersion
    from app.services.discovery.state_machine import freq_z_scores, position_freq_windows

    async with async_session_factory() as session:
        snap_rows = (await session.scalars(
            select(GraphVersion).order_by(GraphVersion.created_at.asc())
        )).all()
    snapshots = [s.snapshot_json or {} for s in snap_rows]
    freq_windows = position_freq_windows(snapshots, set(position_stats))
    window_days = 0
    observation_start = None
    if snap_rows:
        # 观测窗口起点：首个快照日期（东八区）。成熟岗位排除以此为准——
        # 早于此日期的岗位是系统开始观测前就存在的市场存量
        observation_start = snap_rows[0].created_at.astimezone(_TZ_CN).date().isoformat()
    if len(snap_rows) >= 2:
        span = (snap_rows[-1].created_at - snap_rows[0].created_at)
        window_days = max(span.days, 0) if span else 0

    inputs = []
    # 岗位 → 快照环比增长率（置信度三维加权 §7.2.4 用，见下方 compute_confidence）
    growth_by_position: dict[str, float] = {}
    for name, stat in position_stats.items():
        freq = float(stat["count"])
        freqs = freq_windows.get(name, [])
        # 快照窗口 ≥ 2 期时用真实 Z-score/MA3/环比；否则保持保守冷启动信号
        z_score = None
        growth_rate = 0.0
        jd_freq_ma3 = freq
        # 冷启动二项样本：岗位在快照窗口中的出现密度（默认 0/0 = 快照未出现，
        # 无法冷启动）。口径为"首现后窗口出现率"（successes=出现窗口数，
        # total=首现之后窗口数）而非全量 JD 占比——后者在 JD 占比下 Wilson
        # 下界极低（实测 0.005-0.185），任何阈值都无法通过
        cold_successes, cold_total = 0, 0
        if len(freqs) >= 2:
            zs = freq_z_scores(freqs)
            z_score = float(zs[-1])
            recent3 = freqs[-3:]
            jd_freq_ma3 = sum(recent3) / len(recent3)
            if freqs[-2] > 0:
                growth_rate = (freqs[-1] - freqs[-2]) / freqs[-2]
        if freqs:
            active = sum(1 for f in freqs if f > 0)
            first_active = next((i for i, f in enumerate(freqs) if f > 0), None)
            if first_active is not None:
                cold_successes, cold_total = active, len(freqs) - first_active
        growth_by_position[name] = growth_rate
        inputs.append(
            DiscoveryInput(
                position_name=name,
                features=DiscoveryFeatures(
                    jd_freq_ma3=jd_freq_ma3,
                    z_score=z_score,
                    source_diversity=len(stat["sources"]),
                    first_seen_date=stat.get("first_seen"),
                ),
                history_days=window_days or 1,
                cold_successes=cold_successes,
                cold_total=cold_total,
                first_seen_date=stat.get("first_seen"),
                observation_start=observation_start,
                collection_start=collection_start,
                post_date_missing=not stat.get("has_post_date"),
            )
        )

    # ── 3. 阶段一门控 + 阶段二 RAG 接地 ──
    detector = DiscoveryDetector()
    candidates = detector.detect_candidates(_Provider(inputs))

    # ── 3.1 学术/社区异常信号（设计 §7.2.2 辅助加分特征，M4 接通观察池）──
    # paper_raw(arxiv) / community_raw(github) 过去 12 周聚合 → (技能,源) 周频次，
    # 候选岗位关联技能任一命中 2σ 即标记 arxiv/github_anomaly（仅置信度加分，
    # 不参与 candidate 触发门控，对齐"学术/社区源不独立触发 candidate"）。
    from datetime import date, timedelta

    from app.services.discovery.watch_pool import aggregate_weekly_freqs, anomaly_flags

    since = (date.today() - timedelta(weeks=12)).isoformat()
    async with async_session_factory() as session:
        paper_rows = (await session.scalars(
            select(PaperRaw).where(PaperRaw.crawled_at >= since)
        )).all()
        community_rows = (await session.scalars(
            select(CommunityRaw).where(CommunityRaw.crawled_at >= since)
        )).all()
    academic_freqs = aggregate_weekly_freqs([*paper_rows, *community_rows])

    grounded = []
    # LLM 实例（定义草案中文生成）：未配置 api_key 时 LLMProviderChain 构造
    # 即抛 LLMConfigurationError，fallback 到权威库原文，接地不阻塞。
    from app.services.extraction.jd_extractor import JDExtractor

    llm = None
    try:
        llm = JDExtractor()._llm
    except Exception:
        llm = None
    async with async_session_factory() as session:
        for cand in candidates:
            c = await detector.ground_with_rag(cand, session, llm=llm)
            # 置信度：jd_count/source_diversity 来自候选特征，
            # growth_rate 用快照窗口重建的环比增长率（§7.2.4 三维加权公式）
            flags = anomaly_flags(academic_freqs, position_skills.get(cand.position_name, set()))
            conf = compute_confidence(
                jd_count=int(cand.features.jd_freq_ma3),
                source_count=cand.features.source_diversity,
                growth_rate=growth_by_position.get(cand.position_name, 0.0),
                # 学术/社区异常信号（§7.2.2 辅助加分特征，M4 接通观察池）：
                # paper_raw/community_raw 周频次 2σ 判定，仅作置信度加分
                arxiv_anomaly=flags["arxiv"],
                github_anomaly=flags["github"],
            )
            c = c.model_copy(update={"confidence": conf})
            grounded.append(c)
            await _upsert_candidate(session, c)
        await session.commit()

    # 注：自动态迁移（emerging→stable / declining 等）由 discovery_auto_transition
    # 任务负责（依赖 graph_versions 快照序列的窗口频次）；本任务只负责
    # candidate 入池与 RAG 接地。candidate→emerging/rejected 由 admin 审核端点
    # 调用状态机评估。

    return {
        "candidates": len(grounded),
        "seed_matched": sum(1 for c in grounded if c.seed_matched),
        "rag_matched": sum(1 for c in grounded if c.rag_matched),
    }


async def discovery_auto_transition(ctx: dict) -> dict:
    """自动状态流转（设计文档 7.2.1 状态机：emerging/stable/declining 自动迁移）。

    从 jd_raw 已抽取记录按 post_date 聚合岗位 30 天窗口 JD 发布频次（declining
    信号源）→ 对 discovery_candidates 中 state ∈ {emerging, stable, declining}
    的岗位调用 evaluate_auto_transition 判定 → 命中则 PositionStateMachine.persist
    （Neo4j Position.status + 候选池状态）。

    信号源说明（2026-08-11）：declining 信号从"图谱快照 REQUIRES 边数"改为真实
    JD 发布数——快照边数随图谱清理/重建/改名剧烈波动（08-11 重建致"算法工程师"
    1348→56 伪降），而发布数语义 = 设计文档"JD 需求下降"。post_date 缺失按入库
    日兜底（_first_seen_date_of）。

    注意：自动流转 operator="system"，不写 AuditLog（audit_logs.user_id 为
    users 外键，system 无对应用户）。人工流转记录见 /evolution/state-machine。

    emerging → stable: confidence ≥ 0.8 AND 连续 2 窗口波动 < 25% AND 源 ≥ 2
    emerging/stable → declining: 连续 3 窗口频次下降 > 40%
    declining → stable: 连续 2 窗口 z_score > 0（回升）

    幂等：persist 按 name MERGE，重复执行结果一致；无命中不产生副作用。

    数据不足（jd_raw 无已抽取记录或岗位窗口序列 < 2）时跳过，不武断判定（冷启动）。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.business import DiscoveryCandidate
    from app.models.raw import JDRaw
    from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState
    from app.services.discovery.state_machine import (
        WindowFreq, decline_rate, evaluate_auto_transition, freq_z_scores,
        PositionStateMachine, jd_publish_windows, window_volatility,
    )
    from app.services.extraction.dictionary import normalize_position_name

    import logging
    _logger = logging.getLogger(__name__)

    # ── 1. 聚合 jd_raw 已抽取记录 → 岗位按天 JD 发布数（declining 信号源）──
    # 一次加载已抽取记录（万级），按 _first_seen_date_of（post_date 解析日优先、
    # 入库日兜底）统计每岗位每日发布数，再切 30 天窗口
    async with async_session_factory() as session:
        jd_rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    daily_freqs: dict[str, dict[str, int]] = {}
    for row in jd_rows:
        ext = (row.snapshot or {}).get("extraction") or {}
        name = normalize_position_name(ext.get("position_name") or "")
        if not name:
            continue
        day = _first_seen_date_of(row)
        day_counts = daily_freqs.setdefault(name, {})
        day_counts[day] = day_counts.get(day, 0) + 1

    freq_windows = jd_publish_windows(daily_freqs)
    if not freq_windows:
        return {"transitions": 0, "detail": "jd_raw 无已抽取记录，无法计算窗口序列（冷启动）"}

    # ── 2. 对候选池中自动可迁移状态的岗位执行判定 ──
    machine = PositionStateMachine()
    transitions: list[dict] = []
    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(DiscoveryCandidate).where(
                DiscoveryCandidate.state.in_(
                    [PositionState.EMERGING.value, PositionState.STABLE.value, PositionState.DECLINING.value]
                )
            )
        )).all()

        for row in rows:
            name = normalize_position_name(row.position_name)
            if not name:
                continue
            freqs = freq_windows.get(name, [])
            if len(freqs) < 2:
                _logger.info(
                    "auto_transition 跳过: %s 窗口序列 %s（<2 期，冷启动不武断判定）",
                    row.position_name, freqs,
                )
                continue

            features = DiscoveryFeatures(**row.features)
            candidate = CandidatePosition(
                candidate_id=row.id,
                position_name=row.position_name,
                state=PositionState(row.state),
                features=features,
                detected_at=row.detected_at,
                evidence_refs=row.evidence_refs,
                seed_matched=row.seed_matched,
                rag_matched=row.rag_matched,
                definition_draft=row.definition_draft,
            )
            conf = float((row.confidence or {}).get("final_confidence", 0.0))
            # z_scores 由频次序列自身重建（freq_z_scores）：declining 岗位回升
            # 时最近 2 窗口 z > 0，触发 declining → stable 自动回迁
            windows = WindowFreq(freqs=freqs, z_scores=freq_z_scores(freqs))
            target = evaluate_auto_transition(candidate, windows, confidence=conf)
            _logger.info(
                "auto_transition: %s state=%s 30天窗口序列=%s z_scores=%s "
                "volatility=%.3f decline_rate=%.3f → %s",
                row.position_name, row.state, freqs,
                [round(z, 3) for z in windows.z_scores],
                window_volatility(windows), decline_rate(windows),
                target.value if target else "不迁移",
            )
            if target is None:
                continue

            def _persist_transition() -> CandidatePosition:
                # machine.persist 含同步 Neo4j 写（MERGE + SET status），放线程池
                with neo4j_driver.session() as neo4j_session:
                    return machine.persist(
                        neo4j_session, candidate, target, operator="system",
                    )

            updated = await asyncio.to_thread(_persist_transition)
            row.state = updated.state.value
            transitions.append({
                "position_name": row.position_name,
                "from_state": candidate.state.value,
                "to_state": updated.state.value,
            })
        await session.commit()

    return {
        "transitions": len(transitions),
        "detail": transitions,
    }


class _Provider:
    """适配 CandidateProvider Protocol 的内存数据源。"""

    def __init__(self, inputs):
        self._inputs = inputs

    def iter_inputs(self):
        return iter(self._inputs)


async def _upsert_candidate(session, cand) -> None:
    """按 position_name upsert 候选池（幂等：同岗位覆盖更新特征/状态）。"""
    from app.models.business import DiscoveryCandidate
    from sqlalchemy import select

    row = await session.scalar(
        select(DiscoveryCandidate).where(DiscoveryCandidate.position_name == cand.position_name)
    )
    payload = {
        "state": cand.state.value,
        "features": cand.features.model_dump() if hasattr(cand.features, "model_dump") else cand.features,
        "confidence": cand.confidence.model_dump() if cand.confidence else {},
        "evidence_refs": cand.evidence_refs,
        "seed_matched": cand.seed_matched,
        "rag_matched": cand.rag_matched,
        "definition_draft": cand.definition_draft,
        "detected_at": cand.detected_at,
    }
    if row is None:
        session.add(DiscoveryCandidate(id=cand.candidate_id, position_name=cand.position_name, **payload))
    else:
        # 已晋升（emerging/stable/declining 等）的岗位不被 discovery_daily
        # 打回 candidate；仅仍为 candidate 的行允许状态覆盖
        if row.state != "candidate":
            payload.pop("state", None)
        for k, v in payload.items():
            setattr(row, k, v)


# ============================================================
# 技术热点观察池（设计文档 7.2.5）
# ============================================================

async def watch_signal_daily(ctx: dict, run_date: str | None = None) -> dict:
    """每日技术热点信号监测（设计文档 7.2.5 观察池 + MLI 拐点）。

    流程：聚合 4 源 raw 表（jd/course/paper/community）周频次 → 判定
    命中阈值（JD 3 月移动平均环比 > 50%；学术/社区/课程 2σ）→ 幂等
    upsert technology_watch → JD 源命中且该技能此前已在观察池的技能提升
    candidate（写入 discovery_candidates，设计 §7.2.5 / 方案 §2）。

    幂等：technology_watch 按 (skill, source, period) 唯一约束 upsert；
    候选池提升仅对已有观察历史且未晋升的技能生效（不重复提升）。

    Args:
        run_date: 统计周期 YYYY-MM-DD（缺省用当天）
    """
    from datetime import date, timedelta
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.business import TechnologyWatch
    from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
    from app.services.discovery.watch_pool import (
        aggregate_weekly_freqs,
        anomaly_flags,
        build_signals,
        promotion_features,
    )

    period = run_date or date.today().isoformat()
    # 观察窗口：过去 12 周（JD 3 月移动平均需 12 周以上历史）
    since = (date.fromisoformat(period) - timedelta(weeks=12)).isoformat()

    # ── 1. 读取 4 源 raw 行（crawled_at >= since）──
    async with async_session_factory() as session:
        jd_rows = (await session.scalars(
            select(JDRaw).where(JDRaw.crawled_at >= since)
        )).all()
        course_rows = (await session.scalars(
            select(CourseRaw).where(CourseRaw.crawled_at >= since)
        )).all()
        paper_rows = (await session.scalars(
            select(PaperRaw).where(PaperRaw.crawled_at >= since)
        )).all()
        community_rows = (await session.scalars(
            select(CommunityRaw).where(CommunityRaw.crawled_at >= since)
        )).all()

    all_rows = [*jd_rows, *course_rows, *paper_rows, *community_rows]
    if not all_rows:
        return {"signals": 0, "detail": f"{period} 无 raw 数据"}

    freqs = aggregate_weekly_freqs(all_rows)
    signals = build_signals(freqs, period)
    # 学术/社区源周频次（§7.2.2 辅助加分特征，提升候选置信度加分用）
    academic_freqs = aggregate_weekly_freqs([*paper_rows, *community_rows])

    # ── 2. 幂等 upsert technology_watch + 计算 MLI ──
    promoted: list[str] = []
    upserted = 0
    async with async_session_factory() as session:
        for sig in signals:
            row = await session.scalar(
                select(TechnologyWatch).where(
                    TechnologyWatch.skill_name == sig.skill_name,
                    TechnologyWatch.signal_source == sig.signal_source,
                    TechnologyWatch.period == sig.period,
                )
            )
            if row is None:
                session.add(TechnologyWatch(
                    skill_name=sig.skill_name,
                    signal_source=sig.signal_source,
                    signal_value=sig.signal_value,
                    period=sig.period,
                    status="watch",
                ))
            else:
                row.signal_value = sig.signal_value
                row.last_signal_at = datetime.now(timezone.utc)
            upserted += 1
        await session.commit()

        # ── 3. 提升候选：JD 源命中且该技能此前已在观察池（设计 §7.2.5 / 方案 §2）──
        from app.models.business import DiscoveryCandidate
        from app.services.discovery.confidence import compute_confidence
        from app.services.discovery.watch_pool import promotable_skills

        prior_rows = (await session.scalars(
            select(TechnologyWatch.skill_name).where(
                TechnologyWatch.period < period,
            )
        )).all()
        previously_watched = {name for name in prior_rows}

        for skill in promotable_skills(signals, previously_watched):
            existing = await session.scalar(
                select(DiscoveryCandidate).where(
                    DiscoveryCandidate.position_name == skill
                )
            )
            if existing is not None:
                continue  # 已在候选池/已晋升，不重复提升
            # 真实特征与置信度（替代硬编码 source_diversity=1/final_confidence=0.0：
            # 否则提升候选永远无法过 emerging 门槛——跨 ≥2 源 + 置信度 ≥ 0.6）
            feat = promotion_features(freqs, skill)
            flags = anomaly_flags(academic_freqs, {skill})
            conf = compute_confidence(
                jd_count=int(feat["jd_freq_ma3"]),
                source_count=feat["source_diversity"],
                growth_rate=feat["growth"],
                arxiv_anomaly=flags["arxiv"],
                github_anomaly=flags["github"],
            )
            session.add(DiscoveryCandidate(
                id=f"cand-{skill[:20]}",
                position_name=skill,
                state="candidate",
                features=feat,  # 键与 DiscoveryFeatures schema 兼容
                confidence=conf.model_dump(),
                evidence_refs=[f"watch:{period}:{skill}"],
                seed_matched=False,
                rag_matched=False,
                definition_draft="",
                detected_at=period,
            ))
            promoted.append(skill)
            # 状态流转：该技能本期 watch 行 → candidate_promoted
            watch_rows = (await session.scalars(
                select(TechnologyWatch).where(
                    TechnologyWatch.skill_name == skill,
                    TechnologyWatch.period == period,
                    TechnologyWatch.status == "watch",
                )
            )).all()
            for r in watch_rows:
                r.status = "candidate_promoted"
        await session.commit()

    return {
        "signals": upserted,
        "promoted": len(promoted),
        "detail": promoted,
    }


# ============================================================
# ARQ Worker 注册
# ============================================================

async def check_llm_providers_health(ctx: dict) -> dict:
    """LLM provider 健康检查（设计文档 §6.5：每 5min 调 /models 端点）。

    遍历 enabled provider 探测 /models 可用性，结果写 Redis（llm:health:{name}），
    供调用链展示/运维排查。配置缺失（无 yaml）时跳过并返回原因，不触发
    ARQ 重试；单 provider 探测失败仅记 unhealthy，由熔断/退避机制在调用侧兜底。
    """
    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        health_check_all,
    )

    try:
        checked = await asyncio.to_thread(health_check_all)
    except LLMConfigurationError as e:
        return {"status": "skipped", "reason": str(e)}
    print(f"[check_llm_providers_health] {checked}", flush=True)
    return {"status": "ok", "healthy": checked}


async def on_startup(ctx: dict) -> None:
    """Worker 启动钩子。

    预热 OCR 引擎（PaddleOCR 懒加载首次调用约 24s，2026-08-09 扫描件 OCR
    速度评测）：异步预加载到全局单例，使首次 resume_parse 免于 24s 冷加载。
    模型不可用（未下载/依赖缺失）时预热失败不阻塞 worker 启动，后续
    resume_parse 仍会按需懒加载并抛 ResumeParseError 由任务层处理。
    """
    print(f"[ARQ Worker] 启动，PID={ctx.get('worker_pid')}")

    async def _warm_ocr():
        try:
            from app.services.resume import file_parser as _fp

            _fp._ocr_engine()
            print("[ARQ Worker] OCR 引擎预热完成")
        except Exception as e:
            print(f"[ARQ Worker] OCR 预热跳过（模型不可用）: {str(e)[:100]}")

    asyncio.create_task(_warm_ocr())


async def on_shutdown(ctx: dict) -> None:
    """Worker 关闭钩子。"""
    print("[ARQ Worker] 关闭")


class WorkerSettings:
    """ARQ Worker 配置。

    启动命令：arq app.workers.tasks.WorkerSettings
    """
    functions = [
        crawl_platform,
        # ETL 主管线含 7 平台爬虫 subprocess（单源上限 900s）+ LLM 批量抽取 + 全量入图，
        # 超出全局 job_timeout(1800s)，per-function 放宽至 3h；max_tries=1 防整管线重跑
        func(run_etl_pipeline, timeout=10800, max_tries=1),
        dedup_simhash,
        validate_temporal,
        detect_inflation,
        resume_parse,
        match_recommend,
        batch_extract,
        load_courses,
        evaluate_courses,
        diversity_report,
        check_data_freshness,
        aggregate_positions,
        cross_validate_jds,
        sync_skill_normalization,
        backfill_embeddings,
        discovery_daily,
        discovery_auto_transition,
        watch_signal_daily,
        snapshot_graph,
        check_llm_providers_health,
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(settings.arq_redis_url)
    concurrency = settings.arq_concurrency
    job_timeout = settings.arq_job_timeout
    max_retries = 2
    retry_delay = 10
    # 定时任务（设计文档 §6.5）：每 5min 探测 provider 健康并写 Redis；
    # run_at_startup 让 worker 启动即跑一次，快速发现不可用 provider
    cron_jobs = [
        cron(
            check_llm_providers_health,
            minute=set(range(0, 60, 5)),
            run_at_startup=True,
        ),
        # 设计文档 §7.2.5 观察池：每日 06:00 监测技术热点信号（ETL 之后）
        cron(watch_signal_daily, hour=6, minute=0),
    ]
