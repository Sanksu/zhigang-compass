"""岗位归一化回填（加固版，2026-08-17）。

背景：jd_raw.snapshot.normalized_position 由岗位归一化收敛引入，存量已抽取
记录缺该键；本脚本按当前确定性规则补写，绝不修改 extraction.position_name。

安全设计：
- 默认 plan 模式：只读扫描 → 生成不可变清单（每行含整快照备份 + 快照哈希 +
  分类 + 元数据校验和文件），不写数据库；清单路径打印在输出中。
- --apply：单事务内对清单目标行执行条件 UPDATE（jsonb_set 仅更新规范名与版本
  元数据）；只允许清单中的旧值/旧版本仍匹配时写入。实际更新行数
  ≠ --expect-updated 时整体回滚。
- --verify：只读校验（目标值与版本一致 / 排除行未变 / 原始岗位名未变 /
  非目标快照哈希未变）。
- --rollback：按清单整快照恢复，仅当现值及版本仍等于本次写入结果。

用法（cwd=backend）：
    python -m scripts.backfill_normalized_positions                 # plan 写清单
    python -m scripts.backfill_normalized_positions --apply <清单> --expect-updated 5556
    python -m scripts.backfill_normalized_positions --verify <清单>
    python -m scripts.backfill_normalized_positions --rollback <清单>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text

from app.core.database import async_session_factory, engine
from app.models.raw import JDRaw
from app.services.extraction.position_normalization import (
    POSITION_NORMALIZATION_VERSION,
    normalization_version,
    normalized_position_from_snapshot,
)

REPORTS_DIR = ROOT / "reports"


def _snapshot_hash(snapshot: dict) -> str:
    """规范化 JSON 快照哈希（校验清单行自洽/非目标行未变）。"""
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _without_normalization(snapshot: dict) -> dict:
    """返回不含归一化缓存及其版本元数据的快照副本。"""
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"normalized_position", "normalized_position_meta"}
    }


def _version_diff(lines: list[dict]) -> dict[str, int]:
    """按旧版到新版汇总清单中的归一化版本迁移差异。"""
    result: dict[str, int] = {}
    for line in lines:
        key = (
            f"{line['old_normalization_version'] or 'legacy'}"
            f"→{line['new_normalization_version']}"
        )
        result[key] = result.get(key, 0) + 1
    return result


def _revision() -> str:
    try:
        import subprocess

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return f"{head}(dirty={bool(dirty)})"
    except Exception:
        return "unknown"


def _classify(snapshot: dict, value: str) -> tuple[str, str, str]:
    """返回 (classification, old_normalized, old_version)。"""
    old = str(snapshot.get("normalized_position") or "").strip()
    old_version = normalization_version(snapshot)
    if not value:
        extraction = snapshot.get("extraction") or {}
        if extraction.get("skipped"):
            return "empty_skipped", old, old_version
        if not str(extraction.get("position_name") or "").strip():
            return "empty_raw_name", old, old_version
        return "rejected_by_rules", old, old_version
    if old and old == value and old_version == POSITION_NORMALIZATION_VERSION:
        return "existing_current", old, old_version
    if old and old == value:
        return "version_upgrade", old, old_version
    return "target", old, old_version


async def plan(batch_size: int, start_after_id: int) -> Path:
    """只读扫描并生成不可变清单（含整快照备份，供回滚）。"""
    lines: list[dict] = []
    last_id = start_after_id
    while True:
        async with async_session_factory() as session:
            rows = (
                await session.scalars(
                    select(JDRaw)
                    .where(
                        JDRaw.id > last_id,
                        JDRaw.snapshot["extraction"].astext.isnot(None),
                    )
                    .order_by(JDRaw.id.asc())
                    .limit(batch_size)
                )
            ).all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                snap = dict(row.snapshot or {})
                value = normalized_position_from_snapshot(snap)
                classification, old, old_version = _classify(snap, value)
                lines.append(
                    {
                        "jd_raw_id": row.id,
                        "source": row.source,
                        "source_id": row.source_id,
                        "fingerprint": row.fingerprint,
                        "raw_position_name": str((snap.get("extraction") or {}).get("position_name") or ""),
                        "old_normalized": old,
                        "old_normalization_version": old_version,
                        "new_normalized": value,
                        "new_normalization_version": POSITION_NORMALIZATION_VERSION,
                        "classification": classification,
                        "snapshot": snap,
                        "snapshot_hash": _snapshot_hash(snap),
                    }
                )

    counts: dict[str, int] = {}
    for line in lines:
        counts[line["classification"]] = counts.get(line["classification"], 0) + 1
    ids = [line["jd_raw_id"] for line in lines]
    if len(ids) != len(set(ids)):
        raise SystemExit("清单出现重复 jd_raw_id，终止")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = REPORTS_DIR / f"normalized_position_backfill_{ts}.jsonl"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    checksum = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    meta = {
        "manifest": manifest_path.name,
        "checksum_sha256": checksum,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "revision": _revision(),
        "normalization_version": POSITION_NORMALIZATION_VERSION,
        "normalization_version_diff": _version_diff(lines),
        "counts": counts,
        "id_min": min(ids) if ids else None,
        "id_max": max(ids) if ids else None,
        "batch_size": batch_size,
        "start_after_id": start_after_id,
    }
    meta_path = manifest_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"清单: {manifest_path}")
    return manifest_path


def _load_manifest(path: Path) -> list[dict]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [line["jd_raw_id"] for line in lines]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"清单 {path} 存在重复 jd_raw_id，终止")
    return lines


async def apply_manifest(path: Path, expect_updated: int) -> None:
    """单事务条件写入；实际更新行数 != 预期时整体回滚。"""
    lines = _load_manifest(path)
    targets = [
        line for line in lines if line["classification"] in {"target", "version_upgrade"}
    ]
    if len(targets) != expect_updated:
        raise SystemExit(
            f"清单目标 {len(targets)} != 预期 {expect_updated}，终止（先重新 plan 并核对）"
        )
    statement = text(
        """
        UPDATE jd_raw
        SET snapshot = jsonb_set(
                jsonb_set(
                    COALESCE(snapshot, '{}'::jsonb), '{normalized_position}',
                    to_jsonb(:value::text), true),
                '{normalized_position_meta}',
                jsonb_build_object('version', :version::text), true),
            updated_at = now()
        WHERE id = :id
          AND snapshot ? 'extraction'
          AND COALESCE(snapshot->>'normalized_position', '') = :old_value
          AND COALESCE(snapshot->'normalized_position_meta'->>'version', '') = :old_version
          AND NOT COALESCE((snapshot->'extraction'->>'skipped')::boolean, false)
          AND NULLIF(btrim(snapshot->'extraction'->>'position_name'), '') IS NOT NULL
        """
    )
    async with engine.begin() as conn:
        updated = 0
        for line in targets:
            result = await conn.execute(
                statement,
                {
                    "id": line["jd_raw_id"],
                    "value": line["new_normalized"],
                    "version": line["new_normalization_version"],
                    "old_value": line["old_normalized"],
                    "old_version": line["old_normalization_version"],
                },
            )
            updated += result.rowcount
        if updated != expect_updated:
            raise RuntimeError(
                f"实际更新 {updated} != 预期 {expect_updated}，事务整体回滚，未提交任何行"
            )
    print(f"apply 完成: 更新 {updated} 行（清单 {path.name}）")


async def verify_manifest(path: Path) -> None:
    """只读校验：目标值一致 / 原始岗位名未变 / 非目标快照未变。"""
    lines = _load_manifest(path)
    targets = [
        line for line in lines if line["classification"] in {"target", "version_upgrade"}
    ]
    others = [
        line for line in lines if line["classification"] not in {"target", "version_upgrade"}
    ]
    failures: list[str] = []

    async with async_session_factory() as session:
        if targets:
            target_ids = [line["jd_raw_id"] for line in targets]
            rows = (await session.scalars(
                select(JDRaw).where(JDRaw.id.in_(target_ids))
            )).all()
            by_id = {row.id: row for row in rows}
            for line in targets:
                row = by_id.get(line["jd_raw_id"])
                snap = dict(row.snapshot or {}) if row else None
                if snap is None:
                    failures.append(f"{line['jd_raw_id']}: 行不存在")
                    continue
                if str(snap.get("normalized_position") or "") != line["new_normalized"]:
                    failures.append(f"{line['jd_raw_id']}: 现值 != 清单值")
                if normalization_version(snap) != line["new_normalization_version"]:
                    failures.append(f"{line['jd_raw_id']}: 归一化版本 != 清单版本")
                raw = str((snap.get("extraction") or {}).get("position_name") or "")
                if raw != line["raw_position_name"]:
                    failures.append(f"{line['jd_raw_id']}: 原始岗位名被修改")
                if _snapshot_hash(_without_normalization(snap)) != _snapshot_hash(
                    _without_normalization(line["snapshot"])
                ):
                    failures.append(f"{line['jd_raw_id']}: 非归一化部分快照哈希不一致")

        if others:
            other_ids = [line["jd_raw_id"] for line in others]
            rows = (await session.scalars(
                select(JDRaw).where(JDRaw.id.in_(other_ids))
            )).all()
            by_id = {row.id: row for row in rows}
            for line in others:
                row = by_id.get(line["jd_raw_id"])
                snap = dict(row.snapshot or {}) if row else None
                if snap is None:
                    failures.append(f"{line['jd_raw_id']}: 行不存在")
                    continue
                if line["classification"] == "existing_current":
                    # 计划时已存在当前版本的值：校验其仍与清单一致（不要求空白）。
                    if str(snap.get("normalized_position") or "") != line["new_normalized"]:
                        failures.append(f"{line['jd_raw_id']}: 现值 != 清单既有值")
                    if normalization_version(snap) != line["new_normalization_version"]:
                        failures.append(f"{line['jd_raw_id']}: 归一化版本 != 清单版本")
                    continue
                if _snapshot_hash(snap) != line["snapshot_hash"]:
                    failures.append(f"{line['jd_raw_id']}: 排除行快照被修改")
                if str(snap.get("normalized_position") or "").strip():
                    failures.append(f"{line['jd_raw_id']}: 排除行被写入 normalized_position")

    if failures:
        raise SystemExit(f"校验失败（{len(failures)} 项）: {failures[:10]}")
    print(f"verify 通过: 目标 {len(targets)} 行一致，排除 {len(others)} 行未变（清单 {path.name}）")


async def rollback_manifest(path: Path) -> None:
    """按清单整快照恢复；仅当现值 == 本次写入值（防止覆盖后续改动）。"""
    lines = _load_manifest(path)
    targets = [
        line for line in lines if line["classification"] in {"target", "version_upgrade"}
    ]
    statement = text(
        """
        UPDATE jd_raw
        SET snapshot = :old_snapshot::jsonb, updated_at = now()
        WHERE id = :id
          AND snapshot->>'normalized_position' = :new_value
          AND COALESCE(snapshot->'normalized_position_meta'->>'version', '') = :new_version
        """
    )
    async with engine.begin() as conn:
        rolled = 0
        for line in targets:
            result = await conn.execute(
                statement,
                {
                    "id": line["jd_raw_id"],
                    "old_snapshot": json.dumps(line["snapshot"], ensure_ascii=False),
                    "new_value": line["new_normalized"],
                    "new_version": line["new_normalization_version"],
                },
            )
            rolled += result.rowcount
    print(f"rollback 完成: 恢复 {rolled}/{len(targets)} 行（清单 {path.name}）")
    if rolled != len(targets):
        raise SystemExit("部分行现值与本次写入值不一致，未恢复；请人工核对")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="MANIFEST", help="执行清单写入（需 --expect-updated）")
    parser.add_argument("--expect-updated", type=int, help="apply 预期更新行数（须与清单目标数一致）")
    parser.add_argument("--verify", metavar="MANIFEST", help="只读校验清单执行结果")
    parser.add_argument("--rollback", metavar="MANIFEST", help="按清单回滚")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--start-after-id", type=int, default=0)
    args = parser.parse_args()

    if args.apply:
        if args.expect_updated is None:
            parser.error("--apply 必须提供 --expect-updated")
        asyncio.run(apply_manifest(Path(args.apply), args.expect_updated))
    elif args.verify:
        asyncio.run(verify_manifest(Path(args.verify)))
    elif args.rollback:
        asyncio.run(rollback_manifest(Path(args.rollback)))
    else:
        if args.batch_size < 1:
            parser.error("--batch-size 必须为正整数")
        asyncio.run(plan(args.batch_size, args.start_after_id))


if __name__ == "__main__":
    main()
