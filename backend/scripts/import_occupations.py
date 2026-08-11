"""权威岗位库三源导入脚本（AL-M4-01，设计文档 5.1 Occupation 节点 / 7.2.3 RAG 接地）。

三源：O*NET / 人社部《国家职业分类大典（2022版）》/ LinkedIn Emerging Jobs，
均落同一 `occupations` 表（source 字段区分），供 RAG 接地一次检索天然覆盖。

- `--source onet`：从 O*NET 官方 CSV（30.3 版）下载 Occupation Data（1016 标准职业）
  与 Job Titles（别名表）→ 聚合 → 幂等 upsert。
- `--source hrss` / `--source linkedin`：默认导入内置精简样例子集（仅用于验证三源检索
  链路，code/定义以权威文件为准）；如需导入正式数据，用 `--csv-dir` 提供
  `hrss_occupations.csv` / `linkedin_occupations.csv`（列：code,name,category,definition,aliases，
  aliases 分号分隔）。

导入后同步双路检索索引：
1. pgvector 语义向量（occupations.embedding，Sentence-BERT 384 维，模型不可用跳过）
2. Neo4j Occupation 节点（occupation_search 全文索引数据源，Neo4j 不可达跳过）

任一索引同步失败不影响入库（RAG 接地是辅助确认，降级 ILIKE 关键词路）。

用法：
    python scripts/import_occupations.py                            # O*NET 在线导入（全量刷新）
    python scripts/import_occupations.py --source hrss              # 人社部样例子集导入
    python scripts/import_occupations.py --source linkedin          # LinkedIn 样例子集导入
    python scripts/import_occupations.py --source hrss --no-write   # 仅解析/统计，不写库（冒烟）
    python scripts/import_occupations.py --csv-dir data/onet        # 使用本地缓存 CSV（离线）
"""

import argparse
import asyncio
import csv
import io
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("import_occupations")

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.business import Occupation

# O*NET 30.3 官方 CSV 地址（Creative Commons 许可，见 onetcenter.org/license_db.html）
_OCCUPATION_DATA_URL = "https://www.onetcenter.org/dl_files/database/db_30_3_csv/occupation_data.csv"
_JOB_TITLES_URL = "https://www.onetcenter.org/dl_files/database/db_30_3_csv/job_titles.csv"

# 人社部《国家职业分类大典（2022版）》数字技术职业内置精简样例子集。
# 示例数据，仅用于验证三源检索链路；code 与定义以权威文件为准，
# 正式数据请用 --csv-dir 提供 hrss_occupations.csv 覆盖。
# 列：code,name,category,definition,aliases（aliases 分号分隔）
_HRSS_SAMPLE_CSV = """code,name,category,definition,aliases
2-02-38-01,人工智能工程技术人员,数字技术工程技术人员,从事人工智能相关算法、深度学习技术的分析研究开发，设计优化运维管理和应用人工智能系统,AI工程师;人工智能研发工程师
2-02-38-03,大数据工程技术人员,数字技术工程技术人员,从事大数据采集、清洗、分析、治理、挖掘及大数据系统规划设计开发和运维,大数据工程师
2-02-38-04,云计算工程技术人员,数字技术工程技术人员,从事云计算系统架构规划设计开发部署与运维,云计算工程师
2-02-38-08,区块链工程技术人员,数字技术工程技术人员,从事区块链底层技术架构、共识机制、智能合约及区块链系统开发运维,区块链工程师
2-02-38-12,数据安全工程技术人员,数字技术工程技术人员,从事数据安全治理、数据全生命周期安全防护及数据安全评估,数据安全工程师
2-02-30-09,数据分析处理工程技术人员,数字技术工程技术人员,从事数据分析处理系统的设计开发与数据分析应用,数据分析师;数据分析工程师
2-06-07-13,数字化管理师,数字技术相关管理人员,利用数字化技术将业务流程与数字化工具结合以提升管理效能,数字化运营;数字化转型顾问
4-04-04-04,信息安全测试员,数字技术应用与服务人员,对信息系统与网络开展安全测试、漏洞挖掘与风险评估,渗透测试工程师;安全测试工程师
2-02-10-03,计算机软件工程技术人员,数字技术工程技术人员,从事计算机软件的设计开发测试维护与技术服务,软件工程师;软件开发工程师
2-02-10-07,信息安全工程技术人员,数字技术工程技术人员,从事信息安全系统的规划设计集成运维与风险评估,信息安全工程师
"""

