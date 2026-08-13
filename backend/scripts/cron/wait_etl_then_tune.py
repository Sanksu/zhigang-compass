"""等待 ETL 完成后重跑图算法 Optuna（根因五自动化）。

轮询 Redis arq job 完成（当前手动触发的 run_etl_pipeline），ETL 结束时
图已定型（阶段 14 快照已发布、15 自动流转已回写），此时：
    1. 导出最终共现图快照（temp/ 不入库）
    2. Optuna 调优 leiden（--trials 50）
    3. --apply 写回 configs/graph_algo.yaml
    4. 重建 Neo4j Community 索引（sync_communities.py 读新配置）

用法：.venv/Scripts/python.exe scripts/cron/wait_etl_then_tune.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PY = BACKEND / ".venv" / "Scripts" / "python.exe"
JOB_ID = "355a3662709a4974a4824bc4f76126ac"  # 11:51 手动触发的 run_etl_pipeline
POLL_SEC = 60
MAX_WAIT_MIN = 180


def _redis() -> tuple[str, list[str]]:
    """读取 arq:queue 当前成员（docker exec redis-cli 无依赖）。"""
    out = subprocess.run(
        ["docker", "exec", "zhigang-redis", "redis-cli", "-n", "1", "ZRANGE", "arq:queue", "0", "-1"],
        capture_output=True, text=True, timeout=30,
    )
    members = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    return out.stdout, members


def etl_done() -> bool:
    """job 不在队列且 in-progress 键消失 → 已消费完成。"""
    _, members = _redis()
    if JOB_ID in members:
        return False
    # in-progress 键存在说明仍在执行（arq 2.x 消费时写入 hash）
    r = subprocess.run(
        ["docker", "exec", "zhigang-redis", "redis-cli", "-n", "1", "EXISTS",
         f"arq:in-progress:{JOB_ID}"],
        capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip() == "0"


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    p = subprocess.run([str(PY), *cmd], cwd=str(BACKEND), timeout=7200)
    if p.returncode != 0:
        raise SystemExit(f"命令失败（exit={p.returncode}）：{' '.join(str(c) for c in cmd)}")


def main() -> int:
    print(f"[wait_etl_then_tune] 轮询 ETL job {JOB_ID} 完成（每 {POLL_SEC}s，上限 {MAX_WAIT_MIN}min）", flush=True)
    waited = 0
    while not etl_done():
        waited += POLL_SEC
        if waited > MAX_WAIT_MIN * 60:
            print(f"[wait_etl_then_tune] 超时（{MAX_WAIT_MIN}min），job 仍在队列/执行中，请人工检查", flush=True)
            return 2
        time.sleep(POLL_SEC)
    print(f"[wait_etl_then_tune] ETL job 完成（等待 {waited // 60}min）→ 开始图算法参数重调", flush=True)
    # 给 worker 收尾（写 graph_versions 已含在 job 内，无需额外等待）

    snapshot = BACKEND / "temp" / "graph_cooccurrence_latest.json"
    run(["scripts/graph_algo_tune.py", "--export", str(snapshot)])
    print(f"[wait_etl_then_tune] 已导出最终图快照: {snapshot.relative_to(BACKEND)}", flush=True)

    run(["scripts/graph_algo_tune.py", "--snapshot", str(snapshot),
         "--algorithm", "leiden", "--trials", "50", "--apply"])
    print("[wait_etl_then_tune] Optuna 完成，新参数已写回 configs/graph_algo.yaml", flush=True)

    # ── 参数落地前全量图验证（P1）：调参快照按旧 min_weight 过滤导出，新参数
    # 应用到全量图可能二次过滤过度（08-13 实测 mw=2.325 在真实图只留 75/1245
    # 节点，sync_communities 门禁拦截）。验证失败回滚旧参数并保留旧索引。 ──
    import json as _json

    cfg_path = BACKEND / "configs" / "graph_algo.yaml"
    _cfg_before = cfg_path.read_text(encoding="utf-8")
    from app.services.graph_algorithms.config import load_graph_algo_config as _load_cfg
    from app.services.graph_algorithms.louvain import (
        guard_community_distribution as _guard_comm,
        louvain_hierarchical as _hier,
    )
    from app.services.graph_algorithms.network import load_skill_cooccurrence as _load_cooc
    from app.core.database import neo4j_driver as _neo4j_driver

    _cfg = _load_cfg()
    with _neo4j_driver.session() as _s:
        _graph, _name_map = _load_cooc(_s, min_weight=_cfg["min_weight"])
    _h = _hier(_graph, resolution=_cfg["resolution"])
    try:
        _g = _guard_comm(_h["levels"], _h.get("best_level"))
        print(f"[wait_etl_then_tune] 新参数全量图验证通过: {_g}", flush=True)
    except ValueError as e:
        print(f"[wait_etl_then_tune] 新参数验证失败，回滚旧参数: {e}", flush=True)
        cfg_path.write_text(_cfg_before, encoding="utf-8")
        print("[wait_etl_then_tune] 已回滚 configs/graph_algo.yaml（保留现有 Community 索引）", flush=True)
        return 3

    run(["scripts/sync_communities.py"])
    print("[wait_etl_then_tune] Community 索引已按新参数重建", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
