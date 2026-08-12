"""图算法参数 Optuna 调优（图算法优化方案阶段一）。

在固定共现图快照上搜索 Louvain 分辨率 γ 与共现边权重下限 min_weight，
objective = 0.5·Q + 0.3·同质性 + 0.2·(1−过小簇占比)（方案阶段一验收指标）。

用法：
    uv run python scripts/graph_algo_tune.py --export data/graph_cooccurrence.json
        # 从 Neo4j 导出全量共现图快照（min_weight=1.0 不过滤，固定数据集）
    uv run python scripts/graph_algo_tune.py --snapshot data/graph_cooccurrence.json
        # 在快照上 Optuna 扫描（默认 50 trial）
    uv run python scripts/graph_algo_tune.py --snapshot <path> --trials 100 --apply
        # 指定 trial 数并将最优参数写回 configs/graph_algo.yaml
    uv run python scripts/graph_algo_tune.py --dry-run
        # 不跑 Optuna，仅打印当前配置指标（无快照时用内置演示图冒烟）

产物：最优参数写入 `configs/graph_algo.yaml`（幂等覆盖，可审计）。
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))
_CONFIG_PATH = _BACKEND_DIR / "configs" / "graph_algo.yaml"

from app.core.logging import setup_logging

logger = setup_logging("graph_algo_tune")

# 搜索空间（图算法优化方案阶段一）
GAMMA_MIN, GAMMA_MAX = 0.5, 2.0
MIN_WEIGHT_MIN, MIN_WEIGHT_MAX = 1.0, 3.0
# objective 权重（方案阶段一）
W_Q, W_HOM, W_SMALL = 0.5, 0.3, 0.2
# 过小簇口径（与 postprocess.MIN_CLUSTER_SIZE 对齐）
SMALL_CLUSTER_SIZE = 2

# 内置演示图（--dry-run 无快照时冒烟用，3 个稠密子图 + 1 个桥节点）
_DEMO_GRAPH = {
    "s1": {"s2": 3.0, "s3": 3.0, "s4": 2.0, "s9": 1.0},
    "s2": {"s1": 3.0, "s3": 3.0, "s4": 2.0},
    "s3": {"s1": 3.0, "s2": 3.0, "s4": 2.0},
    "s4": {"s1": 2.0, "s2": 2.0, "s3": 2.0, "s5": 2.0},
    "s5": {"s4": 2.0, "s6": 3.0, "s7": 3.0},
    "s6": {"s5": 3.0, "s7": 3.0},
    "s7": {"s5": 3.0, "s6": 3.0},
    "s8": {"s9": 2.0, "s10": 2.0},
    "s9": {"s1": 1.0, "s8": 2.0, "s10": 2.0},
    "s10": {"s8": 2.0, "s9": 2.0},
}
_DEMO_NAMES = {k: f"技能{k}" for k in _DEMO_GRAPH}


def filter_graph(graph: dict[str, dict[str, float]], min_weight: float) -> dict[str, dict[str, float]]:
    """按权重下限过滤共现图，保持无向对称（双向登记）。

    每条无向边仅处理一次（u < v），过滤后重建对称邻接表，保证
    Louvain/同质性计算口径与 network.load_skill_cooccurrence 一致。
    """
    edges: dict[tuple[str, str], float] = {}
    for u, nbs in graph.items():
        for v, w in nbs.items():
            if u < v:
                edges[(u, v)] = w
    result: dict[str, dict[str, float]] = {}
    for (u, v), w in edges.items():
        if w >= min_weight:
            result.setdefault(u, {})[v] = w
            result.setdefault(v, {})[u] = w
    return result


def small_cluster_ratio(partition: dict[str, int]) -> float:
    """过小簇占比（size ≤ SMALL_CLUSTER_SIZE 的簇数 / 总簇数），空划分返回 0。"""
    if not partition:
        return 0.0
    sizes = Counter(partition.values())
    return sum(1 for s in sizes.values() if s <= SMALL_CLUSTER_SIZE) / len(sizes)


def evaluate(graph: dict[str, dict[str, float]], resolution: float, min_weight: float) -> dict:
    """给定 (resolution, min_weight) 的聚类质量指标（Q / 同质性 / 过小簇占比）。"""
    from app.services.graph_algorithms.louvain import _modularity, homogeneity, louvain

    g = filter_graph(graph, min_weight)
    partition = louvain(g, resolution=resolution)
    if not partition:
        return {"modularity": 0.0, "homogeneity": 0.0, "small_ratio": 0.0, "cluster_count": 0}
    return {
        "modularity": _modularity(g, partition, resolution),
        "homogeneity": homogeneity(g, partition),
        "small_ratio": small_cluster_ratio(partition),
        "cluster_count": len(set(partition.values())),
    }


def objective(graph: dict[str, dict[str, float]], trial) -> float:
    """Optuna 目标：0.5·Q + 0.3·同质性 + 0.2·(1−过小簇占比)，最大化。"""
    resolution = trial.suggest_float("resolution", GAMMA_MIN, GAMMA_MAX)
    min_weight = trial.suggest_float("min_weight", MIN_WEIGHT_MIN, MIN_WEIGHT_MAX)
    m = evaluate(graph, resolution, min_weight)
    return W_Q * m["modularity"] + W_HOM * m["homogeneity"] + W_SMALL * (1.0 - m["small_ratio"])


def tune(graph: dict[str, dict[str, float]], n_trials: int, seed: int = 42, n_runs: int = 10) -> dict:
    """Optuna 扫描 + 最优参数稳定性验证（10 次独立运行报告均值/标准差）。"""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(lambda t: objective(graph, t), n_trials=n_trials)
    best = study.best_params

    # 稳定性验证：最优参数独立重跑 n_runs 次（Louvain 为确定性算法，
    # 验证口径为最优参数在多轮运行中指标无退化）
    scores = [
        W_Q * evaluate(graph, best["resolution"], best["min_weight"])["modularity"]
        + W_HOM * evaluate(graph, best["resolution"], best["min_weight"])["homogeneity"]
        + W_SMALL * (1.0 - evaluate(graph, best["resolution"], best["min_weight"])["small_ratio"])
        for _ in range(n_runs)
    ]
    mean = sum(scores) / len(scores)
    std = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
    metrics = evaluate(graph, best["resolution"], best["min_weight"])
    return {
        "resolution": best["resolution"],
        "min_weight": best["min_weight"],
        "objective": study.best_value,
        "stability_mean": mean,
        "stability_std": std,
        "metrics": metrics,
    }


def export_snapshot(path: Path) -> None:
    """从 Neo4j 导出全量共现图快照（min_weight=1.0 不过滤，供调优固定数据集）。"""
    from app.core.database import neo4j_driver
    from app.services.graph_algorithms.network import load_skill_cooccurrence

    with neo4j_driver.session() as session:
        graph, name_map = load_skill_cooccurrence(session, min_weight=1.0)
    payload = {
        "exported_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "node_count": len(graph),
        "edge_count": sum(len(nbs) for nbs in graph.values()) // 2,
        "graph": graph,
        "name_map": name_map,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"快照已导出: {path}（{payload['node_count']} 节点 / {payload['edge_count']} 边）")


def load_snapshot(path: Path) -> dict[str, dict[str, float]]:
    """加载快照 JSON，返回 {graph, name_map}。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["graph"], payload.get("name_map", {})


