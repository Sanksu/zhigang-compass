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
from datetime import datetime
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

# 内置演示图（--dry-run 无快照时冒烟用）：4 个稠密社区（各 4 节点，内部权重 3.0）
# + 社区间弱桥边（权重 1.0，min_weight=2.0 时被过滤，社区结构清晰）
_DEMO_GRAPH: dict[str, dict[str, float]] = {}
for _c in "abcd":
    _nodes = [f"{_c}{i}" for i in range(1, 5)]
    for _i, _u in enumerate(_nodes):
        for _v in _nodes[_i + 1:]:
            _DEMO_GRAPH.setdefault(_u, {})[_v] = 3.0
            _DEMO_GRAPH.setdefault(_v, {})[_u] = 3.0
for _u, _v in [("a4", "b1"), ("b4", "c1"), ("c4", "d1")]:
    _DEMO_GRAPH.setdefault(_u, {})[_v] = 1.0
    _DEMO_GRAPH.setdefault(_v, {})[_u] = 1.0
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


def _is_degenerate(partition: dict[str, int]) -> bool:
    """退化解判定：簇数 ≤ 2 或最大簇占比 > 0.5（单簇/近单簇无聚类价值）。

    分辨率化模块度在 γ<1 时单簇 Q=1−γ 虚高、同质性恒 1.0，会主导
    objective 使 Optuna 收敛到"全部合并"的退化最优（2026-08-12 实跑发现）。
    """
    if not partition:
        return True
    sizes = Counter(partition.values())
    if len(sizes) <= 2:
        return True
    return max(sizes.values()) / len(partition) > 0.5


def score_partition(g: dict[str, dict[str, float]], partition: dict[str, int]) -> float:
    """聚类质量综合分（objective 口径）：0.5·Q + 0.3·同质性 + 0.2·(1−过小簇)。

    Q 统一用标准模块度（γ=1.0）评分——γ 只负责生成划分，不参与评分，
    避免 γ<1 时分辨率化 Q 虚高引导退化解；退化解直接罚 0。
    """
    from app.services.graph_algorithms.louvain import _modularity, homogeneity

    if _is_degenerate(partition):
        return 0.0
    q = _modularity(g, partition, 1.0)
    h = homogeneity(g, partition)
    s = small_cluster_ratio(partition)
    return W_Q * q + W_HOM * h + W_SMALL * (1.0 - s)


def _metrics(g: dict[str, dict[str, float]], partition: dict[str, int]) -> dict:
    """划分质量指标（标准 Q / 同质性 / 过小簇占比 / 簇数 / 退化标记）。"""
    from app.services.graph_algorithms.louvain import _modularity, homogeneity

    if not partition:
        return {"modularity": 0.0, "homogeneity": 0.0, "small_ratio": 0.0, "cluster_count": 0, "degenerate": True}
    return {
        "modularity": _modularity(g, partition, 1.0),
        "homogeneity": homogeneity(g, partition),
        "small_ratio": small_cluster_ratio(partition),
        "cluster_count": len(set(partition.values())),
        "degenerate": _is_degenerate(partition),
    }


def evaluate(graph: dict[str, dict[str, float]], resolution: float, min_weight: float) -> dict:
    """给定 (resolution, min_weight) 的 Louvain 聚类质量指标（标准 Q 口径）。"""
    from app.services.graph_algorithms.louvain import louvain

    g = filter_graph(graph, min_weight)
    return _metrics(g, louvain(g, resolution=resolution))


def compare_algorithms(graph: dict[str, dict[str, float]], resolution: float, min_weight: float) -> dict:
    """阶段二验收对比：Leiden vs Louvain（同参数，标准 Q/同质性口径）。

    验收标准（图算法优化方案 §1.2）：Leiden 的 Q ≥ Louvain + 0.01 且
    同质性 ≥ Louvain + 0.05 才允许切换 algorithm=leiden。
    """
    from app.services.graph_algorithms.leiden import leiden
    from app.services.graph_algorithms.louvain import louvain

    g = filter_graph(graph, min_weight)
    return {
        "louvain": _metrics(g, louvain(g, resolution=resolution)),
        "leiden": _metrics(g, leiden(g, resolution=resolution)),
    }


def _cluster(graph: dict[str, dict[str, float]], resolution: float, algorithm: str = "louvain") -> dict[str, int]:
    """按算法名聚类（louvain 纯 Python / leiden igraph+leidenalg，阶段二双实现并存）。"""
    if algorithm == "leiden":
        from app.services.graph_algorithms.leiden import leiden

        return leiden(graph, resolution=resolution)
    from app.services.graph_algorithms.louvain import louvain

    return louvain(graph, resolution=resolution)