# LinkedIn Emerging Jobs 年度报告核心新兴岗位内置精简样例子集。
# 示例数据，仅用于验证三源检索链路；正式数据请用 --csv-dir 提供 linkedin_occupations.csv 覆盖。
_LINKEDIN_SAMPLE_CSV = """code,name,category,definition,aliases
LI-0001,AI Engineer,Artificial Intelligence,Designs and deploys AI/LLM solutions including RAG pipelines agent systems and model tuning,AI工程师;人工智能工程师
LI-0002,Machine Learning Engineer,Artificial Intelligence,Builds and maintains ML models and MLOps pipelines for production,机器学习工程师
LI-0003,Data Engineer,Data Infrastructure,Designs data pipelines warehouses and streaming infrastructure,数据工程师
LI-0004,Cloud Engineer,Cloud Infrastructure,Architects and operates multi-cloud platforms and CI/CD infrastructure,云计算工程师;云平台工程师
LI-0005,MLOps Engineer,Machine Learning,Operationalizes ML model lifecycle including deployment monitoring and retraining,MLOps工程师
LI-0006,RAG Engineer,Generative AI,Develops retrieval-augmented generation systems and vector-based knowledge retrieval,检索增强生成工程师;RAG开发工程师
LI-0007,AI Agent Engineer,Generative AI,Builds LLM-powered autonomous agents and orchestration workflows,智能体工程师;AI Agent开发
LI-0008,Generative AI Engineer,Generative AI,Designs generative model applications for content and code generation,AIGC工程师;生成式AI工程师
LI-0009,Cybersecurity Specialist,Cybersecurity,Protects systems against cyber threats and manages incident response,网络安全专家;信息安全专家
LI-0010,Data Scientist,Data Science,Analyzes data to derive insights and build predictive models,数据科学家
"""

# SOC major group（前两位代码 → 大类名，用于 category 字段）
_SOC_MAJOR_GROUPS = {
    "11": "Management",
    "13": "Business and Financial Operations",
    "15": "Computer and Mathematical",
    "17": "Architecture and Engineering",
    "19": "Life, Physical, and Social Science",
    "21": "Community and Social Service",
    "23": "Legal",
    "25": "Educational Instruction and Library",
    "27": "Arts, Design, Entertainment, Sports, and Media",
    "29": "Healthcare Practitioners and Technical",
    "31": "Healthcare Support",
    "33": "Protective Service",
    "35": "Food Preparation and Serving Related",
    "37": "Building and Grounds Cleaning and Maintenance",
    "39": "Personal Care and Service",
    "41": "Sales and Related",
    "43": "Office and Administrative Support",
    "45": "Farming, Fishing, and Forestry",
    "47": "Construction and Extraction",
    "49": "Installation, Maintenance, and Repair",
    "51": "Production",
    "53": "Transportation and Material Moving",
    "55": "Military Specific",
}


def _category_for(code: str) -> str:
    """由 O*NET-SOC 代码前两位推导 major group 大类名。"""
    return _SOC_MAJOR_GROUPS.get(code.split("-")[0][:2], "")


def _fetch_csv(url: str) -> io.StringIO:
    """下载 CSV 并返回文本流（UTF-8）。"""
    with urllib.request.urlopen(url, timeout=60) as resp:
        return io.StringIO(resp.read().decode("utf-8"))


def _parse_rows(handle: io.StringIO, expected: int) -> list[list[str]]:
    """解析 CSV（引号内逗号安全），跳过表头，校验行数下限。"""
    reader = csv.reader(handle)
    next(reader, None)  # 表头
    rows = [row for row in reader if row and row[0].strip()]
    if len(rows) < expected:
        raise RuntimeError(f"CSV 行数异常: 期望 ≥{expected}，实际 {len(rows)}")
    return rows