def apply_config(resolution: float, min_weight: float) -> None:
    """最优参数写回 configs/graph_algo.yaml（保留 algorithm/min_size 字段）。"""
    import yaml

    existing = {}
    if _CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
            existing = data if isinstance(data, dict) else {}
        except yaml.YAMLError:
            existing = {}
    existing.update({"resolution": resolution, "min_weight": min_weight})
    _CONFIG_PATH.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(f"最优参数已写入 {_CONFIG_PATH.relative_to(_BACKEND_DIR)}: {existing}")


def report(tag: str, m: dict, params: dict) -> None:
    """打印指标报告。"""
    print(f"[{tag}] γ={params['resolution']:.3f} min_weight={params['min_weight']:.3f}")
    print(
        f"  Q={m['modularity']:.4f} 同质性={m['homogeneity']:.4f} "
        f"过小簇占比={m['small_ratio']:.4f} 簇数={m['cluster_count']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="图算法参数 Optuna 调优（阶段一：γ + min_weight）")
    parser.add_argument("--export", type=Path, help="从 Neo4j 导出共现图快照 JSON 到指定路径")
    parser.add_argument("--snapshot", type=Path, help="共现图快照 JSON（固定数据集，避免每轮重查）")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trial 数（默认 50）")
    parser.add_argument("--runs", type=int, default=10, help="最优参数稳定性验证次数（默认 10）")
    parser.add_argument("--apply", action="store_true", help="将最优参数写回 configs/graph_algo.yaml")
    parser.add_argument("--dry-run", action="store_true", help="不跑 Optuna，仅打印当前配置指标")
    args = parser.parse_args()

    if args.export:
        export_snapshot(args.export)
        return

    # 图来源：快照文件 > 内置演示图（dry-run 冒烟）
    if args.snapshot:
        graph, _ = load_snapshot(args.snapshot)
        logger.info(f"快照: {args.snapshot}（{len(graph)} 节点）")
    else:
        graph = _DEMO_GRAPH
        logger.warning("未指定 --snapshot，使用内置演示图（仅冒烟，非真实数据）")

    from app.services.graph_algorithms.config import load_graph_algo_config

    cfg = load_graph_algo_config()
    m = evaluate(graph, cfg["resolution"], cfg["min_weight"])
    report("当前配置（configs/graph_algo.yaml）", m, cfg)

    if args.dry_run:
        return

    result = tune(graph, n_trials=args.trials, n_runs=args.runs)
    report(f"Optuna 最优（{args.trials} trial）", result["metrics"], result)
    print(f"  objective={result['objective']:.4f} 稳定性 mean={result['stability_mean']:.4f} std={result['stability_std']:.4f}")

    if args.apply:
        apply_config(result["resolution"], result["min_weight"])


if __name__ == "__main__":
    main()