def objective(graph: dict[str, dict[str, float]], trial, algorithm: str = "louvain") -> float:
    """Optuna 目标：0.5·Q + 0.3·同质性 + 0.2·(1−过小簇占比)，最大化。

    Q 用标准模块度（γ 不参与评分）；退化解（簇数 ≤ 2 或最大簇占比 > 0.5）
    直接罚 0，防止收敛到"全部合并"的退化最优。
    """
    resolution = trial.suggest_float("resolution", GAMMA_MIN, GAMMA_MAX)
    min_weight = trial.suggest_float("min_weight", MIN_WEIGHT_MIN, MIN_WEIGHT_MAX)
    g = filter_graph(graph, min_weight)
    partition = _cluster(g, resolution, algorithm)
    return score_partition(g, partition)


def tune(graph: dict[str, dict[str, float]], n_trials: int, seed: int = 42, n_runs: int = 10, algorithm: str = "louvain") -> dict:
    """Optuna 扫描 + 最优参数稳定性验证（10 次独立运行报告均值/标准差）。

    Args:
        algorithm: louvain | leiden（阶段二：Leiden 需在自身参数空间重新调优，
            不能沿用 Louvain 最优参数——2026-08-12 验收实测两者最优参数不互通）
    """
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(lambda t: objective(graph, t, algorithm), n_trials=n_trials)
    best = study.best_params

    # 稳定性验证：最优参数独立重跑 n_runs 次（Louvain/Leiden 均确定性算法，
    # 验证口径为最优参数在多轮运行中指标无退化）
    g_best = filter_graph(graph, best["min_weight"])
    scores = [
        score_partition(g_best, _cluster(g_best, best["resolution"], algorithm))
        for _ in range(n_runs)
    ]
    mean = sum(scores) / len(scores)
    std = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
    metrics = _metrics(g_best, _cluster(g_best, best["resolution"], algorithm))
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
        "exported_at": datetime.now().isoformat(timespec="seconds"),
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
    deg = " [退化]" if m.get("degenerate") else ""
    print(
        f"  Q={m['modularity']:.4f} 同质性={m['homogeneity']:.4f} "
        f"过小簇占比={m['small_ratio']:.4f} 簇数={m['cluster_count']}{deg}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="图算法参数 Optuna 调优（阶段一：γ + min_weight；阶段二：Leiden 验收对比）")
    parser.add_argument("--export", type=Path, help="从 Neo4j 导出共现图快照 JSON 到指定路径")
    parser.add_argument("--snapshot", type=Path, help="共现图快照 JSON（固定数据集，避免每轮重查）")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trial 数（默认 50）")
    parser.add_argument("--runs", type=int, default=10, help="最优参数稳定性验证次数（默认 10）")
    parser.add_argument("--apply", action="store_true", help="将最优参数写回 configs/graph_algo.yaml")
    parser.add_argument("--dry-run", action="store_true", help="不跑 Optuna，仅打印当前配置指标")
    parser.add_argument("--compare", action="store_true", help="阶段二验收：Leiden vs Louvain 同参数对比（不调优）")
    parser.add_argument("--algorithm", choices=["louvain", "leiden"], default="louvain", help="调优目标算法（阶段二 Leiden 需自身参数空间调优）")
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

    if args.compare:
        # 阶段二验收：Leiden vs Louvain（当前配置参数，标准 Q/同质性口径）
        comp = compare_algorithms(graph, cfg["resolution"], cfg["min_weight"])
        m_l, m_g = comp["louvain"], comp["leiden"]
        print(f"[Leiden 验收对比] γ={cfg['resolution']:.3f} min_weight={cfg['min_weight']:.3f}")
        print(f"  Louvain: Q={m_l['modularity']:.4f} 同质性={m_l['homogeneity']:.4f} 过小簇={m_l['small_ratio']:.4f} 簇数={m_l['cluster_count']}")
        print(f"  Leiden : Q={m_g['modularity']:.4f} 同质性={m_g['homogeneity']:.4f} 过小簇={m_g['small_ratio']:.4f} 簇数={m_g['cluster_count']}")
        dq = m_g["modularity"] - m_l["modularity"]
        dh = m_g["homogeneity"] - m_l["homogeneity"]
        ok = dq >= 0.01 and dh >= 0.05
        print(f"  ΔQ={dq:+.4f}（≥ +0.01）Δ同质性={dh:+.4f}（≥ +0.05）")
        print(f"  → {'✅ 验收达标，可切换 configs/graph_algo.yaml algorithm=leiden' if ok else '❌ 验收未达标，保持 algorithm=louvain'}")
        return

    m = evaluate(graph, cfg["resolution"], cfg["min_weight"])
    report("当前配置（configs/graph_algo.yaml）", m, cfg)

    if args.dry_run:
        return

    result = tune(graph, n_trials=args.trials, n_runs=args.runs, algorithm=args.algorithm)
    report(f"Optuna 最优（{args.trials} trial, {args.algorithm}）", result["metrics"], result)
    print(f"  objective={result['objective']:.4f} 稳定性 mean={result['stability_mean']:.4f} std={result['stability_std']:.4f}")
    print("  （Louvain/Leiden 均为确定性算法：稳定性验证为多轮运行口径复核，std=0 属预期；扰动鲁棒性可抽 80% 边子集复验）")

    if args.apply:
        apply_config(result["resolution"], result["min_weight"])


if __name__ == "__main__":
    main()