def _parse_sample_csv(text: str) -> list[list[str]]:
    """解析内置样例子集 CSV（首行为表头，跳过 # 注释行与空行）。"""
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # 表头
    return [
        row for row in reader
        if row and row[0].strip() and not row[0].lstrip().startswith("#")
    ]


def _parse_aliases(cell: str) -> list[str]:
    """解析别名列（分号分隔）。"""
    return [a.strip() for a in (cell or "").split(";") if a.strip()]


def _load_occupation_data(csv_dir: Path | None) -> list[list[str]]:
    """加载 Occupation Data（code, title, description）。优先本地缓存，否则在线下载。"""
    if csv_dir is not None:
        path = csv_dir / "occupation_data.csv"
        if not path.exists():
            raise FileNotFoundError(f"本地 CSV 不存在: {path}")
        return _parse_rows(io.StringIO(path.read_text(encoding="utf-8")), 1000)
    return _parse_rows(_fetch_csv(_OCCUPATION_DATA_URL), 1000)


def _load_job_titles(csv_dir: Path | None) -> dict[str, list[str]]:
    """加载 Job Titles（code → 别名列表）。仅取 Source=09/10（用户提交/雇主招聘）可信别名。"""
    def _iter_rows():
        if csv_dir is not None:
            path = csv_dir / "job_titles.csv"
            if not path.exists():
                raise FileNotFoundError(f"本地 CSV 不存在: {path}")
            return _parse_rows(io.StringIO(path.read_text(encoding="utf-8")), 50000)
        return _parse_rows(_fetch_csv(_JOB_TITLES_URL), 50000)

    aliases: dict[str, list[str]] = defaultdict(list)
    for row in _iter_rows():
        code = row[0].strip()
        job_title = (row[2] or "").strip()
        sources = row[4] if len(row) > 4 else ""
        # 仅采信雇主招聘/用户提交来源（10/09），避免陈旧分类系统的噪音别名
        if job_title and ("10" in sources or "09" in sources):
            if job_title not in aliases[code]:
                aliases[code].append(job_title)
    return dict(aliases)


def _load_source_rows(source: str, csv_dir: Path | None) -> list[tuple[str, str, str, str, list[str]]]:
    """加载指定来源岗位数据（code, name, category, definition, aliases）。

    onet 沿用 O*NET 官方 CSV（occupation_data + job_titles）；
    hrss/linkedin 优先读取本地 <source>_occupations.csv（--csv-dir 提供），
    缺省使用内置精简样例子集（示例数据，正式以权威文件为准）。
    """
    if source == "onet":
        occupation_rows = _load_occupation_data(csv_dir)
        alias_map = _load_job_titles(csv_dir)
        return [
            (
                r[0].strip(),
                r[1].strip(),
                _category_for(r[0].strip()),
                r[2].strip() if len(r) > 2 else "",
                alias_map.get(r[0].strip(), []),
            )
            for r in occupation_rows
        ]
    if csv_dir is not None:
        path = csv_dir / f"{source}_occupations.csv"
        if not path.exists():
            raise FileNotFoundError(f"本地 CSV 不存在: {path}")
        text = path.read_text(encoding="utf-8")
    else:
        text = _HRSS_SAMPLE_CSV if source == "hrss" else _LINKEDIN_SAMPLE_CSV
    return [
        (
            r[0].strip(),
            r[1].strip(),
            r[2].strip() if len(r) > 2 else "",
            r[3].strip() if len(r) > 3 else "",
            _parse_aliases(r[4] if len(r) > 4 else ""),
        )
        for r in _parse_sample_csv(text)
    ]


async def _upsert(
    session, code: str, name: str, category: str, definition: str, aliases: list[str], source: str
) -> bool:
    """按 code upsert 单条记录，返回是否新建。"""
    existing = await session.scalar(select(Occupation).where(Occupation.code == code))
    if existing is None:
        session.add(
            Occupation(
                code=code,
                name=name,
                category=category,
                definition=definition,
                aliases=aliases,
                source=source,
            )
        )
        return True
    existing.name = name
    existing.category = category
    existing.definition = definition
    existing.aliases = aliases
    existing.source = source
    return False


