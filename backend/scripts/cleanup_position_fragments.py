"""存量碎片岗位节点清理（P7 词典规则部署后执行，一次性）。

背景：dictionary._POSITION_STOPWORDS P7 批次（2026-08-15，T-04）新增拦截
缩写/产品名/荒谬组合岗位名（CNO/GTM/AI 证据/CMDB发现 等），_POSITION_KEYWORDS
新增映射（SDET/QA → 测试工程师、首席统计师 → 统计师）。词典规则生效后：
- 拦截词：normalize_position_name 返回空，聚合不再覆盖存量节点；
- 映射词：聚合输出规范岗位名，存量碎片节点的旧边不被对齐删除覆盖；
两者都会残留"无新支撑"的节点与边（import_jd 的 REQUIRES、旧聚合的
weight 边、HAS_EVIDENCE），本脚本按清单 DETACH DELETE 清除。

保留策略：证据节点不删（jd_raw 有原始记录，技能 EVIDENCED_BY 引用链完整，
与 _purge_dup_import_residue 同口径）；IT/Web 为剥壳产物（IT经理→IT）不拦
不删；技术栈细分岗位（Angular前端等）是设计保留，不在清单。

用法：
    python scripts/cleanup_position_fragments.py          # 正式执行
    python scripts/cleanup_position_fragments.py --dry-run  # 只预览不删

依赖 Neo4j 可达（config.NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD）。
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from app.core.database import neo4j_driver

# P7 拦截清单（与 dictionary._POSITION_STOPWORDS P7 批次一致；IT/Web 裸词
# 是 IT经理/Web经理 剥壳产物，保留不入清单）
FRAGMENT_NAMES = [
    "CNO", "GTM", "Pega", "OpenResty", "Salesforce", "Salesforce 应用",
    ".Net", "C#/.NET",
    "AI 证据", "AI证据", "BLT 首席", "340B 项目分析", "RFP 文案",
    "AI/ML应用", "AI/ML与生成式AI", "Web内容平台", "Web与移动端", "云/DevSecOps",
    "AI 与自动化",
    "IT支持", "IT 支持", "IT 系统", "IT站点", "IT研发系统",
    "IT站点技术支持", "IT流程自动化", "IT系统管理员",
    "CMDB发现", "AR/VR设计验证", "Gemini 应用合作伙伴", "GRC自动化",
    # 映射词（存量碎片节点删除，聚合会在规范岗位重建边）
    "SDET", "QA", "QA自动化", "首席统计师",
    # AI 泛词族（T-04 第二批 2026-08-15）：加入 _GENERIC_ROUTED_FAMILIES 后按
    # JD 技能路由到细分族，存量节点失去聚合支撑（聚合输出细分族名），残留
    # 节点在此清除；证据/技能边随节点删，聚合会在细分族重建
    "AI应用", "AI产品", "AI自动化", "AI 自动化", "AI建模", "AI构建", "AI 构建",
    "AI智能体", "AI与智能体", "AI 模型部署", "LLM 应用", "AI 性能", "AI性能",
    "AI 原生构建", "AI基础设施", "云AI", "预测科学与AI", "AI前向部署", "AI与LLM",
    "AI智能体与RAG系统", "智能体AI首席", "智能体 AI", "企业AI平台",
    "生成式AI应用", "Azure 平台", "应用AI客户", "应用AI智能体",
    "零售运营与AI参与", "客户策略分析及应用AI", "云AI客户", "AI验证", "AIoT",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只预览匹配节点，不执行删除")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) WHERE p.name IN $names "
            "OPTIONAL MATCH (p)-[:HAS_EVIDENCE]->(e:Evidence) "
            "OPTIONAL MATCH (p)-[r:REQUIRES]->(:Skill) "
            "RETURN p.name AS name, p.id AS id, p.status AS status, p.freq AS freq, "
            "       count(e) AS ev, count(r) AS edges ORDER BY p.name",
            names=FRAGMENT_NAMES,
        ).data()
    print(f"匹配碎片岗位节点: {len(rows)}")
    for r in rows:
        print(f"  {r['name']!r} | id={r['id']} | status={r['status']} | freq={r['freq']} "
              f"| 证据={r['ev']} | 边={r['edges']}")
    if args.dry_run or not rows:
        return 0

    backup = f"reports/position_fragments_cleanup_{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    with open(backup, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[备份] {backup}")

    with neo4j_driver.session() as session:
        n = session.run(
            "MATCH (p:Position) WHERE p.name IN $names DETACH DELETE p RETURN count(p) AS n",
            names=[r["name"] for r in rows],
        ).single()["n"]
    print(f"[完成] 删除碎片岗位节点 {n} 个（证据节点保留）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
