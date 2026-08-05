"""O*NET 权威岗位库导入脚本（AL-M4-01，设计文档 5.1 Occupation 节点 / 7.2.3 RAG 接地）。

从 O*NET 官方 CSV（30.3 版）下载 Occupation Data（1016 标准职业）与 Job Titles
（别名表）→ 聚合 → 幂等 upsert 至 PostgreSQL `occupations` 表，并同步双路检索索引：
1. pgvector 语义向量（occupations.embedding，Sentence-BERT 384 维，模型不可用跳过）
2. Neo4j Occupation 节点（occupation_search 全文索引数据源，Neo4j 不可达跳过）

任一索引同步失败不影响入库（RAG 接地是辅助确认，降级 ILIKE 关键词路）。

用法：
    python scripts/import_occupations.py                    # 在线下载后导入（全量刷新）
    python scripts/import_occupations.py --no-write         # 仅下载/统计，不写库（冒烟）
    python scripts/import_occupations.py --csv-dir data/onet  # 使用本地缓存 CSV（离线）
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

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.business import Occupation

# O*NET 30.3 官方 CSV 地址（Creative Commons 许可，见 onetcenter.org/license_db.html）
_OCCUPATION_DATA_URL = "https://www.onetcenter.org/dl_files/database/db_30_3_csv/occupation_data.csv"
_JOB_TITLES_URL = "https://www.onetcenter.org/dl_files/database/db_30_3_csv/job_titles.csv"

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


async def _upsert(session, code: str, name: str, category: str, definition: str, aliases: list[str]) -> bool:
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
                source="onet",
            )
        )
        return True
    existing.name = name
    existing.category = category
    existing.definition = definition
    existing.aliases = aliases
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
        print(f"  ! 语义模型不可用，跳过向量生成: {e}")
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
        print(f"  ! Neo4j 同步失败（接地将降级为 ILIKE）: {e}")
        return 0
    return len(rows)


async def main(write: bool, csv_dir: Path | None) -> None:
    occupation_rows = _load_occupation_data(csv_dir)
    alias_map = _load_job_titles(csv_dir)
    print(f"Occupation Data {len(occupation_rows)} 条 | Job Titles 别名 {sum(len(v) for v in alias_map.values())} 条")

    created = updated = 0
    async with async_session_factory() as session:
        for row in occupation_rows:
            code, name = row[0].strip(), row[1].strip()
            definition = row[2].strip() if len(row) > 2 else ""
            category = _category_for(code)
            aliases = alias_map.get(code, [])
            if not write:
                continue
            is_new = await _upsert(session, code, name, category, definition, aliases)
            created += is_new
            updated += not is_new
        if write:
            await session.commit()

    if not write:
        print("冒烟模式（--no-write）：解析校验通过，未写库")
        return
    print(f"导入完成: 新建 {created} / 更新 {updated} | 总量 {created + updated}")

    # 双路检索索引同步（T-06）：向量（语义路）+ Neo4j 全文（关键词路）
    async with async_session_factory() as session:
        vec_count = await _fill_embeddings(session)
        neo4j_count = await _sync_neo4j(session)
    print(f"索引同步: pgvector 向量 {vec_count} 条 | Neo4j Occupation 节点 {neo4j_count} 个")

    # 抽样展示 IT 大类（15/17）数据，便于人工核验
    sample = [r for r in occupation_rows if r[0].startswith(("15-", "17-"))][:5]
    for row in sample:
        print(f"  {row[0]} | {row[1]} | aliases={alias_map.get(row[0].strip(), [])[:3]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入 O*NET 权威岗位库")
    parser.add_argument("--no-write", action="store_true", help="仅下载解析，不写库")
    parser.add_argument("--csv-dir", type=Path, default=None, help="本地 CSV 目录（离线模式）")
    args = parser.parse_args()
    asyncio.run(main(write=not args.no_write, csv_dir=args.csv_dir))