def _embed_text(name: str, category: str) -> str:
    """向量化文本：岗位名为主（跨语言语义匹配），叠加大类名增强上下文。"""
    return f"{name} {category}".strip()


async def _fill_embeddings(session) -> int:
    """为 occupations 批量生成 embedding 向量（pgvector 语义路）。

    模型不可用/加载失败时静默跳过（向量保持 NULL，接地语义路降级为关键词路），
    不阻塞导入。
    """
    from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder

    rows = (await session.scalars(select(Occupation))).all()
    if not rows:
        return 0
    try:
        embedder = SkillEmbedder.get()
        texts = [_embed_text(r.name, r.category) for r in rows]
        embedder.warm(texts)  # 一次 batch encode 填缓存，避免逐条前向推理
        vecs = [embedder.embed(t) for t in texts]
    except SemanticUnavailableError as e:
        logger.warning(f"  ! 语义模型不可用，跳过向量生成: {e}")
        return 0
    for occ, vec in zip(rows, vecs):
        occ.embedding = vec
    await session.commit()
    return len(rows)


async def _sync_neo4j(session) -> int:
    """同步 Occupation 节点到 Neo4j（occupation_search 全文索引数据源）。

    MERGE by code 幂等 upsert；Neo4j 不可达时打印告警不失败
    （接地关键词路降级为 PostgreSQL ILIKE）。
    """
    from app.core.database import neo4j_driver

    rows = (await session.scalars(select(Occupation))).all()
    if not rows:
        return 0
    payload = [
        {
            "code": occ.code,
            "name": occ.name,
            "category": occ.category,
            "definition": occ.definition,
            "aliases": occ.aliases,
        }
        for occ in rows
    ]
    try:
        with neo4j_driver.session() as ns:
            ns.run(
                "UNWIND $rows AS r "
                "MERGE (o:Occupation {code: r.code}) "
                "SET o.name = r.name, o.category = r.category, "
                "o.definition = r.definition, o.aliases = r.aliases",
                rows=payload,
            )
    except Exception as e:
        logger.warning("  ! Neo4j 同步失败（接地将降级为 ILIKE）: %s", e)
        return 0
    return len(rows)


async def main(write: bool, csv_dir: Path | None, source: str) -> None:
    rows = _load_source_rows(source, csv_dir)
    alias_total = sum(len(aliases) for _, _, _, _, aliases in rows)
    logger.info("来源 %s: 岗位 %s 条 | 别名 %s 条", source, len(rows), alias_total)

    created = updated = 0
    async with async_session_factory() as session:
        for code, name, category, definition, aliases in rows:
            if not write:
                continue
            is_new = await _upsert(session, code, name, category, definition, aliases, source)
            created += is_new
            updated += not is_new
        if write:
            await session.commit()

    if not write:
        logger.info("冒烟模式（--no-write）：解析校验通过，未写库")
        return
    logger.info(f"导入完成: 新建 {created} / 更新 {updated} | 总量 {created + updated}")

    # 双路检索索引同步（T-06）：向量（语义路）+ Neo4j 全文（关键词路）
    # 全量刷新（occupations 表整体重算向量/重写节点，覆盖三源）
    async with async_session_factory() as session:
        vec_count = await _fill_embeddings(session)
        neo4j_count = await _sync_neo4j(session)
    logger.info(f"索引同步: pgvector 向量 {vec_count} 条 | Neo4j Occupation 节点 {neo4j_count} 个")

    # 抽样展示本次导入数据，便于人工核验
    for code, name, *_ in rows[:5]:
        logger.info(f"  {code} | {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入权威岗位库（三源）")
    parser.add_argument(
        "--source",
        choices=("onet", "hrss", "linkedin"),
        default="onet",
        help="导入来源：onet（O*NET 官方 CSV）/ hrss（人社部大典）/ linkedin（LinkedIn Emerging Jobs）",
    )
    parser.add_argument("--no-write", action="store_true", help="仅解析，不写库（冒烟）")
    parser.add_argument("--csv-dir", type=Path, default=None, help="本地 CSV 目录（离线模式）")
    args = parser.parse_args()
    asyncio.run(main(write=not args.no_write, csv_dir=args.csv_dir, source=args.source))
