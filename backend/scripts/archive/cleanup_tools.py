"""批量清洗 Tool 节点：合并别名/大小写变体，统一为规范名（P1 节点碎片治理）。

背景（与 dictionary.normalize_tool_name / post_processor 工具归一化配套）：
图谱 Tool 节点由 LLM 抽取工具名直接建节点，工具名未归一化，同一工具因写法
不同分裂成多个节点（Ansys/ANSYS、DeepSeek/Deepseek、NodeJS/NodeJs 等）。
本脚本按 normalize_tool_name 口径把变体节点的 REQUIRES 入边重连到规范名节点
后删除变体节点。

口径：工具别名表 TOOL_ALIAS + 白名单/技能别名（与抽取侧一致），保证防复发与
清存量同一套规则。合并后 normalize_tool_name(std) == std，可安全重跑（幂等）。

用法：
  python scripts/cleanup_tools.py            # dry-run，只打印合并计划
  python scripts/cleanup_tools.py --apply    # 执行合并并打印变更统计
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.logging import setup_logging
from app.services.extraction.dictionary import normalize_tool_name
from neo4j import GraphDatabase

logger = setup_logging("cleanup_tools")


def _load_tools(driver) -> list[dict]:
    with driver.session() as s:
        return s.run(
            """
            MATCH (t:Tool)
            WHERE t.name IS NOT NULL AND t.name <> ''
            OPTIONAL MATCH (t)<-[r:REQUIRES]-(:Position)
            RETURN t.name AS name, count(r) AS rel_count
            """
        ).data()


def _counts(driver) -> tuple[int, int]:
    with driver.session() as s:
        tools = s.run("MATCH (t:Tool) RETURN count(t) AS c").single()["c"]
        reqs = s.run("MATCH ()-[r:REQUIRES]->(:Tool) RETURN count(r) AS c").single()["c"]
    return tools, reqs


def _merge_variant(driver, std: str, variant: str) -> None:
    """把 variant 节点的 REQUIRES 入边重连到 std 节点后删除 variant。

    重连时原样搬移旧边属性（necessity/level 等），避免合并后技能权重信息丢失。
    """
    with driver.session() as s:
        s.run(
            """
            MATCH (d:Tool {name: $dup})<-[r:REQUIRES]-(p:Position)
            MATCH (m:Tool {name: $std})
            MERGE (p)-[nr:REQUIRES]->(m)
            SET nr += properties(r)
            DELETE r
            """,
            dup=variant, std=std,
        )
        s.run("MATCH (d:Tool {name: $dup}) DETACH DELETE d", dup=variant)


def run(apply: bool) -> None:
    driver: GraphDatabase.driver | None = None
    try:
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        tools_before, reqs_before = _counts(driver)
        rows = _load_tools(driver)

        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[normalize_tool_name(r["name"])].append(r)

        groups_to_merge = {std: members for std, members in groups.items() if len(members) > 1}
        logger.info(
            f"清洗前 Tool 节点 {tools_before} 个，REQUIRES {reqs_before} 条，"
            f"变体组 {len(groups_to_merge)} 组"
        )
        if not groups_to_merge:
            logger.info("无变体，无需清洗")
            return

        for std in sorted(groups_to_merge):
            members = sorted(groups_to_merge[std], key=lambda x: (-x["rel_count"], x["name"]))
            parts = ", ".join(f"{m['name']}({m['rel_count']})" for m in members)
            logger.info(f"  -> {std} <= {parts}")

        if not apply:
            logger.info("dry-run，未改动；加 --apply 执行合并")
            return

        # 主节点优先取组内已有规范名节点，否则 rel 最高成员重命名为规范名
        # （避免 rel 最高但非规范名的节点被改名的同时组内已有规范名节点 → 重名冲突）
        renamed, merged = 0, 0
        for std, members in groups_to_merge.items():
            members = sorted(members, key=lambda x: (-x["rel_count"], x["name"]))
            master = next((m for m in members if m["name"] == std), None)
            if master is None:
                master = members[0]
                with driver.session() as s:
                    s.run("MATCH (m:Tool {name: $old}) SET m.name = $std", old=master["name"], std=std)
                renamed += 1
            for m in members:
                if m is master:
                    continue
                _merge_variant(driver, std, m["name"])
                merged += 1

        tools_after, reqs_after = _counts(driver)
        logger.info(
            f"合并完成：重命名主节点 {renamed} 个，删除变体节点 {merged} 个；"
            f"Tool {tools_before} -> {tools_after}，REQUIRES {reqs_before} -> {reqs_after}"
        )
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    run(apply=apply)
